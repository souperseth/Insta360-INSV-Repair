"""
moov_builder.py - MoovBuilder class for constructing moov atom and sub-atoms
"""

import struct
from typing import Optional, List
from constants import (
    X4_VIDEO_WIDTH, X4_VIDEO_HEIGHT, X4_AUDIO_SAMPLE_RATE, X4_AUDIO_CHANNELS
)

class MoovBuilder:
    """Build a moov atom for a repaired file."""

    def __init__(self, timescale: int = 600):
        self.timescale = timescale

    @staticmethod
    def build_ftyp() -> bytes:
        major_brand = b'avc1'
        minor_version = struct.pack('>I', 0x20140200)
        compatible = b'avc1' + b'isom'
        data = major_brand + minor_version + compatible
        return struct.pack('>I', len(data) + 8) + b'ftyp' + data

    def build_mvhd(self, duration_ts: int, next_track_id: int = 4) -> bytes:
        data = bytearray()
        data += struct.pack('>I', 0)
        data += struct.pack('>I', 0)
        data += struct.pack('>I', 0)
        data += struct.pack('>I', self.timescale)
        data += struct.pack('>I', duration_ts)
        data += struct.pack('>I', 0x00010000)
        data += struct.pack('>H', 0x0100)
        data += b'\x00' * 10
        data += struct.pack('>9I',
            0x00010000, 0, 0,
            0, 0x00010000, 0,
            0, 0, 0x40000000)
        data += b'\x00' * 24
        data += struct.pack('>I', next_track_id)
        return struct.pack('>I', len(data) + 8) + b'mvhd' + bytes(data)

    def build_tkhd(self, track_id: int, duration_ts: int,
                   width: int = 0, height: int = 0, is_audio: bool = False) -> bytes:
        data = bytearray()
        flags = 0x000003
        data += struct.pack('>I', flags)
        data += struct.pack('>I', 0)
        data += struct.pack('>I', 0)
        data += struct.pack('>I', track_id)
        data += struct.pack('>I', 0)
        data += struct.pack('>I', duration_ts)
        data += b'\x00' * 8
        data += struct.pack('>H', 0)
        data += struct.pack('>H', 0)
        data += struct.pack('>H', 0x0100 if is_audio else 0)
        data += b'\x00' * 2
        data += struct.pack('>9I',
            0x00010000, 0, 0,
            0, 0x00010000, 0,
            0, 0, 0x40000000)
        data += struct.pack('>I', width << 16)
        data += struct.pack('>I', height << 16)
        return struct.pack('>I', len(data) + 8) + b'tkhd' + bytes(data)

    def build_mdhd(self, timescale: int, duration: int) -> bytes:
        data = bytearray()
        data += struct.pack('>I', 0)
        data += struct.pack('>I', 0)
        data += struct.pack('>I', 0)
        data += struct.pack('>I', timescale)
        data += struct.pack('>I', duration)
        data += struct.pack('>H', 0x55C4)
        data += struct.pack('>H', 0)
        return struct.pack('>I', len(data) + 8) + b'mdhd' + bytes(data)

    def build_hdlr(self, handler_type: bytes, name: str) -> bytes:
        data = bytearray()
        data += struct.pack('>I', 0)
        data += b'\x00' * 4
        data += handler_type
        data += b'\x00' * 12
        data += name.encode('utf-8') + b'\x00'
        return struct.pack('>I', len(data) + 8) + b'hdlr' + bytes(data)

    def build_stsd_hevc(self, width: int, height: int,
                        vps: bytes, sps: bytes, pps: bytes) -> bytes:
        hvcc = bytearray()
        hvcc += struct.pack('>B', 1)
        hvcc += struct.pack('>B', 0x01)
        hvcc += struct.pack('>I', 0x60000000)
        hvcc += b'\x90\x00\x00\x00\x00\x00'
        hvcc += struct.pack('>B', 153)
        hvcc += struct.pack('>H', 0xF000)
        hvcc += struct.pack('>B', 0xFC)
        hvcc += struct.pack('>B', 0xFD)
        hvcc += struct.pack('>B', 0xF8)
        hvcc += struct.pack('>B', 0xF8)
        hvcc += struct.pack('>H', 0)
        hvcc += struct.pack('>B', 0x0F)
        hvcc += struct.pack('>B', 3)
        hvcc += struct.pack('>B', 0xA0)
        hvcc += struct.pack('>H', 1)
        hvcc += struct.pack('>H', len(vps))
        hvcc += vps
        hvcc += struct.pack('>B', 0xA1)
        hvcc += struct.pack('>H', 1)
        hvcc += struct.pack('>H', len(sps))
        hvcc += sps
        hvcc += struct.pack('>B', 0xA2)
        hvcc += struct.pack('>H', 1)
        hvcc += struct.pack('>H', len(pps))
        hvcc += pps
        hvcc_atom = struct.pack('>I', len(hvcc) + 8) + b'hvcC' + bytes(hvcc)
        entry = bytearray()
        entry += b'\x00' * 6
        entry += struct.pack('>H', 1)
        entry += struct.pack('>H', 0)
        entry += struct.pack('>H', 0)
        entry += b'\x00' * 12
        entry += struct.pack('>H', width)
        entry += struct.pack('>H', height)
        entry += struct.pack('>I', 0x00480000)
        entry += struct.pack('>I', 0x00480000)
        entry += struct.pack('>I', 0)
        entry += struct.pack('>H', 1)
        entry += b'\x00' * 32
        entry += struct.pack('>H', 0x0018)
        entry += struct.pack('>h', -1)
        entry += hvcc_atom
        entry_atom = struct.pack('>I', len(entry) + 8) + b'hvc1' + bytes(entry)
        stsd_data = struct.pack('>I', 0) + struct.pack('>I', 1) + entry_atom
        return struct.pack('>I', len(stsd_data) + 8) + b'stsd' + stsd_data

    def build_stsd_aac(self, sample_rate: int = X4_AUDIO_SAMPLE_RATE, channels: int = X4_AUDIO_CHANNELS) -> bytes:
        aac_config = self._build_aac_config(sample_rate, channels)
        esds = self._build_esds(aac_config)
        esds_atom = struct.pack('>I', len(esds) + 8) + b'esds' + esds
        entry = bytearray()
        entry += b'\x00' * 6
        entry += struct.pack('>H', 1)
        entry += struct.pack('>H', 0)
        entry += struct.pack('>H', 0)
        entry += struct.pack('>I', 0)
        entry += struct.pack('>H', channels)
        entry += struct.pack('>H', 16)
        entry += struct.pack('>H', 0)
        entry += struct.pack('>H', 0)
        entry += struct.pack('>I', sample_rate << 16)
        entry += esds_atom
        entry_atom = struct.pack('>I', len(entry) + 8) + b'mp4a' + bytes(entry)
        stsd_data = struct.pack('>I', 0) + struct.pack('>I', 1) + entry_atom
        return struct.pack('>I', len(stsd_data) + 8) + b'stsd' + stsd_data

    @staticmethod
    def _build_aac_config(sample_rate: int, channels: int) -> bytes:
        freq_index = {96000: 0, 88200: 1, 64000: 2, 48000: 3, 44100: 4,
                      32000: 5, 24000: 6, 22050: 7, 16000: 8, 12000: 9,
                      11025: 10, 8000: 11}.get(sample_rate, 3)
        config = ((2 << 11) | (freq_index << 7) | (channels << 3)) & 0xFFFF
        return struct.pack('>H', config)

    @staticmethod
    def _build_esds(aac_config: bytes) -> bytes:
        data = bytearray()
        data += struct.pack('>I', 0)
        es_desc = bytearray()
        es_desc += struct.pack('>H', 2)
        es_desc += struct.pack('>B', 0)
        dec_config = bytearray()
        dec_config += struct.pack('>B', 0x40)
        dec_config += struct.pack('>B', 0x15)
        dec_config += b'\x00\x00\x00'
        dec_config += struct.pack('>I', 128000)
        dec_config += struct.pack('>I', 128000)
        dsi = bytes([0x05, len(aac_config)]) + aac_config
        dec_config += dsi
        dec_config_tagged = bytes([0x04, len(dec_config)]) + dec_config
        es_desc += dec_config_tagged
        es_desc += bytes([0x06, 0x01, 0x02])
        es_desc_tagged = bytes([0x03, len(es_desc)]) + es_desc
        data += es_desc_tagged
        return bytes(data)

    def build_stts(self, entries: List[tuple]) -> bytes:
        data = struct.pack('>I', 0)
        data += struct.pack('>I', len(entries))
        for count, delta in entries:
            data += struct.pack('>II', count, delta)
        return struct.pack('>I', len(data) + 8) + b'stts' + data

    def build_stss(self, sync_samples: List[int]) -> bytes:
        data = struct.pack('>I', 0)
        data += struct.pack('>I', len(sync_samples))
        for s in sync_samples:
            data += struct.pack('>I', s)
        return struct.pack('>I', len(data) + 8) + b'stss' + data

    def build_stsc(self, entries: List[tuple]) -> bytes:
        data = struct.pack('>I', 0)
        data += struct.pack('>I', len(entries))
        for fc, spc, sdi in entries:
            data += struct.pack('>III', fc, spc, sdi)
        return struct.pack('>I', len(data) + 8) + b'stsc' + data

    def build_stsz(self, sample_sizes: List[int]) -> bytes:
        data = struct.pack('>I', 0)
        data += struct.pack('>I', 0)
        data += struct.pack('>I', len(sample_sizes))
        for s in sample_sizes:
            data += struct.pack('>I', s)
        return struct.pack('>I', len(data) + 8) + b'stsz' + data

    def build_co64(self, chunk_offsets: List[int]) -> bytes:
        data = struct.pack('>I', 0)
        data += struct.pack('>I', len(chunk_offsets))
        for off in chunk_offsets:
            data += struct.pack('>Q', off)
        return struct.pack('>I', len(data) + 8) + b'co64' + data

    def build_ctts(self, entries: List[tuple]) -> bytes:
        data = struct.pack('>I', 0)
        data += struct.pack('>I', len(entries))
        for count, offset in entries:
            data += struct.pack('>II', count, offset)
        return struct.pack('>I', len(data) + 8) + b'ctts' + data

    def build_stbl(self, stsd: bytes, stts: bytes, stsc: bytes,
                   stsz: bytes, co64: bytes, stss: Optional[bytes] = None,
                   ctts: Optional[bytes] = None) -> bytes:
        children = stsd + stts
        if ctts:
            children += ctts
        children += stsc + stsz + co64
        if stss:
            children += stss
        return struct.pack('>I', len(children) + 8) + b'stbl' + children

    def build_dinf(self) -> bytes:
        dref_entry = struct.pack('>I', 12) + b'alis' + struct.pack('>I', 0x000001)
        dref_data = struct.pack('>I', 0) + struct.pack('>I', 1) + dref_entry
        dref = struct.pack('>I', len(dref_data) + 8) + b'dref' + dref_data
        return struct.pack('>I', len(dref) + 8) + b'dinf' + dref

    def build_elst(self, entries: List[tuple], movie_timescale: int = 600) -> bytes:
        data = struct.pack('>I', 0)
        data += struct.pack('>I', len(entries))
        for seg_dur, med_time, rate_int, rate_frac in entries:
            data += struct.pack('>I', seg_dur)
            data += struct.pack('>i', med_time)
            data += struct.pack('>h', rate_int)
            data += struct.pack('>h', rate_frac)
        return struct.pack('>I', len(data) + 8) + b'elst' + data

    def build_edts(self, elst: bytes) -> bytes:
        return struct.pack('>I', len(elst) + 8) + b'edts' + elst

    def build_vmhd(self) -> bytes:
        data = struct.pack('>I', 1)
        data += struct.pack('>H', 0)
        data += struct.pack('>3H', 0, 0, 0)
        return struct.pack('>I', len(data) + 8) + b'vmhd' + data

    def build_smhd(self) -> bytes:
        data = struct.pack('>I', 0)
        data += struct.pack('>H', 0)
        data += struct.pack('>H', 0)
        return struct.pack('>I', len(data) + 8) + b'smhd' + data

    def build_minf(self, media_header: bytes, dinf: bytes, stbl: bytes) -> bytes:
        children = media_header + dinf + stbl
        return struct.pack('>I', len(children) + 8) + b'minf' + children

    def build_mdia(self, mdhd: bytes, hdlr: bytes, minf: bytes) -> bytes:
        children = mdhd + hdlr + minf
        return struct.pack('>I', len(children) + 8) + b'mdia' + children

    def build_trak(self, tkhd: bytes, mdia: bytes, edts: Optional[bytes] = None) -> bytes:
        children = tkhd
        if edts:
            children += edts
        children += mdia
        return struct.pack('>I', len(children) + 8) + b'trak' + children

    def build_moov(self, mvhd: bytes, udta: Optional[bytes], traks: List[bytes]) -> bytes:
        children = mvhd
        if udta:
            children += udta
        for trak in traks:
            children += trak
        return struct.pack('>I', len(children) + 8) + b'moov' + children