from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProviderSnapshot:
    name: str
    available: bool = False
    percent: float = 0.0
    used: int = 0
    limit: int = 0
    unit: str = "tokens"
    window_label: str = "최근 5시간"
    secondary_percent: Optional[float] = None
    secondary_label: Optional[str] = None
    resets_at: Optional[float] = None
    last_activity: Optional[float] = None
    note: str = ""
    error: str = ""

    def percent_clamped(self) -> float:
        return max(0.0, min(self.percent, 999.0))


_FILE_CACHE: dict[str, tuple[float, object]] = {}


def cache_get(path: str, mtime: float) -> Optional[object]:
    entry = _FILE_CACHE.get(path)
    if entry and entry[0] == mtime:
        return entry[1]
    return None


def cache_put(path: str, mtime: float, value: object) -> None:
    _FILE_CACHE[path] = (mtime, value)


def cache_evict_missing(paths_present: set[str]) -> None:
    for stale in list(_FILE_CACHE.keys()):
        if stale not in paths_present:
            _FILE_CACHE.pop(stale, None)
