from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .base import MP4Box

@dataclass
class DataEntryAliasBox(MP4Box):
    """Alias Data Entry Box (``alis``)."""

    version: int = 0
    flags: int = 0
    alias_data: bytes | None = None

    @classmethod
    def from_parsed(
        cls,
        box_type: str,
        size: int,
        offset: int,
        data: bytes,
        children: List[MP4Box] | None = None,
    ) -> "DataEntryAliasBox":
        version = data[0] if data else 0
        flags = int.from_bytes(data[1:4], "big") if len(data) >= 4 else 0
        alias_data = data[4:] if (flags & 0x000001) == 0 and len(data) > 4 else None
        return cls(
            box_type,
            size,
            offset,
            children or [],
            None,
            version,
            flags,
            alias_data,
        )

    def properties(self) -> Dict[str, object]:
        props = super().properties()
        props.update(
            {
                "flags": self.flags,
                "version": self.version,
                "alias_data": self.alias_data,
            }
        )
        return props