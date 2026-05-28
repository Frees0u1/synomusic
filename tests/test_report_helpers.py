from io import StringIO
from rich.console import Console
from src.matcher import MatchResult, MatchStatus
from src.report import print_fuzzy_details, print_unmatched_list


def _console() -> Console:
    return Console(file=StringIO(), highlight=False, markup=False)


def test_print_fuzzy_details_empty():
    console = _console()
    print_fuzzy_details([], console)
    output = console.file.getvalue()
    assert output == ""


def test_print_fuzzy_details_renders_rows():
    console = _console()
    result = MatchResult(status=MatchStatus.FUZZY, track_id="1", score=85, low_confidence=False)
    print_fuzzy_details([("稻香", "周杰伦", result)], console)
    output = console.file.getvalue()
    assert "稻香" in output
    assert "周杰伦" in output
    assert "85" in output


def test_print_unmatched_list_empty():
    console = _console()
    print_unmatched_list([], console)
    output = console.file.getvalue()
    assert output == ""


def test_print_unmatched_list_renders_entries():
    console = _console()
    print_unmatched_list([("稻香", "周杰伦"), ("江南", "林俊杰")], console)
    output = console.file.getvalue()
    assert "稻香" in output
    assert "江南" in output
