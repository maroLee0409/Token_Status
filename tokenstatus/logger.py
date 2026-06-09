"""File-based logging since the app runs under pythonw with no console."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import CONFIG_DIR

_LOG_FILE = CONFIG_DIR / "log.txt"
_SETUP = False


def setup() -> Path:
    global _SETUP
    if _SETUP:
        return _LOG_FILE
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        _LOG_FILE, maxBytes=512_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Catch unhandled exceptions so they aren't silent.
    def _hook(exctype, value, tb):
        logging.exception("UNCAUGHT", exc_info=(exctype, value, tb))
    sys.excepthook = _hook

    _SETUP = True
    return _LOG_FILE


def log_path() -> Path:
    return _LOG_FILE
