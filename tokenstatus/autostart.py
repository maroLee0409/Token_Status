"""Cross-platform "launch at login" management.

Windows  -> HKCU Run registry key
macOS    -> ~/Library/LaunchAgents/com.TokenStatus.plist (launchd)
Linux    -> no-op (left to the desktop environment)
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "TokenStatus"


def _launch_parts() -> list[str]:
    """[executable, *args] used to relaunch the app in tray-only mode at login."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--tray"]
    python = Path(sys.executable)
    if sys.platform.startswith("win"):
        # Prefer the console-less interpreter on Windows.
        pythonw = python.with_name("pythonw.exe")
        runner = pythonw if pythonw.exists() else python
    else:
        runner = python
    launcher = Path(__file__).resolve().parent.parent / "launcher.py"
    return [str(runner), str(launcher), "--tray"]


# ---------------------------------------------------------------- Windows
if sys.platform.startswith("win"):
    import winreg

    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def _command_string() -> str:
        return " ".join(f'"{p}"' if " " in p else p for p in _launch_parts())

    def is_enabled() -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, APP_NAME)
            return True
        except OSError:
            return False

    def enable() -> None:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command_string())

    def disable() -> None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, APP_NAME)
        except OSError:
            pass


# ------------------------------------------------------------------ macOS
elif sys.platform == "darwin":
    _LABEL = f"com.{APP_NAME}"
    _PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"

    def _plist_xml() -> str:
        args = "".join(f"        <string>{p}</string>\n" for p in _launch_parts())
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            "<dict>\n"
            "    <key>Label</key>\n"
            f"    <string>{_LABEL}</string>\n"
            "    <key>ProgramArguments</key>\n"
            "    <array>\n"
            f"{args}"
            "    </array>\n"
            "    <key>RunAtLoad</key>\n"
            "    <true/>\n"
            "</dict>\n"
            "</plist>\n"
        )

    def is_enabled() -> bool:
        return _PLIST.exists()

    def enable() -> None:
        _PLIST.parent.mkdir(parents=True, exist_ok=True)
        _PLIST.write_text(_plist_xml(), encoding="utf-8")

    def disable() -> None:
        try:
            _PLIST.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ other
else:
    def is_enabled() -> bool:
        return False

    def enable() -> None:
        pass

    def disable() -> None:
        pass


def apply(desired: bool) -> None:
    if desired and not is_enabled():
        enable()
    elif not desired and is_enabled():
        disable()
