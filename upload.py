#!/usr/bin/env python3
"""Shortcut for uploading local results to Hugging Face."""

import os
import sys
from pathlib import Path


if __name__ == "__main__":
    script = Path(__file__).resolve().parent / "upload" / "upload_to_hf.py"
    os.execv(sys.executable, [sys.executable, str(script), *sys.argv[1:]])
