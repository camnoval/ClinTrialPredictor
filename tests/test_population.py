"""Tests for the population / FDAAA-carriage engine.

No expected value is transcribed. Every assertion derives from one of:
  - a named statutory constant in the module under test (era boundaries),
  - a structural property that must hold whatever the values are (the eras partition the
    timeline; coverage counts sum to n; every component is documented),
  - the calendar itself (a date one day either side of a boundary),
  - a cross-module invariant (the posting-outcome field is registered as a label input).

A transcribed year like 2008 would pass just as happily against a wrong boundary, and if
it were transcribed FROM a buggy boundary it would certify the bug.
"""
from datetime import date, timedelta

from trial_pos.services.endpoint_label import LABEL_DERIVED_FIELDS
from trial_pos.services.population import (
    ERA_DOC, ERAS, FDAAA_COMPONENT_DOC, FDAAA_COMPONENTS, FDAAA_ENACTED,
    FDAAA_RESULTS_EFFECTIVE, FINAL_RULE_COMPLIANCE, FINAL_RULE_EFFECTIVE,
    KNOWN_STUDY_TYPES, NOT_APPLICABLE, POSTING_OUTCOME_FIELDS,
    STUDY_TYPE_EXPANDED_ACCESS, STUDY_TYPE_INTERVENTIONAL, STUDY_TYPE_OBSERVATIONAL,
    ERA_DATE_ABSENT, ERA_DATE_FALLBACK, ERA_DATE_PRIMARY, ERA_DATE_SOURCES,
    ERA_DATE_SOURCE_DOC, UNKNOWN, completion_gap_days, component_coverage,
    components_for_row, era_coverage, era_coverage_by_source, era_date_for_row,
    era_for_date, era_for_row, gap_summary, is_interventional, is_not_applicable,
    normalize_study_type, parse_date, parse_year, quantile, tribool,
)

ONE_DAY = timedelta(days=1)


# ---- study type ----------------------------------------------------------
def test_interventional_is_recognised_in_any_casing():
    for raw in ("Interventional", "INTERVENTIONAL", "  interventional  "):
        assert normalize_study_type(raw) == STUDY_TYPE_INTERVENTIONAL, raw
        assert is_interventional(raw) is True, raw


def test_observational_variants_including_bracketed_qualifier():
    # AACT ships 'Observational [Patient Registry]'; prefix matching must fold it in
    # rather than leaving a second observational bucket that looks like a third type.
    for raw in ("Observational", "Observational [Patient Registry]"):
        assert normalize_study_type(raw) == STUDY_TYPE_OBSERVATIONAL, raw
        assert is_interventional(raw) is False, raw


def test_expanded_access_is_named_not_lumped():
    assert normalize_study_type("Expanded Access") == STUDY_TYPE_EXPANDED_ACCESS
    assert is_interventional("Expanded Access") is False


def test_unknown_study_type_is_none_not_false():
    # The distinction the whole module exists to preserve: absent is not negative.
    for raw in (None, "", "   ", float("nan"), "N/A", "nan"):
        assert is_interventional(raw) is None, repr(raw)


def test_unrecognised_type_survives_as_itself_for_the_audit():
    # A genuinely new study_type must reach the audit's tally, not vanish into None.
    novel = "Some Future Type"
    assert normalize_study_type(novel) == novel.lower()
    assert is_interventional(novel) is False


def test_every_known_study_type_is_a_fixed_point_of_normalisation():
    for known in KNOWN_STUDY_TYPES:
        assert normalize_study_type(known) == known, known


# ---- tri-state booleans --------------------------------------------------
def test_native_bools_pass_through():
    assert tribool(True) is True
    assert tribool(False) is False


def test_postgres_and_csv_roundtrip_forms_agree_with_each_other():
    # The same value written by Postgres and re-read from CSV must parse identically,
    # since --from-raw re-derives labels from the CSV dump.
    for truthy in ("t", "T", "true", "True", "TRUE", "yes", "y", "1", 1):
        assert tribool(truthy) is True, repr(truthy)
    for falsy in ("f", "F", "false", "False", "FALSE", "no", "n", "0", 0):
        assert tribool(falsy) is False, repr(falsy)


def test_blank_and_nan_are_unknown_not_false():
    # pandas turns an empty CSV cell into float NaN, which is truthy; a naive parse would
    # read it as True, and a slightly-less-naive one as False. Both are wrong.
    for blank in (None, "", "   ", float("nan"), "nan", "NULL", "NaT"):
        assert tribool(blank) is None, repr(blank)


def test_unexpected_numbers_are_unknown_rather_than_coerced():
    for odd in (2, -1, 0.5):
        assert tribool(odd) is None, repr(odd)


# ---- dates ---------------------------------------------------------------
def test_full_date_parses_to_itself():
    d = FDAAA_RESULTS_EFFECTIVE
    assert parse_date(d.isoformat()) == d
    assert parse_date(d) == d


def test_partial_dates_resolve_to_the_start_of_the_stated_period():
    # Derived from a boundary constant rather than a literal, so the test tracks the
    # module. Month precision -> first of month; year precision -> first of January.
    d = FINAL_RULE_COMPLIANCE
    assert parse_date(f"{d.year:04d}-{d.month:02d}") == date(d.year, d.month, 1)
    assert parse_date(f"{d.year:04d}") == date(d.year, 1, 1)
    assert parse_year(f"{d.year:04d}-{d.month:02d}") == d.year


def test_unparseable_dates_are_none():
    for bad in (None, "", "not a date", "2015-13-01", float("nan")):
        assert parse_date(bad) is None, repr(bad)


# ---- eras: derived from statute, and a genuine partition -----------------
def test_statutory_dates_are_in_the_order_the_statutes_were():
    # FDAAA enacted, then its results obligation a year later, then the Final Rule
    # effective, then its compliance date. Ordering is the invariant; the values are the
    # module's to state.
    assert FDAAA_ENACTED < FDAAA_RESULTS_EFFECTIVE
    assert FDAAA_RESULTS_EFFECTIVE < FINAL_RULE_EFFECTIVE
    assert FINAL_RULE_EFFECTIVE < FINAL_RULE_COMPLIANCE


def test_eras_form_a_contiguous_exhaustive_partition():
    # Structural: open at both ends, each era's end is the next era's start, no gaps and
    # no overlaps. This is what licenses era_for_date never returning None for a date.
    assert ERAS[0][1] is None, "first era must be open at the start"
    assert ERAS[-1][2] is None, "last era must be open at the end"
    for (_n1, _s1, end1), (_n2, start2, _e2) in zip(ERAS, ERAS[1:]):
        assert end1 == start2, "eras must abut exactly"


def test_era_boundaries_are_the_statutory_dates_not_arbitrary_years():
    # The reason the bins are trustworthy: each internal boundary IS a named statutory
    # constant. Transcribing 2008 here would not test this.
    internal_boundaries = {end for _n, _s, end in ERAS if end is not None}
    assert internal_boundaries == {FDAAA_RESULTS_EFFECTIVE, FINAL_RULE_COMPLIANCE}


def test_each_boundary_separates_the_day_before_from_the_day_of():
    # Half-open intervals: the boundary date itself belongs to the LATER era.
    for _name, start, _end in ERAS:
        if start is None:
            continue
        assert era_for_date(start) != era_for_date(start - ONE_DAY), start


def test_every_era_is_reachable_and_documented():
    reached = {era_for_date(b) for _n, b, _e in ERAS if b is not None}
    reached |= {era_for_date(ERAS[0][2] - ONE_DAY)}       # a pre-first-boundary date
    assert reached == {name for name, _s, _e in ERAS}
    assert set(ERA_DOC) == {name for name, _s, _e in ERAS}
    assert all(ERA_DOC[name].strip() for name, _s, _e in ERAS)


def test_unparseable_date_has_no_era():
    assert era_for_date("not a date") is None


# ---- component carriage --------------------------------------------------
def test_every_component_is_documented_and_uniquely_named():
    names = [name for name, _t, _c, _k, _w in FDAAA_COMPONENTS]
    assert len(names) == len(set(names)), "component output names must be unique"
    assert set(FDAAA_COMPONENT_DOC) == set(names)
    assert all(FDAAA_COMPONENT_DOC[n].strip() for n in names)


def test_every_component_declares_a_parser_kind_that_exists():
    for _n, _t, _c, kind, _w in FDAAA_COMPONENTS:
        assert kind in ("tribool", "text", "date"), kind


def test_components_for_row_always_returns_the_full_shape():
    # An absent column must yield an explicit unknown, not a missing key -- otherwise the
    # output CSV grows ragged and "unknown" stops being representable.
    expected = {name for name, _t, _c, _k, _w in FDAAA_COMPONENTS}
    assert set(components_for_row({})) == expected
    assert all(v is None for v in components_for_row({}).values())


def test_components_for_row_preserves_each_declared_kind():
    row = {}
    for _n, _t, column, kind, _w in FDAAA_COMPONENTS:
        row[column] = {"tribool": "t", "text": "Phase 3",
                       "date": FDAAA_RESULTS_EFFECTIVE.isoformat()}[kind]
    got = components_for_row(row)
    for name, _t, _c, kind, _w in FDAAA_COMPONENTS:
        if kind == "tribool":
            assert got[name] is True, name
        elif kind == "date":
            assert got[name] == FDAAA_RESULTS_EFFECTIVE.isoformat(), name
        else:
            assert got[name] == "Phase 3", name


def test_a_declared_false_stays_false_through_carriage():
    # The asymmetry that matters: false must survive, because false and unknown drive
    # different conclusions in the audit.
    tribools = [(n, c) for n, _t, c, k, _w in FDAAA_COMPONENTS if k == "tribool"]
    assert tribools, "expected at least one tri-state component"
    for name, column in tribools:
        assert components_for_row({column: "f"})[name] is False, name


# ---- coverage counting ---------------------------------------------------
def _rows_covering_every_state():
    """One row per tri-state value plus one empty row, built from the spec itself."""
    rows = []
    for token in ("t", "f"):
        rows.append(components_for_row(
            {c: token for _n, _t, c, k, _w in FDAAA_COMPONENTS if k == "tribool"}))
    rows.append(components_for_row({}))
    return rows


def test_coverage_counts_sum_to_the_number_of_rows():
    # Structural and the whole point: nothing may be dropped or double-counted.
    rows = _rows_covering_every_state()
    cov = component_coverage(rows)
    for name, buckets in cov.items():
        assert sum(buckets.values()) == len(rows), name


def test_coverage_reports_every_component():
    cov = component_coverage(_rows_covering_every_state())
    assert set(cov) == {name for name, _t, _c, _k, _w in FDAAA_COMPONENTS}


def test_tristate_components_report_three_states_and_others_do_not():
    # A text field has no meaningful 'false'. It does have a meaningful
    # 'not_applicable', which is a different claim from 'unknown'.
    cov = component_coverage(_rows_covering_every_state())
    for name, _t, _c, kind, _w in FDAAA_COMPONENTS:
        if kind == "tribool":
            assert set(cov[name]) == {"true", "false", UNKNOWN}, name
        else:
            assert set(cov[name]) == {"present", NOT_APPLICABLE, UNKNOWN}, name


def test_explicit_not_applicable_survives_carriage_and_is_counted_apart():
    # The bug the first live run exposed: AACT ships `phase` as 'NA' where no phase
    # applies, and an earlier _text treated 'na' as blank -- so "no phase applies" and
    # "nobody recorded a phase" landed in one bucket. They are different claims and the
    # applicability rule reads them differently, so they must stay separable.
    text_fields = [(n, c) for n, _t, c, k, _w in FDAAA_COMPONENTS if k == "text"]
    assert text_fields, "expected at least one text component"
    for name, column in text_fields:
        carried = components_for_row({column: "NA"})[name]
        assert carried is not None, f"{name}: explicit NA was flattened to absent"
        assert is_not_applicable(carried), name
        cov = component_coverage([components_for_row({column: "NA"}),
                                  components_for_row({column: None})])
        assert cov[name][NOT_APPLICABLE] == 1, name
        assert cov[name][UNKNOWN] == 1, name
        assert cov[name]["present"] == 0, name


def test_not_applicable_recognised_in_its_common_spellings_and_casings():
    for raw in ("NA", "na", "N/A", "n/a", "Not Applicable", "  NA  "):
        assert is_not_applicable(raw) is True, repr(raw)
    for raw in (None, "", "PHASE1", "Phase 3", float("nan")):
        assert is_not_applicable(raw) is False, repr(raw)


def test_a_real_value_is_neither_blank_nor_not_applicable():
    # Guards the fix from over-reaching: uppercase CTG enum values must pass through
    # untouched, since that is the format AACT actually ships now.
    for raw in ("PHASE1", "PHASE1_PHASE2", "EARLY_PHASE1", "PHASE4"):
        assert is_not_applicable(raw) is False, raw
        text_fields = [(n, c) for n, _t, c, k, _w in FDAAA_COMPONENTS if k == "text"]
        for name, column in text_fields:
            assert components_for_row({column: raw})[name] == raw, (name, raw)


def test_study_type_still_treats_na_as_unknown_despite_the_text_change():
    # study_type is the one field where 'N/A' genuinely means unknown, and it handles that
    # itself AFTER _text runs. Loosening _text must not have broken it.
    for raw in ("N/A", "NA", "na"):
        assert normalize_study_type(raw) is None, repr(raw)
        assert is_interventional(raw) is None, repr(raw)


def test_coverage_distinguishes_declared_false_from_absent():
    rows = _rows_covering_every_state()
    cov = component_coverage(rows)
    for name, _t, _c, kind, _w in FDAAA_COMPONENTS:
        if kind != "tribool":
            continue
        # exactly one row said true, one said false, one said nothing
        assert cov[name]["true"] == 1, name
        assert cov[name]["false"] == 1, name
        assert cov[name][UNKNOWN] == len(rows) - 2, name


def test_empty_input_reports_zeros_rather_than_an_empty_dict():
    cov = component_coverage([])
    assert set(cov) == {name for name, _t, _c, _k, _w in FDAAA_COMPONENTS}
    assert all(sum(b.values()) == 0 for b in cov.values())


def test_era_coverage_counts_sum_and_include_every_era_at_zero():
    boundary = FINAL_RULE_COMPLIANCE
    rows = [
        {"primary_completion_date": boundary.isoformat()},
        {"primary_completion_date": (ERAS[0][2] - ONE_DAY).isoformat()},
        {"primary_completion_date": None},
    ]
    cov = era_coverage(rows)
    assert sum(cov.values()) == len(rows)
    assert set(cov) == {name for name, _s, _e in ERAS} | {UNKNOWN}
    assert cov[UNKNOWN] == 1


def test_era_coverage_reads_the_field_it_is_told_to():
    field = "some_other_date"
    rows = [{field: FINAL_RULE_COMPLIANCE.isoformat()}]
    cov = era_coverage(rows, date_field=field)
    assert cov[era_for_date(FINAL_RULE_COMPLIANCE)] == 1
    # and the default field, being absent, must read as unknown rather than erroring
    assert era_coverage(rows)[UNKNOWN] == 1


# ---- cross-module invariant ---------------------------------------------
def test_posting_outcome_fields_are_registered_as_label_inputs():
    # R7. `were_results_reported` is AACT's own results-posted flag: pulled because the
    # audit needs it as an OUTCOME, and therefore barred from any feature matrix. If this
    # fails, the field is being carried without being registered, which is how a leak
    # gets in quietly.
    for field in POSTING_OUTCOME_FIELDS:
        assert field in LABEL_DERIVED_FIELDS, field


def test_no_fdaaa_component_is_a_label_input():
    # The components are legitimate audit inputs AND legitimate features. If one ever
    # lands on the label-input registry, carrying it here becomes a leak and this test is
    # the tripwire.
    for name, _t, _c, _k, _w in FDAAA_COMPONENTS:
        assert name not in LABEL_DERIVED_FIELDS, name


def test_module_computes_no_applicability_verdict():
    # The deliberate absence, asserted so a future session cannot quietly add one and
    # have it slip into the pull unnoticed. Applicability is the audit's call.
    # Exact names, not substrings. The first version matched any name containing
    # "applicab" and false-positived TWICE on innocent helpers -- a private constant and
    # then `is_not_applicable`, which is about whether a FIELD states not-applicable, an
    # entirely different question from whether FDAAA applies to a trial. A tripwire that
    # cries wolf gets switched off, which is worse than not having one, so this checks a
    # precise set of verdict names instead.
    #
    # This test is a narrow backstop, not the real guard. The real guard is the module
    # docstring and review. What it catches is the specific careless case: someone adding
    # an obviously-named applicability function to the pull path.
    import trial_pos.services.population as pop
    verdict_names = {
        "fdaaa_applies", "fdaaa_applicability", "applicability", "is_fdaaa_applicable",
        "fdaaa_required", "requires_posting", "must_post", "posting_required",
        "required_to_post", "is_subject_to_fdaaa", "applicability_for_row",
    }
    present = sorted(n for n in verdict_names
                     if hasattr(pop, n) and callable(getattr(pop, n)))
    assert not present, f"applicability determination leaked into the pull: {present}"


# ---- era date source and the flagged fallback ----------------------------
def test_primary_completion_wins_when_present():
    row = {ERA_DATE_PRIMARY: FDAAA_RESULTS_EFFECTIVE.isoformat(),
           ERA_DATE_FALLBACK: FINAL_RULE_COMPLIANCE.isoformat()}
    d, src = era_date_for_row(row)
    assert src == ERA_DATE_PRIMARY
    assert d == FDAAA_RESULTS_EFFECTIVE
    # and the era follows the primary date, not the later fallback
    assert era_for_row(row)[0] == era_for_date(FDAAA_RESULTS_EFFECTIVE)


def test_fallback_is_used_only_when_primary_is_absent():
    row = {ERA_DATE_PRIMARY: None, ERA_DATE_FALLBACK: FINAL_RULE_COMPLIANCE.isoformat()}
    d, src = era_date_for_row(row, allow_fallback=True)
    assert (d, src) == (FINAL_RULE_COMPLIANCE, ERA_DATE_FALLBACK)


def test_fallback_can_be_switched_off_and_then_yields_absent():
    # The flag must actually change behaviour, or printing its value is theatre.
    row = {ERA_DATE_PRIMARY: None, ERA_DATE_FALLBACK: FINAL_RULE_COMPLIANCE.isoformat()}
    d, src = era_date_for_row(row, allow_fallback=False)
    assert d is None and src == ERA_DATE_ABSENT
    assert era_for_row(row, allow_fallback=False)[0] is None


def test_absent_when_neither_date_parses():
    for row in ({}, {ERA_DATE_PRIMARY: "", ERA_DATE_FALLBACK: None},
                {ERA_DATE_PRIMARY: "not a date", ERA_DATE_FALLBACK: "also not"}):
        assert era_date_for_row(row)[1] == ERA_DATE_ABSENT, row


def test_every_date_source_is_documented():
    assert set(ERA_DATE_SOURCE_DOC) == set(ERA_DATE_SOURCES)
    assert all(ERA_DATE_SOURCE_DOC[s].strip() for s in ERA_DATE_SOURCES)


def test_era_coverage_by_source_is_a_partition_of_the_rows():
    boundary = FINAL_RULE_COMPLIANCE
    rows = [
        {ERA_DATE_PRIMARY: boundary.isoformat(), ERA_DATE_FALLBACK: None},
        {ERA_DATE_PRIMARY: None, ERA_DATE_FALLBACK: boundary.isoformat()},
        {ERA_DATE_PRIMARY: None, ERA_DATE_FALLBACK: None},
    ]
    cov = era_coverage_by_source(rows)
    total = sum(sum(buckets.values()) for buckets in cov.values())
    assert total == len(rows)
    assert set(cov) == set(ERA_DATE_SOURCES)
    assert cov[ERA_DATE_PRIMARY][era_for_date(boundary)] == 1
    assert cov[ERA_DATE_FALLBACK][era_for_date(boundary)] == 1
    assert cov[ERA_DATE_ABSENT][UNKNOWN] == 1


def test_switching_the_fallback_off_moves_rows_to_absent_not_out_of_existence():
    rows = [{ERA_DATE_PRIMARY: None,
             ERA_DATE_FALLBACK: FINAL_RULE_COMPLIANCE.isoformat()}]
    on = era_coverage_by_source(rows, allow_fallback=True)
    off = era_coverage_by_source(rows, allow_fallback=False)
    assert sum(sum(b.values()) for b in on.values()) == len(rows)
    assert sum(sum(b.values()) for b in off.values()) == len(rows)
    assert on[ERA_DATE_FALLBACK][era_for_date(FINAL_RULE_COMPLIANCE)] == 1
    assert off[ERA_DATE_ABSENT][UNKNOWN] == 1


# ---- gap diagnostics ----------------------------------------------------
def test_gap_is_the_calendar_difference_and_signed():
    # Derived from the calendar, not transcribed: build the second date by adding a known
    # number of days to the first, then require the gap to equal that number.
    for offset in (0, 1, 365, 1000):
        row = {ERA_DATE_PRIMARY: FDAAA_RESULTS_EFFECTIVE.isoformat(),
               ERA_DATE_FALLBACK: (FDAAA_RESULTS_EFFECTIVE
                                   + timedelta(days=offset)).isoformat()}
        assert completion_gap_days(row) == offset, offset
    # negative is preserved, because overall-before-primary is a registry error worth
    # counting rather than clamping to zero
    row = {ERA_DATE_PRIMARY: FDAAA_RESULTS_EFFECTIVE.isoformat(),
           ERA_DATE_FALLBACK: (FDAAA_RESULTS_EFFECTIVE - timedelta(days=5)).isoformat()}
    assert completion_gap_days(row) == -5


def test_gap_is_none_when_either_date_is_missing():
    assert completion_gap_days({ERA_DATE_PRIMARY: "2015-01-01"}) is None
    assert completion_gap_days({ERA_DATE_FALLBACK: "2015-01-01"}) is None
    assert completion_gap_days({}) is None


def test_quantile_returns_an_observed_value_and_respects_the_ends():
    values = list(range(11))          # 0..10
    assert quantile(values, 0.0) == values[0]
    assert quantile(values, 1.0) == values[-1]
    # nearest-rank must never invent a value between observations
    for q in (0.1, 0.25, 0.5, 0.75, 0.9):
        assert quantile(values, q) in values, q
    # monotone in q
    got = [quantile(values, q) for q in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert got == sorted(got)


def test_quantile_of_a_symmetric_range_is_its_midpoint():
    # Closed form: for 0..2k the nearest-rank median is k.
    for k in (1, 5, 50):
        assert quantile(list(range(2 * k + 1)), 0.5) == k


def test_quantile_rejects_an_out_of_range_q():
    for bad in (-0.1, 1.1):
        try:
            quantile([1, 2, 3], bad)
        except ValueError:
            continue
        raise AssertionError(f"quantile accepted q={bad}")


def test_quantile_of_empty_is_none():
    assert quantile([], 0.5) is None


def test_gap_summary_counts_only_comparable_rows():
    offsets = [0, 10, 20, 30, 40]
    rows = [{ERA_DATE_PRIMARY: FDAAA_RESULTS_EFFECTIVE.isoformat(),
             ERA_DATE_FALLBACK: (FDAAA_RESULTS_EFFECTIVE
                                 + timedelta(days=o)).isoformat()} for o in offsets]
    rows.append({ERA_DATE_PRIMARY: None, ERA_DATE_FALLBACK: "2015-01-01"})
    s = gap_summary(rows)
    assert s["n_comparable"] == len(offsets)
    assert s["min"] == min(offsets)
    assert s["max"] == max(offsets)
    assert s["n_negative"] == 0
    assert s["q50"] == quantile(sorted(offsets), 0.5)


def test_gap_summary_reports_negatives_separately():
    rows = [{ERA_DATE_PRIMARY: FDAAA_RESULTS_EFFECTIVE.isoformat(),
             ERA_DATE_FALLBACK: (FDAAA_RESULTS_EFFECTIVE
                                 + timedelta(days=o)).isoformat()}
            for o in (-3, -1, 5)]
    s = gap_summary(rows)
    assert s["n_negative"] == 2
    assert s["min"] == -3


def test_gap_summary_of_empty_is_shaped_not_missing():
    s = gap_summary([])
    assert s["n_comparable"] == 0
    assert s["min"] is None and s["max"] is None
    assert "q50" in s