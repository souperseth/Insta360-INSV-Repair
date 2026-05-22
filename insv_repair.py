#!/usr/bin/env python3
"""Compatibility launcher for :mod:`insv_tools.repair`."""

import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from insv_tools.repair import *  # noqa: F401,F403,E402
from insv_tools.repair import main  # noqa: E402


if __name__ == '__main__':
    raise SystemExit(main())
