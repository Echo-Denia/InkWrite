"""Entry point: python -m hwlib.cli ..."""

from __future__ import annotations

import sys

from .pipeline import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
