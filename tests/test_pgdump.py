"""Tests for the pg_dump COPY reader. Plain asserts so tests/_run_stdlib.py works.

Expected values are built from the escape table and the fixture itself, not transcribed.
"""
from __future__ import annotations

from trial_pos.services.pgdump import (
    COPY_TERMINATOR, FIELD_SEPARATOR, NULL_TOKEN, DumpFormatError, column_null_profile,
    iter_copy_blocks, parse_copy_header, unescape_field,
)


def _raises(fn) -> bool:
    try:
        fn()
    except DumpFormatError:
        return True
    return False


def _block(table, columns, rows, schema="public"):
    head = f"COPY {schema + '.' if schema else ''}{table} ({', '.join(columns)}) FROM stdin;\n"
    body = "".join(FIELD_SEPARATOR.join(r) + "\n" for r in rows)
    return [head] + body.splitlines(keepends=True) + [COPY_TERMINATOR + "\n"]


# ---- header -----------------------------------------------------------------
def test_qualified_header():
    assert parse_copy_header("COPY public.approval (id, struct_id, type) FROM stdin;\n") \
        == ("public", "approval", ("id", "struct_id", "type"))


def test_unqualified_and_quoted_header():
    assert parse_copy_header('COPY "approval" ("id", "type") FROM stdin;') \
        == (None, "approval", ("id", "type"))


def test_non_copy_lines_are_not_headers():
    for line in ("", "SET client_encoding = 'UTF8';", "CREATE TABLE public.approval (",
                 "COPY public.approval TO stdout;"):
        assert parse_copy_header(line) is None, line


# ---- escapes ------------------------------------------------------------------
def test_null_is_none_and_empty_is_empty():
    assert unescape_field(NULL_TOKEN) is None
    assert unescape_field("") == ""


def test_simple_escapes():
    for code, char in (("t", "\t"), ("n", "\n"), ("r", "\r"), ("b", "\b"), ("f", "\f"),
                       ("v", "\v"), ("\\", "\\")):
        assert unescape_field("a\\" + code + "b") == "a" + char + "b", code


def test_octal_and_hex_escapes():
    assert unescape_field("\\" + format(ord("A"), "o")) == "A"
    assert unescape_field("\\x" + format(ord("A"), "x")) == "A"


def test_text_without_backslashes_is_unchanged():
    assert unescape_field("Abacavir sulfate (1:2)") == "Abacavir sulfate (1:2)"


def test_an_escaped_null_token_is_text_not_null():
    # the two-character text "\N" is written escaped as \\N, which is not NULL
    assert unescape_field("\\\\N") == "\\N"


def test_trailing_backslash_raises():
    assert _raises(lambda: unescape_field("abc\\"))


# ---- blocks -------------------------------------------------------------------
def test_wanted_block_is_read_and_others_skipped():
    lines = (["SET x = 1;\n"]
             + _block("skip_me", ("a",), [["1"], ["2"]])
             + _block("approval", ("id", "type"), [["1", "FDA"], ["2", NULL_TOKEN]]))
    out = list(iter_copy_blocks(lines, {"approval": None}))
    assert [t for t, _, _ in out] == ["approval"]
    _, cols, rows = out[0]
    assert cols == ("id", "type") and rows == [["1", "FDA"], ["2", None]]


def test_column_selection_keeps_the_requested_order():
    lines = _block("structures", ("id", "molfile", "name"), [["7", "big", "aspirin"]])
    (_, cols, rows), = iter_copy_blocks(lines, {"structures": ("name", "id")})
    assert cols == ("name", "id") and rows == [["aspirin", "7"]]


def test_a_requested_column_absent_from_the_header_raises():
    lines = _block("structures", ("id", "name"), [["7", "aspirin"]])
    assert _raises(lambda: list(iter_copy_blocks(lines, {"structures": ("id", "inchikey")})))


def test_an_unterminated_block_raises_wanted_or_not():
    for wanted in ({"t": None}, {"other": None}):
        lines = _block("t", ("a",), [["1"]])[:-1]
        assert _raises(lambda: list(iter_copy_blocks(lines, wanted))), wanted


def test_a_field_count_mismatch_raises():
    lines = ["COPY public.t (a, b) FROM stdin;\n", "1\n", COPY_TERMINATOR + "\n"]
    assert _raises(lambda: list(iter_copy_blocks(lines, {"t": None})))


def test_a_table_appearing_twice_raises():
    lines = _block("t", ("a",), [["1"]]) + _block("t", ("a",), [["2"]])
    assert _raises(lambda: list(iter_copy_blocks(lines, {"t": None})))


def test_schema_restriction():
    lines = _block("t", ("a",), [["1"]], schema="other") + _block("t", ("a",), [["2"]])
    (_, _, rows), = iter_copy_blocks(lines, {"t": None}, schema="public")
    assert rows == [["2"]]


def test_windows_line_endings_do_not_leak_into_the_last_field():
    lines = [l.replace("\n", "\r\n") for l in _block("t", ("a", "b"), [["1", "x"]])]
    (_, _, rows), = iter_copy_blocks(lines, {"t": None})
    assert rows == [["1", "x"]]


def test_an_escaped_tab_does_not_split_a_field():
    lines = _block("t", ("a", "b"), [["x\\ty", "z"]])
    (_, _, rows), = iter_copy_blocks(lines, {"t": None})
    assert rows == [["x\ty", "z"]]


def test_an_empty_table_yields_no_rows():
    (_, _, rows), = iter_copy_blocks(_block("t", ("a",), []), {"t": None})
    assert rows == []


# ---- null profile -------------------------------------------------------------
def test_null_profile_separates_null_from_empty():
    rows = [[None, ""], ["", "x"], [None, None]]
    profile = column_null_profile(("a", "b"), rows)
    assert profile["a"] == {"null": sum(1 for r in rows if r[0] is None),
                            "empty": sum(1 for r in rows if r[0] == "")}
    assert profile["b"] == {"null": sum(1 for r in rows if r[1] is None),
                            "empty": sum(1 for r in rows if r[1] == "")}
