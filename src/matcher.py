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
class Candidate:
    track_id: str
    title: str
    artist: str
    score: int


@dataclass
class MatchResult:
    status: MatchStatus
    track_id: Optional[str] = None
    score: int = 0
    low_confidence: bool = False
    candidates: list[Candidate] = field(default_factory=list)


class Matcher:
    def __init__(self, library: list[dict], fuzzy_threshold: int = 80):
        self._library = [
            {
                "id": t["id"],
                "title": t["title"],
                "artist": t["artist"],
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

        # Level 2: fuzzy — collect all candidates, keep top 3 by combined score
        scored: list[tuple[float, bool, dict]] = []
        for track in self._library:
            title_score = fuzz.ratio(title_norm, track["title_norm"])
            if title_score < self._threshold:
                continue
            artist_score = fuzz.partial_ratio(artist_norm, track["artist_norm"])
            # High title score (>=95) overrides artist threshold — catches cases like
            # library "(Live)" stripped to exact title match but artist name differs slightly
            low_conf = artist_norm != track["artist_norm"] and (artist_score >= 65 or title_score >= 95)
            if artist_norm == track["artist_norm"] or low_conf:
                combined = title_score * 0.7 + artist_score * 0.3
                scored.append((combined, low_conf, track))

        if not scored:
            return MatchResult(status=MatchStatus.UNMATCHED)

        scored.sort(key=lambda x: x[0], reverse=True)
        best_combined, best_low_conf, best_track = scored[0]
        candidates = [
            Candidate(
                track_id=t["id"],
                title=t["title"],
                artist=t["artist"],
                score=int(round(c)),
            )
            for c, _, t in scored[:3]
        ]
        return MatchResult(
            status=MatchStatus.FUZZY,
            track_id=best_track["id"],
            score=int(round(best_combined)),
            low_confidence=best_low_conf,
            candidates=candidates,
        )
