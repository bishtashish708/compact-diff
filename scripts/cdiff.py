#!/usr/bin/env python3
"""compact-diff entry point. Stdlib only; needs Python 3.9+."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compact_diff.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
