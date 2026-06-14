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
# Handles bare suffix style: " - live", " - acoustic", etc. (no brackets)
VERSION_SUFFIX = re.compile(
    r"\s+-\s+(live|remix|remaster(?:ed)?|acoustic|cover|version)\s*$",
    re.IGNORECASE,
)
FEAT_PATTERN = re.compile(r"\bft\.|\bft\b|\bfeaturing\b", re.IGNORECASE)
ARTIST_BRACKETED_ALIAS = re.compile(r"\s*[\(\[（【][^\)\]）】]+[\)\]）】]\s*")
ARTIST_SPLIT_PATTERN = re.compile(
    r"\s*(?:,|，|、|/|／|&|＆|\+|;|；|\band\b|\bfeat\.)\s*",
    re.IGNORECASE,
)
ARTIST_COMPACT_PATTERN = re.compile(r"[\s'’`\".\-_:·・]+")
ARTIST_VARIANT_MAP = str.maketrans(
    {
        "倫": "伦",
        "陳": "陈",
        "綺": "绮",
        "貞": "贞",
        "樂": "乐",
        "隊": "队",
        "張": "张",
        "劉": "刘",
        "趙": "赵",
        "鄭": "郑",
        "黃": "黄",
        "盧": "卢",
        "蘇": "苏",
        "蕭": "萧",
        "馬": "马",
        "鄧": "邓",
        "葉": "叶",
        "謝": "谢",
        "羅": "罗",
        "鍾": "钟",
        "譚": "谭",
        "傑": "杰",
        "偉": "伟",
        "國": "国",
        "華": "华",
        "龍": "龙",
        "鳳": "凤",
        "陽": "阳",
        "飛": "飞",
        "學": "学",
        "鋒": "锋",
        "濤": "涛",
    }
)


def normalize(text: str) -> str:
    text = text.strip()
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = FEAT_PATTERN.sub("feat.", text)
    text = VERSION_TAGS.sub("", text)
    text = VERSION_SUFFIX.sub("", text)
    return text.strip()


def _fold_artist_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return unicodedata.normalize("NFKC", text).translate(ARTIST_VARIANT_MAP)


def _compact_artist(text: str) -> str:
    return ARTIST_COMPACT_PATTERN.sub("", text)


def _artist_variants(artist_norm: str) -> set[str]:
    base = artist_norm.strip()
    without_alias = ARTIST_BRACKETED_ALIAS.sub("", base).strip()
    raw_variants = {base}
    if without_alias:
        raw_variants.add(without_alias)

    variants: set[str] = set()
    for variant in raw_variants:
        folded = _fold_artist_text(variant)
        variants.add(folded)
        compact = _compact_artist(folded)
        if compact:
            variants.add(compact)
    return {variant for variant in variants if variant}


def _artist_parts(artist_norm: str) -> list[str]:
    artist = ARTIST_BRACKETED_ALIAS.sub("", artist_norm).strip()
    parts = []
    for part in ARTIST_SPLIT_PATTERN.split(artist):
        folded = _compact_artist(_fold_artist_text(part.strip()))
        if folded:
            parts.append(folded)
    return parts


def _artist_match_score(expected_norm: str, candidate_norm: str) -> tuple[bool, int]:
    expected_variants = _artist_variants(expected_norm)
    candidate_variants = _artist_variants(candidate_norm)
    if not expected_variants or not candidate_variants:
        return False, 0
    if expected_variants & candidate_variants:
        return True, 100

    expected_parts = _artist_parts(expected_norm)
    candidate_parts = _artist_parts(candidate_norm)
    if expected_parts and candidate_parts:
        if (
            len(expected_parts) > 1
            and len(candidate_parts) > 1
            and set(expected_parts) == set(candidate_parts)
        ):
            return True, 95
        if len(expected_parts) == 1 and len(candidate_parts) > 1 and expected_parts[0] == candidate_parts[0]:
            return True, 90
        if len(candidate_parts) == 1 and len(expected_parts) > 1 and candidate_parts[0] == expected_parts[0]:
            return True, 90

    artist_score = max(
        fuzz.ratio(expected_variant, candidate_variant)
        for expected_variant in expected_variants
        for candidate_variant in candidate_variants
    )
    return artist_score >= 82, int(round(artist_score))


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
    path: str = ""


@dataclass
class MatchResult:
    status: MatchStatus
    track_id: Optional[str] = None
    score: int = 0
    low_confidence: bool = False
    candidates: list[Candidate] = field(default_factory=list)
    matched_title: str = ""
    matched_artist: str = ""
    matched_path: str = ""


class Matcher:
    def __init__(self, library: list[dict], fuzzy_threshold: int = 80):
        self._library = [
            {
                "id": t["id"],
                "title": t["title"],
                "artist": t["artist"],
                "path": t.get("path", ""),
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
                return MatchResult(
                    status=MatchStatus.STRICT, track_id=track["id"], score=100,
                    matched_title=track["title"], matched_artist=track["artist"],
                    matched_path=track.get("path", ""),
                )

        # Level 2: fuzzy — collect all candidates, keep top 3 by combined score
        scored: list[tuple[float, bool, dict]] = []
        for track in self._library:
            title_score = fuzz.ratio(title_norm, track["title_norm"])
            if title_score < self._threshold:
                continue
            artist_ok, artist_score = _artist_match_score(artist_norm, track["artist_norm"])
            if artist_ok:
                low_conf = artist_norm != track["artist_norm"]
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
                path=t.get("path", ""),
            )
            for c, _, t in scored[:3]
        ]
        return MatchResult(
            status=MatchStatus.FUZZY,
            track_id=best_track["id"],
            score=int(round(best_combined)),
            low_confidence=best_low_conf,
            candidates=candidates,
            matched_title=best_track["title"],
            matched_artist=best_track["artist"],
            matched_path=best_track.get("path", ""),
        )
