from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .base import MP4Box

@dataclass
class AMBABox(MP4Box):
    """Insta360 AMBA Metadata Box (``AMBA``)."""

    data: bytes | None = None

    @classmethod
    def from_parsed(
        cls,
        box_type: str,
        size: int,
        offset: int,
        data: bytes,
        children: List[MP4Box] | None = None,
    ) -> "AMBABox":
        return cls(box_type, size, offset, children or [], data)

    def properties(self) -> Dict[str, object]:
        props = super().properties()
        props["data"] = self.data
        return props