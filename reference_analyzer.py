"""
reference_analyzer.py - ReferenceAnalyzer class for extracting codec parameters and structure from a reference .insv file
"""

import struct
from typing import Optional
from constants import Atom
from atom_parser import AtomParser
from constants import HEVC_NAL_VPS, HEVC_NAL_SPS, HEVC_NAL_PPS

class ReferenceAnalyzer:
    """Extract codec parameters and structure from a reference .insv file."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.f = open(filepath, 'rb')
        self.file_size = self.f.seek(0, 2)
        self.parser = AtomParser(self.f, self.file_size)
        self.tracks = []

    def close(self):
        self.f.close()

    def analyze(self) -> dict:
        top_atoms = self.parser.parse_top_level()
        info = {
            'ftyp': None,
            'moov_offset': None,
            'mdat_offset': None,
            'mdat_size': None,
            'tracks': [],
            'mvhd_timescale': None,
            'udta': None,
        }
        for atom in top_atoms:
            if atom.type == b'ftyp':
                info['ftyp'] = self.parser.extract_atom_raw(atom)
            elif atom.type == b'mdat':
                info['mdat_offset'] = atom.offset
                info['mdat_size'] = atom.size
            elif atom.type == b'moov':
                info['moov_offset'] = atom.offset
                self._parse_moov(atom, info)
        return info

    def _parse_moov(self, moov: Atom, info: dict):
        children = self.parser.parse_children(moov)
        for child in children:
            if child.type == b'mvhd':
                data = self.parser.read_atom_data(child)
                version = data[0]
                if version == 0:
                    info['mvhd_timescale'] = struct.unpack('>I', data[12:16])[0]
                else:
                    info['mvhd_timescale'] = struct.unpack('>I', data[20:24])[0]
            elif child.type == b'trak':
                track_info = self._parse_trak(child)
                if track_info:
                    info['tracks'].append(track_info)
            elif child.type == b'udta':
                info['udta'] = self.parser.extract_atom_raw(child)

    def _parse_trak(self, trak: Atom) -> Optional[dict]:
        children = self.parser.parse_children(trak)
        track = {
            'track_id': None,
            'handler_type': None,
            'handler_name': '',
            'timescale': None,
            'codec': None,
            'width': 0,
            'height': 0,
            'sample_count': 0,
            'stsd_raw': None,
            'vps': None, 'sps': None, 'pps': None,
            'sample_delta': None,
            'samples_per_chunk': None,
            'chunk_count': 0,
        }
        for child in children:
            if child.type == b'tkhd':
                data = self.parser.read_atom_data(child)
                version = data[0]
                if version == 0:
                    track['track_id'] = struct.unpack('>I', data[12:16])[0]
                    track['width']    = struct.unpack('>I', data[76:80])[0] >> 16
                    track['height']   = struct.unpack('>I', data[80:84])[0] >> 16
                else:
                    track['track_id'] = struct.unpack('>I', data[20:24])[0]
                    track['width']    = struct.unpack('>I', data[88:92])[0] >> 16
                    track['height']   = struct.unpack('>I', data[92:96])[0] >> 16
            elif child.type == b'mdia':
                self._parse_mdia(child, track)
        return track

    def _parse_mdia(self, mdia: Atom, track: dict):
        children = self.parser.parse_children(mdia)
        for child in children:
            if child.type == b'mdhd':
                data = self.parser.read_atom_data(child)
                version = data[0]
                if version == 0:
                    track['timescale'] = struct.unpack('>I', data[12:16])[0]
                else:
                    track['timescale'] = struct.unpack('>I', data[20:24])[0]
            elif child.type == b'hdlr':
                data = self.parser.read_atom_data(child)
                track['handler_type'] = data[8:12]
                track['handler_name'] = data[24:].decode('ascii', errors='replace').rstrip('\x00')
            elif child.type == b'minf':
                self._parse_minf(child, track)

    def _parse_minf(self, minf: Atom, track: dict):
        children = self.parser.parse_children(minf)
        for child in children:
            if child.type == b'stbl':
                self._parse_stbl(child, track)

    def _parse_stbl(self, stbl: Atom, track: dict):
        children = self.parser.parse_children(stbl)
        for child in children:
            if child.type == b'stsd':
                data = self.parser.read_atom_data(child)
                track['stsd_raw'] = self.parser.extract_atom_raw(child)
                entry_type = data[12:16]
                track['codec'] = entry_type.decode('ascii', errors='replace')
                if entry_type == b'hvc1' or entry_type == b'hev1':
                    self._extract_hevc_params(data[8:], track)
            elif child.type == b'stts':
                data = self.parser.read_atom_data(child)
                entry_count = struct.unpack('>I', data[4:8])[0]
                if entry_count > 0:
                    count, delta = struct.unpack('>II', data[8:16])
                    track['sample_delta'] = delta
            elif child.type == b'stsz':
                data = self.parser.read_atom_data(child)
                track['sample_count'] = struct.unpack('>I', data[8:12])[0]
            elif child.type == b'stsc':
                data = self.parser.read_atom_data(child)
                entry_count = struct.unpack('>I', data[4:8])[0]
                if entry_count > 0:
                    _, spc, _ = struct.unpack('>III', data[8:20])
                    track['samples_per_chunk'] = spc
            elif child.type == b'co64':
                data = self.parser.read_atom_data(child)
                track['chunk_count'] = struct.unpack('>I', data[4:8])[0]
            elif child.type == b'stco':
                data = self.parser.read_atom_data(child)
                track['chunk_count'] = struct.unpack('>I', data[4:8])[0]

    def _extract_hevc_params(self, entry_data: bytes, track: dict):
        offset = 4 + 6 + 2 + 2 + 2 + 12 + 2 + 2 + 4 + 4 + 4 + 2 + 32 + 2 + 2
        entry_size = struct.unpack('>I', entry_data[:4])[0]
        while offset < entry_size - 8:
            box_size = struct.unpack('>I', entry_data[offset:offset + 4])[0]
            box_type = entry_data[offset + 4:offset + 8]
            if box_size < 8:
                break
            if box_type == b'hvcC':
                hvcc_data = entry_data[offset + 8:offset + box_size]
                self._parse_hvcc(hvcc_data, track)
                break
            offset += box_size

    @staticmethod
    def _parse_hvcc(data: bytes, track: dict):
        if len(data) < 23:
            return
        num_arrays = data[22]
        offset = 23
        for _ in range(num_arrays):
            if offset + 3 > len(data):
                break
            nal_type = data[offset] & 0x3F
            num_nalus = struct.unpack('>H', data[offset + 1:offset + 3])[0]
            offset += 3
            for _ in range(num_nalus):
                if offset + 2 > len(data):
                    break
                nal_length = struct.unpack('>H', data[offset:offset + 2])[0]
                offset += 2
                if offset + nal_length > len(data):
                    break
                nal_data = data[offset:offset + nal_length]
                if nal_type == HEVC_NAL_VPS:
                    track['vps'] = nal_data
                elif nal_type == HEVC_NAL_SPS:
                    track['sps'] = nal_data
                elif nal_type == HEVC_NAL_PPS:
                    track['pps'] = nal_data
                offset += nal_length