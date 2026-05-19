from __future__ import annotations
import os
import re
import struct
from typing import Dict, Any, Optional

class MdatExtractor:
    """
    Extracts AMBA, nail, trailer, and magic phrase from a broken Insta360 .insv file's mdat region.
    Returns all components as variables for use in repair pipeline.
    """

    # AMBA protobuf marker (start of metadata block)
    AMBA_MARKER = b"\x00\x02\x28\xC0\x12\x00\x0A\x0E"
    # Magic phrase is always 32 hex chars at end of file (plus some padding)
    MAGIC_PHRASE_LEN = 32

    def extract(self, filepath: str) -> Dict[str, Any]:
        with open(filepath, "rb") as f:
            f.seek(0, os.SEEK_END)
            filesize = f.tell()
            mdat_data_start = None
            # Find mdat header (after ftyp+wide)
            f.seek(0)
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                size, box_type = struct.unpack(">I4s", header)
                if box_type == b"mdat":
                    mdat_data_start = f.tell()
                    break
                if size == 1:
                    f.read(8)
                elif size == 0:
                    break
                else:
                    f.seek(size - 8, os.SEEK_CUR)
            if mdat_data_start is None:
                raise ValueError("mdat box not found")

            # 1. Magic phrase: last 48 bytes (32 hex chars + padding)
            f.seek(filesize - 48)
            magic_tail = f.read(48)
            magic_match = re.search(rb"([0-9a-f]{32})", magic_tail)
            magic_phrase = magic_match.group(1).decode() if magic_match else ""

            # 2. Find AMBA marker (search last 2MB)
            search_size = min(2 * 1024 * 1024, filesize)
            f.seek(filesize - search_size)
            tail = f.read(search_size)
            amba_idx = tail.rfind(self.AMBA_MARKER)
            if amba_idx == -1:
                raise ValueError("AMBA marker not found")
            amba_off = filesize - search_size + amba_idx

            # 3. Extract AMBA protobuf block (from marker to next nulls or magic phrase)
            f.seek(amba_off)
            amba_data = f.read(2048)  # Read enough to cover metadata
            # Heuristic: AMBA block ends at first long run of zeros or start of trailer
            end = amba_data.find(b"\x00" * 32)
            if end == -1:
                end = len(amba_data)
            amba_data = amba_data[:end]

            # 4. Extract nail/thumbnail region (scan backward for repeating pattern)
            nail_end = amba_off
            scan_len = min(512 * 1024, nail_end)
            f.seek(nail_end - scan_len)
            nail_scan = f.read(scan_len)
            # Heuristic: look for a long run of mostly bytes in 0x70-0x90 range with low entropy
            window = 4096
            threshold = 0.85  # 85% of bytes in range
            nail_start = None
            for i in range(scan_len - window):
                chunk = nail_scan[i:i+window]
                in_range = sum(0x70 <= b <= 0x90 for b in chunk)
                if in_range / window > threshold:
                    # Check for repeating 2-byte pattern
                    pat = chunk[:2]
                    if all(chunk[j:j+2] == pat for j in range(0, window, 2)):
                        nail_start = nail_end - scan_len + i
                        break
            if nail_start is not None:
                nail_data = f.read(amba_off - nail_start)
            else:
                nail_data = b""

            # 5. Extract trailer (from end of AMBA to magic phrase)
            trailer_start = amba_off + len(amba_data)
            trailer_end = filesize - 48
            f.seek(trailer_start)
            trailer_data = f.read(trailer_end - trailer_start)

            # 6. Video/audio data boundary (end of actual media)
            mdat_video_end = nail_start if nail_start else amba_off

            # 7. Parse AMBA metadata (extract serial, model, firmware, etc.)
            amba_metadata = self._parse_amba(amba_data)

            return {
                "amba_data": amba_data,
                "amba_metadata": amba_metadata,
                "nail_data": nail_data,
                "trailer_data": trailer_data,
                "magic_phrase": magic_phrase,
                "mdat_video_end": mdat_video_end,
                "mdat_data_start": mdat_data_start,
                "clean_mdat_size": mdat_video_end - mdat_data_start if mdat_data_start is not None else None,
            }

    def _parse_amba(self, amba_data: bytes) -> Dict[str, Optional[str]]:
        # Heuristic: extract ASCII fields from protobuf
        # Serial: starts with "IBMEA"
        # Model: "Insta360 X4"
        # Firmware: "v1.9.21_build5"
        # Filepath: ".insv"
        fields = {}
        for label in [b"IBMEA", b"Insta360", b"v1.", b".insv"]:
            idx = amba_data.find(label)
            if idx != -1:
                end = amba_data.find(b"\x00", idx)
                if end == -1:
                    end = idx + 64
                value = amba_data[idx:end].decode(errors="replace")
                if label == b"IBMEA":
                    fields["serial"] = value
                elif label == b"Insta360":
                    fields["model"] = value
                elif label == b"v1.":
                    fields["firmware"] = value
                elif label == b".insv":
                    fields["filepath"] = value
        return fields