"""Tests for drugsatfda. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from datetime import date

from trial_pos.services.drugsatfda import (
    APPL_NO_WIDTH, DELIMITER, ENCODINGS, PRODUCT_NO_WIDTH, decode, norm_appl, norm_product,
    parse_date, parse_tab, require_columns,
)


def _raises(fn, *args, exc=ValueError):
    try:
        fn(*args)
    except exc:
        return True
    return False


def test_parse_tab_counts_malformed_rows_and_strips_the_bom():
    text = "\ufeffA" + DELIMITER + "B\n1" + DELIMITER + "2\n\n3\n4" + DELIMITER + "5\n"
    t = parse_tab(text)
    assert t["header"] == ["A", "B"]
    assert t["rows"] == [{"A": "1", "B": "2"}, {"A": "4", "B": "5"}]
    assert t["malformed"] == 1


def test_quotes_are_data_not_quoting():
    t = parse_tab("A" + DELIMITER + "B\n\"x" + DELIMITER + "y\"\n")
    assert t["rows"] == [{"A": "\"x", "B": "y\""}] and t["malformed"] == 0


def test_decode_falls_back_in_the_declared_order():
    assert decode("caf\u00e9".encode(ENCODINGS[0]))[1] == ENCODINGS[0]
    assert decode("caf\u00e9".encode(ENCODINGS[1]))[1] == ENCODINGS[1]


def test_keys_are_zero_padded_digits_only():
    assert norm_appl("20357") == "20357".zfill(APPL_NO_WIDTH)
    assert norm_product("1") == "1".zfill(PRODUCT_NO_WIDTH)
    assert norm_appl(" N123 ") == "N123" and norm_appl(None) == ""


def test_dates_parse_in_every_declared_format_and_blank_is_none():
    d = date(2001, 2, 3)
    for raw in ("2001-02-03 00:00:00", "2001-02-03", "02/03/2001 00:00:00", "02/03/2001"):
        assert parse_date(raw) == d, raw
    assert parse_date("") is None and parse_date("not a date") is None


def test_require_columns_fails_closed():
    t = parse_tab("A\n1\n")
    require_columns(t, ("A",), "t")
    assert _raises(require_columns, t, ("A", "B"), "t")
