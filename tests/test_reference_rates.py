"""Tests for reference_rates. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from trial_pos.services.reference_rates import (
    ANCHORS, TARGETS, VERIFICATIONS, Figure, anchors_for, figure_percent, format_anchor,
)


def test_every_anchor_is_cited_dated_and_says_what_it_counts():
    keys = [a.key for a in ANCHORS]
    assert len(keys) == len(set(keys))
    for a in ANCHORS:
        assert a.citation and a.locator and a.period and a.counts, a.key
        assert a.targets and set(a.targets) <= set(TARGETS), a.key


def test_every_figure_has_a_verification_and_a_place_it_was_read():
    for a in ANCHORS:
        for f in a.figures:
            assert f.verification in VERIFICATIONS, (a.key, f.what)
            assert f.seen_in, (a.key, f.what)
            assert f.percent is not None or (f.numerator is not None and f.denominator), \
                (a.key, f.what)


def test_figures_are_proportions_and_counts_are_coherent():
    for a in ANCHORS:
        for f in a.figures:
            p = figure_percent(f)
            assert 0.0 < p <= 100.0, (a.key, f.what, p)
            if f.numerator is not None:
                assert 0 <= f.numerator <= f.denominator, (a.key, f.what)


def test_a_percent_from_counts_is_the_ratio_of_the_counts():
    f = Figure("x", None, 3, 4, VERIFICATIONS[0], "here")
    assert figure_percent(f) == 100.0 * f.numerator / f.denominator
    assert figure_percent(Figure("y", None, None, None, VERIFICATIONS[0], "here")) is None


def test_every_target_has_an_anchor_and_the_formatter_never_prints_a_bare_number():
    for t in TARGETS:
        assert anchors_for(t), t
        for a in anchors_for(t):
            lines = format_anchor(a)
            assert lines[0].startswith(a.citation)
            assert any(a.period in line for line in lines)
            assert len(lines) == 3 + max(1, len(a.figures))
            for f, line in zip(a.figures, lines[3:]):
                assert f.verification in line and f.seen_in in line


def test_an_unknown_target_raises():
    try:
        anchors_for("approval_anywhere")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_the_bundle_literature_is_cited_and_says_where_it_was_read():
    from trial_pos.services.reference_rates import FEATURE_LITERATURE, format_support
    keys = [x.key for x in FEATURE_LITERATURE]
    assert len(keys) == len(set(keys)) and keys
    for x in FEATURE_LITERATURE:
        assert x.citation and x.locator and x.supports and x.seen_in, x.key
        lines = format_support(x)
        assert lines[0].startswith(x.citation) and x.locator in lines[0]
