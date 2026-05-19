"""
repairer.py - INSVRepairer class for main repair logic for broken .insv files
"""

import os
import struct
from typing import Optional, BinaryIO
from mdat_extractor import MdatExtractor
from reference_analyzer import ReferenceAnalyzer
from atom_parser import AtomParser
from mdat_scanner import MdatScanner
from moov_builder import MoovBuilder

class INSVRepairer:
    """Main repair logic for broken .insv files."""

    def __init__(self, broken_path: str, output_path: str = None):
        self.broken_path = broken_path
        self.output_path = output_path or self._default_output_path()

    def _default_output_path(self) -> str:
        base, ext = os.path.splitext(self.broken_path)
        return f"{base}_repaired{ext}"

    def repair_with_reference(self, reference_path: str):
        print(f"\n{'='*60}")
        print(f"INSV Repair (Reference Mode)")
        print(f"{'='*60}")
        print(f"Broken file:    {self.broken_path}")
        print(f"Reference file: {reference_path}")
        print(f"Output file:    {self.output_path}")
        print()
        print("[1/4] Analyzing reference file...")
        ref = ReferenceAnalyzer(reference_path)
        ref_info = ref.analyze()
        ref.close()
        print(f"  Reference has {len(ref_info['tracks'])} tracks:")
        for i, t in enumerate(ref_info['tracks']):
            print(f"    Track {i+1}: {t['codec']} "
                  f"({t['handler_type'].decode('ascii', errors='replace')}) "
                  f"{t['width']}x{t['height']} "
                  f"timescale={t['timescale']} "
                  f"samples_per_chunk={t['samples_per_chunk']}")
        print("\n[2/4] Analyzing broken file...")
        with open(self.broken_path, 'rb') as bf:
            bf_size = bf.seek(0, 2)
            parser = AtomParser(bf, bf_size)
            atoms = parser.parse_top_level()
            mdat = parser.find_atom(atoms, b'mdat')
            moov = parser.find_atom(atoms, b'moov')
            ftyp = parser.find_atom(atoms, b'ftyp')
            extracted_mdat_values = None
            if not mdat:
                print("  No mdat atom found - treating entire file as media data")
                mdat_offset = 0
                mdat_size = bf_size
                mdat_header_size = 0
                has_ftyp = False
            else:
                mdat_offset = mdat.offset
                mdat_size = mdat.size
                mdat_header_size = mdat.header_size
                has_ftyp = ftyp is not None
                extracted_mdat_values = MdatExtractor().extract(self.broken_path)
                if extracted_mdat_values:
                    print(f"  Extracted mdat values: {extracted_mdat_values}")
                    # Use clean mdat size if available
                    if extracted_mdat_values.get('clean_mdat_size'):
                        mdat_size = extracted_mdat_values['clean_mdat_size']
                        mdat_offset = extracted_mdat_values['mdat_data_start'] - mdat_header_size
            print(f"  mdat at offset {mdat_offset:#x}, size {mdat_size:,} bytes")
            if moov:
                print(f"  WARNING: File already has a moov atom at {moov.offset:#x}")
                print(f"  Will rebuild moov anyway (existing one may be corrupted)")
            print("\n[3/4] Scanning media data...")
            scanner = MdatScanner(bf, mdat_offset, mdat_size, mdat_header_size)
            scan_result = scanner.scan_chunks_with_reference(ref_info)
            if not scan_result or all(len(scan_result[i]['sample_sizes']) == 0
                                     for i in scan_result):
                print("\n  ERROR: No valid media data found in mdat")
                return False
            print("\n[4/4] Building repaired file...")
            self._build_repaired_file(
                bf, ref_info, scan_result,
                mdat_offset, mdat_size,
                has_ftyp, ftyp,
                mdat_header_size=mdat_header_size,
                extracted_mdat_values=extracted_mdat_values
            )
        print(f"\nRepair complete: {self.output_path}")
        return True

    def repair_standalone(self):
        print(f"\n{'='*60}")
        print(f"INSV Repair (Scan Mode - No Reference)")
        print(f"{'='*60}")
        print(f"Broken file: {self.broken_path}")
        print(f"Output file: {self.output_path}")
        print()
        with open(self.broken_path, 'rb') as bf:
            bf_size = bf.seek(0, 2)
            parser = AtomParser(bf, bf_size)
            atoms = parser.parse_top_level()
            mdat = parser.find_atom(atoms, b'mdat')
            ftyp = parser.find_atom(atoms, b'ftyp')
            if not mdat:
                if ftyp:
                    mdat_offset = ftyp.offset + ftyp.size
                    mdat_size = bf_size - mdat_offset
                    mdat_header_size = 0
                else:
                    mdat_offset = 0
                    mdat_size = bf_size
                    mdat_header_size = 0
                print(f"  No mdat found, treating offset {mdat_offset:#x} to EOF as media data")
            else:
                mdat_offset = mdat.offset
                mdat_size = min(mdat.size, bf_size - mdat.offset)
                mdat_header_size = mdat.header_size
            print(f"  Media data: {mdat_size:,} bytes at offset {mdat_offset:#x}")
            print("\nScanning media data for frames...")
            scanner = MdatScanner(bf, mdat_offset, mdat_size, mdat_header_size)
            scan_result = scanner.scan_mdat_standalone()
            if not scan_result:
                print("\nERROR: Could not detect any valid frames")
                return False
            vps, sps, pps = self._extract_hevc_params_from_mdat(bf, scan_result)
            if not all([vps, sps, pps]):
                print("WARNING: Could not extract HEVC parameters from media data")
                print("  Using default parameters (may not work with all players)")
                vps = vps or bytes([0x40, 0x01, 0x0C, 0x01, 0xFF, 0xFF, 0x01, 0x60,
                                    0x00, 0x00, 0x03, 0x00, 0x90, 0x00, 0x00, 0x03,
                                    0x00, 0x00, 0x03, 0x00, 0x99, 0x95, 0x98, 0x09])
                sps = sps or bytes([0x42, 0x01, 0x01, 0x01, 0x60, 0x00, 0x00, 0x03,
                                    0x00, 0x90, 0x00, 0x00, 0x03, 0x00, 0x00, 0x03,
                                    0x00, 0x99, 0xA0, 0x01, 0xE0, 0x20])
                pps = pps or bytes([0x44, 0x01, 0xC0, 0xF7, 0xC0, 0xCC, 0x90])
            ref_info = self._build_ref_info_from_scan(scan_result, vps, sps, pps)
            print("\nBuilding repaired file...")
            has_ftyp = ftyp is not None
            self._build_repaired_file(bf, ref_info, scan_result,
                                      mdat_offset, mdat_size,
                                      has_ftyp, ftyp,
                                      mdat_header_size=mdat_header_size)
        print(f"\nRepair complete: {self.output_path}")
        return True

    def _extract_hevc_params_from_mdat(self, f: BinaryIO, scan_result: dict) -> tuple:
        vps = sps = pps = None
        for track_idx in (0, 1):
            if track_idx not in scan_result:
                continue
            track = scan_result[track_idx]
            if not track['chunk_offsets']:
                continue
            for chunk_off in track['chunk_offsets'][:10]:
                f.seek(chunk_off)
                pos = chunk_off
                for _ in range(20):
                    f.seek(pos)
                    lb = f.read(4)
                    if len(lb) < 4:
                        break
                    nal_len = struct.unpack('>I', lb)[0]
                    if nal_len < 2 or nal_len > 20 * 1024 * 1024:
                        break
                    nal_data = f.read(nal_len)
                    if len(nal_data) < 2:
                        break
                    nal_type = nal_data[0] >> 1 & 0x3F
                    if nal_type == 32:
                        vps = nal_data
                    elif nal_type == 33:
                        sps = nal_data
                    elif nal_type == 34:
                        pps = nal_data
                    pos += 4 + nal_len
                    if vps and sps and pps:
                        return vps, sps, pps
        return vps, sps, pps

    def _build_ref_info_from_scan(self, scan_result: dict,
                                   vps: bytes, sps: bytes, pps: bytes) -> dict:
        tracks = []
        for i in range(3):
            if i not in scan_result:
                continue
            if i < 2:
                tracks.append({
                    'track_id': i + 1,
                    'handler_type': b'vide',
                    'handler_name': 'INS.HVC',
                    'timescale': 30000,
                    'codec': 'hvc1',
                    'width': 1920,
                    'height': 1920,
                    'sample_delta': 1001,
                    'samples_per_chunk': scan_result[i].get('samples_per_chunk', 1),
                    'vps': vps, 'sps': sps, 'pps': pps,
                })
            else:
                tracks.append({
                    'track_id': i + 1,
                    'handler_type': b'soun',
                    'handler_name': 'INS.AAC',
                    'timescale': 48000,
                    'codec': 'mp4a',
                    'width': 0, 'height': 0,
                    'sample_delta': 1024,
                    'samples_per_chunk': scan_result[i].get('samples_per_chunk', 1),
                })
        return {
            'tracks': tracks,
            'mvhd_timescale': 600,
            'udta': None,
            'ftyp': None,
        }

    def _build_repaired_file(self, broken_f: BinaryIO, ref_info: dict,
                              scan_result: dict, mdat_offset: int, mdat_size: int,
                              has_ftyp: bool, ftyp_atom: Optional[object] = None,
                              mdat_header_size: int = 8,
                              extracted_mdat_values: Optional[dict] = None):
        builder = MoovBuilder(timescale=ref_info.get('mvhd_timescale', 600))
        tracks = ref_info['tracks']
        if has_ftyp and ftyp_atom:
            broken_f.seek(ftyp_atom.offset)
            ftyp_data = broken_f.read(ftyp_atom.size)
        else:
            ftyp_data = builder.build_ftyp()
        ftyp_size = len(ftyp_data)
        # Use clean mdat size if available
        if extracted_mdat_values and extracted_mdat_values.get('clean_mdat_size'):
            mdat_payload_size = extracted_mdat_values['clean_mdat_size']
            old_payload_start = extracted_mdat_values['mdat_data_start']
        else:
            mdat_payload_size = mdat_size - mdat_header_size
            old_payload_start = mdat_offset + mdat_header_size
        WIDE_BOX = struct.pack('>I', 8) + b'wide'
        WIDE_BOX_SIZE = 8
        if mdat_payload_size > 0xFFFFFFFF - 8:
            new_mdat_header_size = 16
            new_mdat_header = (struct.pack('>I', 1) + b'mdat' +
                               struct.pack('>Q', mdat_payload_size + 16))
        else:
            new_mdat_header_size = 8
            new_mdat_header = struct.pack('>I', mdat_payload_size + 8) + b'mdat'
        new_payload_start = ftyp_size + WIDE_BOX_SIZE + new_mdat_header_size
        offset_shift = new_payload_start - old_payload_start
        trak_atoms = []
        max_duration_s = 0
        for i, track in enumerate(tracks):
            if i not in scan_result or not scan_result[i]['sample_sizes']:
                continue
            sr = scan_result[i]
            is_video = track.get('handler_type') == b'vide'
            timescale = sr.get('timescale', track.get('timescale', 60000))
            sample_delta = sr.get('sample_delta', track.get('sample_delta', 1001))
            n_samples = len(sr['sample_sizes'])
            duration_ts = n_samples * sample_delta
            duration_s = duration_ts / timescale
            max_duration_s = max(max_duration_s, duration_s)
            if is_video:
                vps = track.get('vps', b'')
                sps = track.get('sps', b'')
                pps = track.get('pps', b'')
                width = track.get('width', 1920)
                height = track.get('height', 1920)
                if track.get('stsd_raw'):
                    stsd = track['stsd_raw']
                else:
                    stsd = builder.build_stsd_hevc(width, height, vps, sps, pps)
            else:
                if track.get('stsd_raw'):
                    stsd = track['stsd_raw']
                else:
                    stsd = builder.build_stsd_aac(
                        sample_rate=48000,
                        channels=2)
                width = 0
                height = 0
            stts = builder.build_stts([(n_samples, sample_delta)])
            ctts = None
            if is_video:
                ctts = builder.build_ctts([(n_samples, 0)])
            stss = None
            if is_video and sr['sync_samples']:
                stss = builder.build_stss(sr['sync_samples'])
            stsz = builder.build_stsz(sr['sample_sizes'])
            stsc_entries = self._build_stsc_entries(sr)
            stsc = builder.build_stsc(stsc_entries)
            adjusted_offsets = [off + offset_shift for off in sr['chunk_offsets']]
            co64 = builder.build_co64(adjusted_offsets)
            stbl = builder.build_stbl(stsd, stts, stsc, stsz, co64, stss, ctts)
            if is_video:
                media_header = builder.build_vmhd()
            else:
                media_header = builder.build_smhd()
            dinf = builder.build_dinf()
            minf = builder.build_minf(media_header, dinf, stbl)
            handler_type = track.get('handler_type', b'vide')
            handler_name = track.get('handler_name') or 'VideoHandler'
            hdlr = builder.build_hdlr(handler_type, handler_name)
            mdhd = builder.build_mdhd(timescale, duration_ts)
            mdia = builder.build_mdia(mdhd, hdlr, minf)
            mvhd_ts = ref_info.get('mvhd_timescale', 600)
            tkhd_duration = int(duration_s * mvhd_ts)
            tkhd = builder.build_tkhd(
                track_id=track.get('track_id', i + 1),
                duration_ts=tkhd_duration,
                width=width, height=height,
                is_audio=not is_video)
            edts = None
            if is_video:
                elst = builder.build_elst([
                    (tkhd_duration, 0, 1, 0)
                ])
                edts = builder.build_edts(elst)
            trak_atom = builder.build_trak(tkhd, mdia, edts)
            trak_atoms.append(trak_atom)
        mvhd_ts = ref_info.get('mvhd_timescale', 600)
        mvhd_duration = int(max_duration_s * mvhd_ts)
        mvhd = builder.build_mvhd(mvhd_duration, next_track_id=len(trak_atoms) + 1)
        udta = ref_info.get('udta')
        moov = builder.build_moov(mvhd, udta, trak_atoms)
        print(f"\n  Writing repaired file...")
        print(f"    ftyp:  {ftyp_size:,} bytes")
        print(f"    wide:  {WIDE_BOX_SIZE} bytes")
        print(f"    mdat:  {mdat_payload_size + new_mdat_header_size:,} bytes "
              f"({mdat_payload_size:,} payload)")
        print(f"    moov:  {len(moov):,} bytes")
        buf_size = 64 * 1024 * 1024
        with open(self.output_path, 'wb') as out:
            out.write(ftyp_data)
            out.write(WIDE_BOX)
            out.write(new_mdat_header)
            broken_f.seek(old_payload_start)
            remaining = mdat_payload_size
            while remaining > 0:
                chunk = broken_f.read(min(buf_size, remaining))
                if not chunk:
                    break
                out.write(chunk)
                remaining -= len(chunk)
            out.write(moov)
            # Append Insta360 trailer (nail, AMBA, trailer, magic phrase)
            if extracted_mdat_values:
                for key in ["nail_data", "amba_data", "trailer_data"]:
                    data = extracted_mdat_values.get(key)
                    if data:
                        out.write(data)
                magic = extracted_mdat_values.get("magic_phrase")
                if magic:
                    out.write(magic.encode("ascii"))
        total_size = os.path.getsize(self.output_path)
        print(f"    Total: {total_size:,} bytes ({total_size / (1024*1024*1024):.2f} GB)")

    def _build_stsc_entries(self, sr: dict) -> list:
        chunk_offsets = sr['chunk_offsets']
        sample_sizes = sr['sample_sizes']
        spc = sr.get('samples_per_chunk', 1)
        if not chunk_offsets:
            return [(1, 1, 1)]
        n_samples = len(sample_sizes)
        n_chunks = len(chunk_offsets)
        if n_chunks > 0 and n_samples == n_chunks * spc:
            return [(1, spc, 1)]
        if n_chunks == n_samples:
            return [(1, 1, 1)]
        if n_chunks > 0:
            avg_spc = max(1, n_samples // n_chunks)
            return [(1, avg_spc, 1)]
        return [(1, 1, 1)]