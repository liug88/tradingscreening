"""JSON-on-disk cache.

Fundamentals change once a quarter and the CBOE symbol list changes rarely, so
re-fetching them every morning is wasted time. These files get committed back to
the repo by the daily workflow, which is what lets the cache survive between runs
on GitHub's throwaway runners.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._entries: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._entries = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # A corrupt cache is not worth failing the morning run over.
                self._entries = {}

    def get(self, key: str, max_age_days: float | None = None) -> Any | None:
        """Return the cached value, or None if missing or older than max_age_days."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        if max_age_days is not None:
            age = datetime.now(timezone.utc) - _parse(entry["fetched_at"])
            if age > timedelta(days=max_age_days):
                return None
        return entry["value"]

    def set(self, key: str, value: Any) -> None:
        self._entries[key] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "value": value,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # sort_keys keeps the committed diff readable day to day.
        self.path.write_text(
            json.dumps(self._entries, indent=1, sort_keys=True), encoding="utf-8"
        )

    def __len__(self) -> int:
        return len(self._entries)


def _parse(stamp: str) -> datetime:
    dt = datetime.fromisoformat(stamp)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
