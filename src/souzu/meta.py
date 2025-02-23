import os
import sys
from pathlib import Path


def find_souzu() -> Path:
    """Find the souzu executable being run"""
    return Path(os.path.abspath(sys.argv[0]))
