from __future__ import annotations

import threading
import time
from typing import Callable

from .config import Config
from .readers import ProviderSnapshot, read_claude, read_codex, read_gemini


SnapshotMap = dict[str, ProviderSnapshot]
Listener = Callable[[SnapshotMap], None]


class Monitor:
    """Polls each provider's logs on a fixed interval and notifies listeners."""

    def __init__(self, config_getter: Callable[[], Config]) -> None:
        self._config_getter = config_getter
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._snapshots: SnapshotMap = {}
        self._thread: threading.Thread | None = None

    def add_listener(self, fn: Listener) -> None:
        with self._lock:
            self._listeners.append(fn)

    @property
    def snapshots(self) -> SnapshotMap:
        with self._lock:
            return dict(self._snapshots)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="TokenStatus-Monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def refresh_now(self) -> None:
        """Trigger an immediate refresh outside the polling cycle."""
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                snapshots = self._collect()
            except Exception as e:  # noqa: BLE001 — never let polling thread die
                snapshots = {"_error": ProviderSnapshot(name="monitor", error=str(e))}

            with self._lock:
                self._snapshots = snapshots
                listeners = list(self._listeners)

            for fn in listeners:
                try:
                    fn(snapshots)
                except Exception:  # noqa: BLE001
                    pass

            cfg = self._config_getter()
            delay = max(5, int(cfg.refresh_seconds))
            self._wake.wait(timeout=delay)
            self._wake.clear()

    def _collect(self) -> SnapshotMap:
        cfg = self._config_getter()
        out: SnapshotMap = {}
        for account in cfg.providers:
            if not account.enabled:
                continue
            key = account.id
            pc = account.config
            if account.kind == "claude":
                snap = read_claude(
                    pc.log_dir, pc.window_minutes, pc.token_limit,
                    use_api=pc.use_api, org_id=pc.org_id,
                    account_id=account.id, account_email=pc.account_email,
                )
            elif account.kind == "codex":
                snap = read_codex(pc.log_dir, pc.window_minutes, pc.token_limit)
            elif account.kind == "gemini":
                snap = read_gemini(pc.log_dir, pc.window_minutes, pc.message_limit)
            else:
                continue
            if account.label:
                snap.name = account.label
            out[key] = snap
        return out
