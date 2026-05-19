from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Dict, List

from .base import MP4Box

@dataclass
class ChunkLargeOffsetBox(MP4Box):
    """64-bit Chunk Offset Box (``co64``)."""

    version: int = 0
    flags: int = 0
    chunk_offsets: List[int] | None = None

    @classmethod
    def from_parsed(
        cls,
        box_type: str,
        size: int,
        offset: int,
        data: bytes,
        children: List[MP4Box] | None = None,
    ) -> "ChunkLargeOffsetBox":
        version = data[0] if len(data) > 0 else 0
        flags = int.from_bytes(data[1:4], "big") if len(data) >= 4 else 0
        entry_count = struct.unpack(">I", data[4:8])[0] if len(data) >= 8 else 0
        chunk_offsets: List[int] = []
        pos = 8
        for _ in range(entry_count):
            if pos + 8 > len(data):
                break
            chunk_offsets.append(struct.unpack(">Q", data[pos : pos + 8])[0])
            pos += 8
        return cls(
            box_type,
            size,
            offset,
            children or [],
            None,
            version,
            flags,
            chunk_offsets,
        )

    def properties(self) -> Dict[str, object]:
        return {
            "size": self.size,
            "flags": self.flags,
            "version": self.version,
            "box_name": self.__class__.__name__,
            "start": self.offset,
            "chunk_offsets": self.chunk_offsets,
        }