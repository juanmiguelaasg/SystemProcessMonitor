import pytest

from spm import theme as t
from spm.widgets import _TOKEN_RE, _sort_key


@pytest.mark.parametrize("value, expected", [
    (0, "0 B"),
    (512, "512 B"),
    (1024, "1.0 KB"),
    (1536, "1.5 KB"),
    (5 * 1024 ** 2, "5.0 MB"),
    (2.5 * 1024 ** 3, "2.5 GB"),
    (None, "0 B"),
])
def test_fmt_bytes(value, expected):
    assert t.fmt_bytes(value) == expected


def test_fmt_rate():
    assert t.fmt_rate(2048) == "2.0 KB/s"


@pytest.mark.parametrize("seconds, expected", [
    (59, "0m 59s"),
    (3600 + 120, "1h 2m"),
    (2 * 86400 + 3 * 3600 + 5 * 60, "2d 3h 5m"),
])
def test_fmt_duration(seconds, expected):
    assert t.fmt_duration(seconds) == expected


def test_mix():
    assert t.mix("#000000", "#ffffff", 0) == "#000000"
    assert t.mix("#000000", "#ffffff", 1) == "#ffffff"
    assert t.mix("#000000", "#ffffff", 0.5) == "#808080"


def test_level_color():
    assert t.level_color(10) == t.GREEN
    assert t.level_color(70) == t.AMBER
    assert t.level_color(95) == t.ROSE


def test_sort_key_numbers_before_text_and_nulls_last():
    values = ["b", None, 10, "2", "a", 1.5]
    assert sorted(values, key=_sort_key) == [1.5, "2", 10, "a", "b", None]


def test_sql_tokenizer():
    sql = "SELECT name, COUNT(*) FROM t WHERE x = 'it''s' AND ts > :since -- hi\nLIMIT 5"
    kinds = [(m.lastgroup, m.group()) for m in _TOKEN_RE.finditer(sql)]
    assert ("string", "'it''s'") in kinds
    assert ("param", ":since") in kinds
    assert ("comment", "-- hi") in kinds
    assert ("number", "5") in kinds
