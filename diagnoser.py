"""
diagnoser.py - INSVDiagnoser class for diagnosing .insv file issues
"""

import struct
from typing import Optional
from constants import Atom
from atom_parser import AtomParser
from hevc_scanner import HEVCScanner, HEVC_IDR_TYPES
from constants import HEVC_NAL_VPS, HEVC_NAL_SPS, HEVC_NAL_PPS

class INSVDiagnoser:
    """Diagnose issues with an .insv file."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.f = open(filepath, 'rb')
        self.file_size = self.f.seek(0, 2)
        self.parser = AtomParser(self.f, self.file_size)

    def close(self):
        self.f.close()

    def diagnose(self) -> dict:
        findings = {
            'file_size': self.file_size,
            'has_ftyp': False,
            'has_mdat': False,
            'has_moov': False,
            'mdat_offset': None,
            'mdat_size': None,
            'moov_offset': None,
            'moov_size': None,
            'issues': [],
            'tracks': [],
            'repairable': False,
            'repair_strategy': None,
        }
        print(f"\n{'='*60}")
        print(f"INSV File Diagnosis")
        print(f"{'='*60}")
        print(f"File: {self.filepath}")
        print(f"Size: {self.file_size:,} bytes ({self.file_size / (1024*1024*1024):.2f} GB)")
        print()
        atoms = self.parser.parse_top_level()
        print("Top-level atoms:")
        for atom in atoms:
            type_str = atom.type.decode('ascii', errors='replace')
            print(f"  {atom.offset:#014x}  {type_str:6s}  {atom.size:>14,} bytes")
        print()
        for atom in atoms:
            if atom.type == b'ftyp':
                findings['has_ftyp'] = True
                self.f.seek(atom.offset + 8)
                brand = self.f.read(4)
                print(f"  ftyp brand: {brand.decode('ascii', errors='replace')}")
            elif atom.type == b'mdat':
                findings['has_mdat'] = True
                findings['mdat_offset'] = atom.offset
                findings['mdat_size'] = atom.size
                findings['mdat_header_size'] = atom.header_size
            elif atom.type == b'moov':
                findings['has_moov'] = True
                findings['moov_offset'] = atom.offset
                findings['moov_size'] = atom.size
        if not findings['has_ftyp']:
            findings['issues'].append("CRITICAL: Missing ftyp atom (file type header)")
        if not findings['has_mdat']:
            findings['issues'].append("CRITICAL: Missing mdat atom (no media data)")
        else:
            mdat_end = findings['mdat_offset'] + findings['mdat_size']
            if mdat_end > self.file_size:
                findings['issues'].append(
                    f"WARNING: mdat declares size {findings['mdat_size']:,} bytes "
                    f"but file is only {self.file_size:,} bytes "
                    f"(truncated by {mdat_end - self.file_size:,} bytes)")
        if not findings['has_moov']:
            findings['issues'].append("CRITICAL: Missing moov atom (no track/sample index)")
            findings['repairable'] = True
            findings['repair_strategy'] = 'rebuild_moov'
            if findings['has_mdat']:
                self._probe_mdat(findings)
        else:
            moov_atom = self.parser.find_atom(atoms, b'moov')
            if moov_atom:
                self._check_moov(moov_atom, findings)
        print("\nDiagnosis:")
        if findings['issues']:
            for issue in findings['issues']:
                print(f"  * {issue}")
        else:
            print("  No issues detected - file appears intact.")
        if findings['repairable']:
            print(f"\n  Repair strategy: {findings['repair_strategy']}")
            if findings['repair_strategy'] == 'rebuild_moov':
                print("  Use --reference <good_file.insv> for best results,")
                print("  or --scan to rebuild without a reference (slower).")
        print()
        return findings

    def _probe_mdat(self, findings: dict):
        mdat_header_size = findings.get('mdat_header_size', 8)
        mdat_data_start = findings['mdat_offset'] + mdat_header_size
        self.f.seek(mdat_data_start)
        header = self.f.read(64)
        if len(header) < 8:
            return
        nal_length = struct.unpack('>I', header[:4])[0]
        if 1 <= nal_length <= 10 * 1024 * 1024:
            nal_type = HEVCScanner.get_nal_type(header[4])
            findings['detected_codec'] = f'HEVC (NAL type {nal_type})'
            if nal_type in (HEVC_NAL_VPS, HEVC_NAL_SPS, HEVC_NAL_PPS):
                findings['issues'].append(
                    f"INFO: mdat starts with HEVC parameter set (NAL {nal_type}) - "
                    "likely a keyframe at start, good for recovery")
            elif nal_type in HEVC_IDR_TYPES:
                findings['issues'].append(
                    "INFO: mdat starts with HEVC IDR frame - good for recovery")

    def _check_moov(self, moov: Atom, findings: dict):
        try:
            children = self.parser.parse_children(moov)
            track_count = sum(1 for c in children if c.type == b'trak')
            findings['issues'].append(f"INFO: moov contains {track_count} tracks")
            for child in children:
                if child.type == b'trak':
                    self._check_track(child, findings)
        except Exception as e:
            findings['issues'].append(f"ERROR: Failed to parse moov: {e}")
            findings['repairable'] = True
            findings['repair_strategy'] = 'rebuild_moov'

    def _check_track(self, trak: Atom, findings: dict):
        try:
            children = self.parser.parse_children(trak)
            tkhd = self.parser.find_atom(children, b'tkhd')
            if tkhd:
                data = self.parser.read_atom_data(tkhd)
                track_id = struct.unpack('>I', data[12:16])[0]
                findings['tracks'].append({'track_id': track_id, 'ok': True})
        except Exception as e:
            findings['issues'].append(f"WARNING: Corrupted track: {e}")