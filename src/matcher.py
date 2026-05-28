import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from rapidfuzz import fuzz


VERSION_TAGS = re.compile(
    r"\s*[\(\[（【][^\)\]）】]*(live|remix|remaster(?:ed)?|acoustic|cover|version|\d{4})[^\)\]）】]*[\)\]）】]",
    re.IGNORECASE,
)
FEAT_PATTERN = re.compile(r"\bft\.|\bft\b|\bfeaturing\b", re.IGNORECASE)


def normalize(text: str) -> str:
    text = text.strip()
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = FEAT_PATTERN.sub("feat.", text)
    text = VERSION_TAGS.sub("", text)
    return text.strip()


class MatchStatus(Enum):
    STRICT = "strict"
    FUZZY = "fuzzy"
    UNMATCHED = "unmatched"


@dataclass
class MatchResult:
    status: MatchStatus
    track_id: Optional[str] = None
    score: int = 0
    low_confidence: bool = False


class Matcher:
    def __init__(self, library: list[dict], fuzzy_threshold: int = 80):
        self._library = [
            {
                "id": t["id"],
                "title_norm": normalize(t["title"]),
                "artist_norm": normalize(t["artist"]),
            }
            for t in library
        ]
        self._threshold = fuzzy_threshold

    def match(self, title: str, artist: str) -> MatchResult:
        title_norm = normalize(title)
        artist_norm = normalize(artist)

        # Level 1: strict
        for track in self._library:
            if track["title_norm"] == title_norm and track["artist_norm"] == artist_norm:
                return MatchResult(status=MatchStatus.STRICT, track_id=track["id"], score=100)

        # Level 2: fuzzy
        best: Optional[MatchResult] = None
        for track in self._library:
            title_score = fuzz.ratio(title_norm, track["title_norm"])
            if title_score < self._threshold:
                continue
            artist_score = fuzz.partial_ratio(artist_norm, track["artist_norm"])
            low_conf = artist_norm != track["artist_norm"] and artist_score >= 65
            if artist_norm == track["artist_norm"] or low_conf:
                candidate = MatchResult(
                    status=MatchStatus.FUZZY,
                    track_id=track["id"],
                    score=int(title_score),
                    low_confidence=low_conf,
                )
                if best is None or title_score > best.score:
                    best = candidate

        return best if best is not None else MatchResult(status=MatchStatus.UNMATCHED)
