import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class SyncRecord:
    synced_at: str
    playlist_name: str
    netease_id: str
    total: int
    matched_strict: int
    matched_fuzzy: int
    unmatched: int
    navidrome_playlist_id: Optional[str]


class History:
    def __init__(self, path: str):
        self._path = path

    def _load(self) -> list[dict]:
        if not os.path.exists(self._path):
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []

    def append(self, record: SyncRecord) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        records = self._load()
        records.append(asdict(record))
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    def all(self) -> list[SyncRecord]:
        return [SyncRecord(**r) for r in self._load()]

    @staticmethod
    def make_record(
        playlist_name: str,
        netease_id: str,
        total: int,
        matched_strict: int,
        matched_fuzzy: int,
        unmatched: int,
        navidrome_playlist_id: Optional[str],
    ) -> SyncRecord:
        return SyncRecord(
            synced_at=datetime.now().isoformat(timespec="seconds"),
            playlist_name=playlist_name,
            netease_id=netease_id,
            total=total,
            matched_strict=matched_strict,
            matched_fuzzy=matched_fuzzy,
            unmatched=unmatched,
            navidrome_playlist_id=navidrome_playlist_id,
        )
