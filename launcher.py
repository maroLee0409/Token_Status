"""Bootstrap script for autostart — guarantees package resolves regardless of cwd.

Windows Run-key launches commands with cwd = C:\\Windows\\System32, which means
`-m tokenstatus` cannot find the package. Invoking this script directly puts its
parent on sys.path so the import always works.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
os.chdir(HERE)

from tokenstatus.app import main

raise SystemExit(main(sys.argv))
