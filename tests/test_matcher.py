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
    assert normalize("旅行的意义(Live) - live") == "旅行的意义"
    assert normalize("Song - Acoustic") == "song"
    assert normalize("Song - Live") == "song"


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
    library = [{"id": "1", "title": "稻香啊", "artist": "周杰伦"}]
    matcher = Matcher(library, fuzzy_threshold=70)
    result = matcher.match("稻香", "周杰伦")
    assert result.status == MatchStatus.FUZZY
    assert result.track_id == "1"
    assert result.score >= 70


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


def test_high_title_score_matches_even_when_artist_very_different():
    # Library has "(Live)" suffix which normalizes away, but artist name is completely different.
    # Title score ends up 100 — should still match with low confidence rather than be dropped.
    library = [{"id": "1", "title": "旅行的意义(Live)", "artist": "陳綺貞"}]
    matcher = Matcher(library, fuzzy_threshold=80)
    result = matcher.match("旅行的意义", "陈绮贞")
    assert result.status == MatchStatus.FUZZY
    assert result.low_confidence is True
    assert result.track_id == "1"
