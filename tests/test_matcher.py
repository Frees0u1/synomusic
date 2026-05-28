from src.matcher import normalize, MatchResult, MatchStatus, Matcher


def test_normalize_strips_whitespace():
    assert normalize("  hello  ") == "hello"


def test_normalize_fullwidth_to_halfwidth():
    assert normalize("ａｂｃ") == "abc"


def test_normalize_lowercase():
    assert normalize("Hello World") == "hello world"


def test_normalize_feat_variants():
    assert normalize("Song feat. Artist") == "song feat. artist"
    assert normalize("Song ft. Artist") == "song feat. artist"
    assert normalize("Song featuring Artist") == "song feat. artist"


def test_normalize_removes_version_tags():
    assert normalize("Song (Live)") == "song"
    assert normalize("Song (Remix)") == "song"
    assert normalize("Song (Remastered)") == "song"
    assert normalize("Song (2024 Remaster)") == "song"


def test_strict_match():
    library = [
        {"id": "1", "title": "稻香", "artist": "周杰伦"},
        {"id": "2", "title": "江南", "artist": "林俊杰"},
    ]
    matcher = Matcher(library, fuzzy_threshold=80)
    result = matcher.match("稻香", "周杰伦")
    assert result.status == MatchStatus.STRICT
    assert result.track_id == "1"
    assert result.score == 100


def test_fuzzy_match_title():
    library = [{"id": "1", "title": "稻香 (Live版)", "artist": "周杰伦"}]
    matcher = Matcher(library, fuzzy_threshold=80)
    result = matcher.match("稻香", "周杰伦")
    assert result.status in (MatchStatus.STRICT, MatchStatus.FUZZY)
    assert result.track_id == "1"
    assert result.score >= 80


def test_no_match():
    library = [{"id": "1", "title": "江南", "artist": "林俊杰"}]
    matcher = Matcher(library, fuzzy_threshold=80)
    result = matcher.match("稻香", "周杰伦")
    assert result.status == MatchStatus.UNMATCHED
    assert result.track_id is None


def test_fuzzy_low_confidence_when_artist_also_fuzzy():
    library = [{"id": "1", "title": "稻香", "artist": "周杰倫"}]  # 艺术家略有出入
    matcher = Matcher(library, fuzzy_threshold=80)
    result = matcher.match("稻香", "周杰伦")
    assert result.status == MatchStatus.FUZZY
    assert result.low_confidence is True
