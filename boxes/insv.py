from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict

from .base import MP4Box

@dataclass
class INSVFile(MP4Box):
    """Top-level INSV file container (array of MP4 boxes)."""

    boxes: List[MP4Box] = field(default_factory=list)

    @classmethod
    def from_parsed(
        cls,
        box_type: str,
        size: int,
        offset: int,
        data: bytes,
        children: List[MP4Box] | None = None,
    ) -> "INSVFile":
        # The children parameter should be the list of top-level boxes
        return cls(box_type, size, offset, children or [], None, children or [])

    def properties(self) -> Dict[str, object]:
        props = super().properties()
        props["boxes"] = [box.properties() for box in self.boxes]
        return props