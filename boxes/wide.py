from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict

from .base import MP4Box

@dataclass
class WideBox(MP4Box):
    """Wide Placeholder Box (``wide``)."""

    @classmethod
    def from_parsed(
        cls,
        box_type: str,
        size: int,
        offset: int,
        data: bytes,
        children: List[MP4Box] | None = None,
    ) -> "WideBox":
        return cls(box_type, size, offset, children or [], data)

    def properties(self) -> Dict[str, object]:
        props = super().properties()
        props["data"] = self.data
        return props