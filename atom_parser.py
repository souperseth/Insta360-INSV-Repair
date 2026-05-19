"""
atom_parser.py - AtomParser class for MP4/MOV atom (box) structure
"""

import struct
from typing import BinaryIO, Optional, List
from constants import Atom

class AtomParser:
    """Parse MP4/MOV atom (box) structure."""

    def __init__(self, f: BinaryIO, file_size: int):
        self.f = f
        self.file_size = file_size

    def read_atom_header(self, offset: int) -> Optional[Atom]:
        """Read an atom header at the given offset."""
        self.f.seek(offset)
        header = self.f.read(8)
        if len(header) < 8:
            return None
        size, atom_type = struct.unpack('>I4s', header)
        header_size = 8

        if size == 1:  # 64-bit extended size
            ext = self.f.read(8)
            if len(ext) < 8:
                return None
            size = struct.unpack('>Q', ext)[0]
            header_size = 16
        elif size == 0:  # extends to end of file
            size = self.file_size - offset

        return Atom(offset=offset, size=size, type=atom_type, header_size=header_size)

    def parse_top_level(self) -> List[Atom]:
        """Parse all top-level atoms."""
        atoms = []
        offset = 0
        while offset < self.file_size:
            atom = self.read_atom_header(offset)
            if atom is None or atom.size < 8:
                break
            atoms.append(atom)
            offset += atom.size
        return atoms

    def parse_children(self, parent: Atom) -> List[Atom]:
        """Parse child atoms within a container atom."""
        children = []
        offset = parent.offset + parent.header_size
        end = parent.offset + parent.size
        while offset < end:
            atom = self.read_atom_header(offset)
            if atom is None or atom.size < 8:
                break
            children.append(atom)
            offset += atom.size
        return children

    def find_atom(self, atoms: list, atom_type: bytes) -> Optional[Atom]:
        """Find first atom of given type in a list."""
        for a in atoms:
            if a.type == atom_type:
                return a
        return None

    def read_atom_data(self, atom: Atom) -> bytes:
        """Read the full data payload of an atom (excluding header)."""
        self.f.seek(atom.offset + atom.header_size)
        return self.f.read(atom.size - atom.header_size)

    def extract_atom_raw(self, atom: Atom) -> bytes:
        """Read the entire atom including header."""
        self.f.seek(atom.offset)
        return self.f.read(atom.size)