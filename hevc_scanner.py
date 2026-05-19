"""
hevc_scanner.py - HEVCScanner and MP4NalScanner classes for HEVC NAL/frame parsing
"""

from typing import BinaryIO, Optional, List
from constants import HEVC_IDR_TYPES, HEVC_SLICE_TYPES

class HEVCScanner:
    """Scan raw byte stream for HEVC NAL units and frame boundaries."""

    @staticmethod
    def get_nal_type(nal_header_byte: int) -> int:
        """Extract NAL unit type from first byte of NAL header."""
        return (nal_header_byte >> 1) & 0x3F

    @staticmethod
    def is_keyframe_nal(nal_type: int) -> bool:
        return nal_type in HEVC_IDR_TYPES

    @staticmethod
    def is_slice_nal(nal_type: int) -> bool:
        return nal_type in HEVC_SLICE_TYPES

class MP4NalScanner:
    """
    Scan mdat that uses MP4-style length-prefixed NAL units.
    """

    def __init__(self, f: BinaryIO, mdat_offset: int, mdat_size: int,
                 mdat_header_size: int = 8):
        self.f = f
        self.mdat_offset = mdat_offset
        self.mdat_data_offset = mdat_offset + mdat_header_size  # skip mdat box header
        self.mdat_size = mdat_size
        self.mdat_end = mdat_offset + mdat_size

    def scan_nal_units_at(self, offset: int, max_bytes: int = 1024 * 1024) -> List:
        """Scan length-prefixed NAL units starting at offset.

        Returns list of (offset, size, nal_type) tuples.
        """
        nals = []
        self.f.seek(offset)
        pos = offset
        end = min(offset + max_bytes, self.mdat_end)

        while pos < end:
            self.f.seek(pos)
            length_bytes = self.f.read(4)
            if len(length_bytes) < 4:
                break
            nal_length = int.from_bytes(length_bytes, "big")

            # Sanity check: NAL length should be reasonable
            if nal_length == 0 or nal_length > 50 * 1024 * 1024:
                break

            if pos + 4 + nal_length > self.mdat_end:
                break

            # Read NAL header (2 bytes for HEVC)
            nal_header = self.f.read(min(2, nal_length))
            if len(nal_header) < 1:
                break

            nal_type = HEVCScanner.get_nal_type(nal_header[0])
            nals.append((pos, 4 + nal_length, nal_type))
            pos += 4 + nal_length

        return nals

    def identify_frame_at(self, offset: int) -> Optional[dict]:
        """Try to identify what kind of frame/data starts at offset.

        Returns dict with keys: type ('video'|'audio'|'unknown'),
        size, is_keyframe, nal_types.
        """
        self.f.seek(offset)
        header = self.f.read(8)
        if len(header) < 8:
            return None

        # Try as length-prefixed HEVC NAL unit
        nal_length = int.from_bytes(header[:4], "big")
        if 1 <= nal_length <= 50 * 1024 * 1024:
            nal_type = HEVCScanner.get_nal_type(header[4])
            if nal_type <= 40:  # Valid HEVC NAL type range
                # Scan all NALs in this access unit
                nals = self.scan_nal_units_at(offset)
                if nals:
                    total_size = sum(n[1] for n in nals)
                    nal_types = [n[2] for n in nals]
                    is_keyframe = any(HEVCScanner.is_keyframe_nal(t) for t in nal_types)
                    has_slice = any(HEVCScanner.is_slice_nal(t) for t in nal_types)
                    if has_slice or is_keyframe:
                        return {
                            'type': 'video',
                            'size': total_size,
                            'is_keyframe': is_keyframe,
                            'nal_types': nal_types
                        }

        # Try as AAC ADTS frame
        if header[0] == 0xFF and (header[1] & 0xF0) == 0xF0:
            # ADTS header
            frame_length = ((header[3] & 0x03) << 11) | (header[4] << 3) | ((header[5] >> 5) & 0x07)
            if 7 <= frame_length <= 8192:
                return {
                    'type': 'audio',
                    'size': frame_length,
                    'is_keyframe': True,
                    'nal_types': []
                }

        return None