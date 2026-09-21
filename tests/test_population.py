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
    DAYS_PER_YEAR_NOMINAL, DATE_OK, DATE_QUALITIES, DATE_QUALITY_DOC, DATE_TOO_EARLY,
    DATE_TOO_LATE, DATE_TYPES, DATE_TYPE_ACTUAL, DATE_TYPE_ANTICIPATED,
    DATE_TYPE_DOC, DATE_TYPE_ESTIMATED, DATE_UNPARSEABLE, PLANNED_DATE_TYPES,
    is_planned_date,
    DEFAULT_MIN_TRIAL_DATE, DRUG_INTERVENTION_TYPES, DRUG_SIGNALS, DRUG_SIGNAL_DOC,
    INTERVENTION_TYPE_SEP, date_quality, date_quality_coverage, drug_signal_agreement,
    drug_trial_signals, has_drug_intervention, is_actual_date, normalize_date_type,
    parse_intervention_types,
    ERA_DATE_ABSENT, ERA_DATE_FALLBACK, ERA_DATE_PRIMARY, ERA_DATE_SOURCES,
    ERA_DATE_SOURCE_DOC, UNKNOWN, completion_gap_days, component_coverage,
    components_for_row, era_coverage, era_coverage_by_source, era_date_for_row,
    era_for_date, era_for_row, gap_summary, is_interventional, is_not_applicable,
    normalize_study_type, parse_date, parse_year, quantile, tribool,
    ADVANCED_THERAPY_TYPES, AGENCY_CLASSES, COMBINATION_PRODUCT_TYPES,
    MODALITY_SIGNALS, SPONSOR_COLS, entity_coverage, has_advanced_therapy,
    is_drug_like_modality, is_known_agency_class, is_known_responsible_party_type,
    modality_signals, normalize_agency_class, normalize_responsible_party_type,
    parse_agency_classes, sponsor_agreement, sponsor_signals,
    CONDITION_FIELDS, DRUG_NAME_FIELDS, ENTITY_COVERAGE_FIELDS, JOINT_MESH_FIELDS,
    empty_entity_coverage, merge_entity_coverage,
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


# ---- date type ----------------------------------------------------------
def test_date_type_recognised_in_any_casing():
    assert is_actual_date("Actual") is True
    assert is_actual_date("ACTUAL") is True
    assert is_actual_date("Anticipated") is False
    assert normalize_date_type("anticipated") == DATE_TYPE_ANTICIPATED


def test_unknown_date_type_is_none_not_anticipated():
    # A gap in an old record is not a positive statement that the date has not happened.
    # Treating the two alike would either leak unfinished trials into training or discard
    # usable ones.
    for raw in (None, "", "   ", float("nan")):
        assert is_actual_date(raw) is None, repr(raw)


def test_every_date_type_is_a_fixed_point_and_documented():
    for known in DATE_TYPES:
        assert normalize_date_type(known) == known, known
    assert set(DATE_TYPE_DOC) == set(DATE_TYPES)
    assert all(DATE_TYPE_DOC[t].strip() for t in DATE_TYPES)


def test_unrecognised_date_type_survives_for_the_audit():
    assert normalize_date_type("Estimated") == "estimated"


# ---- date plausibility --------------------------------------------------
def test_date_quality_accepts_a_date_inside_the_window():
    as_of = FINAL_RULE_COMPLIANCE
    assert date_quality(as_of.isoformat(), as_of) == DATE_OK


def test_the_floor_separates_the_day_before_from_the_day_of():
    # Derived from the constant, not a transcribed year.
    as_of = FINAL_RULE_COMPLIANCE
    floor = DEFAULT_MIN_TRIAL_DATE
    assert date_quality(floor.isoformat(), as_of, min_date=floor) == DATE_OK
    assert date_quality((floor - ONE_DAY).isoformat(), as_of,
                        min_date=floor) == DATE_TOO_EARLY


def test_the_future_horizon_separates_the_last_allowed_day_from_the_next():
    # Built by adding the bound to as_of, so the test tracks whatever the bound is.
    as_of = FINAL_RULE_COMPLIANCE
    horizon = 30
    last_ok = as_of + timedelta(days=horizon)
    assert date_quality(last_ok.isoformat(), as_of,
                        max_future_days=horizon) == DATE_OK
    assert date_quality((last_ok + ONE_DAY).isoformat(), as_of,
                        max_future_days=horizon) == DATE_TOO_LATE


def test_the_87_year_gap_from_the_live_run_is_caught():
    # The concrete failure that motivated these bounds: a real pulled row whose date sat
    # 31,777 days out. Derived from the default horizon, not hardcoded as "2100".
    as_of = FINAL_RULE_COMPLIANCE
    absurd = as_of + timedelta(days=31777)
    assert date_quality(absurd.isoformat(), as_of) == DATE_TOO_LATE


def test_a_plausible_anticipated_date_is_not_rejected():
    # Anticipated dates legitimately sit in the future; only absurd ones are errors.
    as_of = FINAL_RULE_COMPLIANCE
    soon = as_of + timedelta(days=DAYS_PER_YEAR_NOMINAL)
    assert date_quality(soon.isoformat(), as_of) == DATE_OK


def test_unparseable_is_its_own_quality_not_an_implausible_one():
    as_of = FINAL_RULE_COMPLIANCE
    for bad in (None, "", "not a date"):
        assert date_quality(bad, as_of) == DATE_UNPARSEABLE, repr(bad)


def test_date_quality_does_not_read_the_clock():
    # Same input, two different as_of values, different verdicts -> the function depends
    # on the injected date and nothing else. A date.today() call would make this pass by
    # accident on some days and fail on others.
    target = FINAL_RULE_COMPLIANCE + timedelta(days=1000)
    near = FINAL_RULE_COMPLIANCE + timedelta(days=999)
    assert date_quality(target.isoformat(), near, max_future_days=1) == DATE_OK
    assert date_quality(target.isoformat(), FINAL_RULE_COMPLIANCE,
                        max_future_days=1) == DATE_TOO_LATE


def test_every_quality_is_documented():
    assert set(DATE_QUALITY_DOC) == set(DATE_QUALITIES)
    assert all(DATE_QUALITY_DOC[q].strip() for q in DATE_QUALITIES)


def test_date_quality_coverage_counts_sum_per_field():
    as_of = FINAL_RULE_COMPLIANCE
    fields = ["d1", "d2"]
    rows = [{"d1": as_of.isoformat(), "d2": None},
            {"d1": "not a date", "d2": (as_of - ONE_DAY).isoformat()},
            {}]
    cov = date_quality_coverage(rows, fields, as_of)
    assert set(cov) == set(fields)
    for f in fields:
        assert sum(cov[f].values()) == len(rows), f
        assert set(cov[f]) == set(DATE_QUALITIES), f


# ---- drug-trial signals -------------------------------------------------
def test_drug_and_biological_both_count_as_drug_trials():
    for t in sorted(DRUG_INTERVENTION_TYPES):
        assert has_drug_intervention(t) is True, t


def test_a_mixed_trial_counts_as_a_drug_trial():
    # Any drug-like member is enough; a drug-plus-device trial still has a mechanism.
    mixed = INTERVENTION_TYPE_SEP.join(["Device", sorted(DRUG_INTERVENTION_TYPES)[0]])
    assert has_drug_intervention(mixed) is True


def test_a_purely_non_drug_trial_is_false():
    assert has_drug_intervention(
        INTERVENTION_TYPE_SEP.join(["Device", "Behavioral"])) is False


def test_no_recorded_interventions_is_unknown_not_false():
    # An incomplete registration is not a declared non-drug trial.
    for raw in (None, "", "   ", float("nan"), INTERVENTION_TYPE_SEP):
        assert has_drug_intervention(raw) is None, repr(raw)


def test_intervention_types_are_normalised_deduplicated_and_ordered():
    raw = INTERVENTION_TYPE_SEP.join([" Drug ", "DRUG", "Device"])
    parsed = parse_intervention_types(raw)
    assert parsed == tuple(sorted(set(parsed)))
    assert len(parsed) == 2


def test_phase_na_reads_as_not_drug_like_while_absent_phase_reads_unknown():
    # The distinction the NA fix restored, now load-bearing for scoping.
    assert drug_trial_signals({"phase": "NA"})["phase_is_drug_like"] is False
    assert drug_trial_signals({"phase": "PHASE2"})["phase_is_drug_like"] is True
    assert drug_trial_signals({"phase": None})["phase_is_drug_like"] is None


def test_every_drug_signal_is_produced_and_documented():
    sig = drug_trial_signals({})
    assert set(sig) == set(DRUG_SIGNALS)
    assert set(DRUG_SIGNAL_DOC) == set(DRUG_SIGNALS)
    assert all(DRUG_SIGNAL_DOC[s].strip() for s in DRUG_SIGNALS)
    assert all(v is None for v in sig.values()), "empty row must be all-unknown"


def test_is_drug_trial_uses_only_the_authoritative_signal():
    # It must NOT be a blend. A row whose phase and FDA flag both say drug, but whose
    # interventions say device only, is False -- one stated meaning, re-derivable.
    row = drug_trial_signals({
        "intervention_types": "Device",
        "phase": "PHASE3",
        "is_fda_regulated_drug": "t",
    })
    assert row["is_drug_trial"] is False
    assert row["phase_is_drug_like"] is True
    assert row["is_fda_regulated_drug"] is True


def test_signal_agreement_counts_partition_the_rows():
    rows = [drug_trial_signals(r) for r in (
        {"intervention_types": "Drug", "phase": "PHASE1", "is_fda_regulated_drug": "t"},
        {"intervention_types": "Device", "phase": "NA", "is_fda_regulated_drug": "f"},
        {},
    )]
    out = drug_signal_agreement(rows)
    for s in DRUG_SIGNALS:
        assert sum(out["counts"][s].values()) == len(rows), s
    for key, buckets in out["pairs"].items():
        assert sum(buckets.values()) == len(rows), key


def test_signal_agreement_detects_a_real_disagreement():
    rows = [drug_trial_signals({"intervention_types": "Device", "phase": "PHASE3",
                                "is_fda_regulated_drug": "t"})]
    out = drug_signal_agreement(rows)
    key = "is_drug_trial__vs__phase_is_drug_like"
    assert out["pairs"][key]["disagree"] == 1
    assert out["pairs"][key]["agree"] == 0


def test_unknown_signals_are_not_comparable_rather_than_disagreeing():
    rows = [drug_trial_signals({"phase": "PHASE3"})]      # interventions unknown
    out = drug_signal_agreement(rows)
    key = "is_drug_trial__vs__phase_is_drug_like"
    assert out["pairs"][key]["not_comparable"] == 1
    assert out["pairs"][key]["disagree"] == 0


# ---- date type vocabulary, corrected from real data ---------------------
def test_estimated_is_recognised_because_it_is_what_the_server_reports():
    # The live AACT server reports 'Estimated' and never 'Anticipated'. The first version
    # of DATE_TYPES omitted it.
    assert normalize_date_type("Estimated") == DATE_TYPE_ESTIMATED
    assert is_actual_date("Estimated") is False
    assert is_planned_date("Estimated") is True


def test_every_planned_type_is_a_known_type_and_is_not_actual():
    # Structural: the planned set must be a subset of the vocabulary, and no member of it
    # may read as actual.
    assert PLANNED_DATE_TYPES <= set(DATE_TYPES)
    for planned in sorted(PLANNED_DATE_TYPES):
        assert is_actual_date(planned) is False, planned
        assert is_planned_date(planned) is True, planned


def test_actual_is_the_only_non_planned_known_type():
    non_planned = set(DATE_TYPES) - PLANNED_DATE_TYPES
    assert non_planned == {DATE_TYPE_ACTUAL}


def test_is_actual_is_not_the_negation_of_is_planned():
    # The asymmetry that protects the split from a vocabulary gap: an unrecognised type is
    # not actual (so it can never be trained on as a completed event) and also not
    # confirmed planned (so it is not discarded on a guess).
    novel = "Provisional"
    assert normalize_date_type(novel) == novel.lower()
    assert is_actual_date(novel) is False
    assert is_planned_date(novel) is None


def test_unknown_type_is_none_for_both_predicates():
    for raw in (None, "", "   ", float("nan")):
        assert is_actual_date(raw) is None, repr(raw)
        assert is_planned_date(raw) is None, repr(raw)

# ===========================================================================
# MODALITY signals -- carried beside is_drug_trial, never folded into it
# ===========================================================================
def test_advanced_therapy_detected_without_a_drug_row():
    # the case that matters: 3,629 trials carry genetic with no drug or biological, and
    # under is_drug_trial they read False and vanish from the population
    assert has_advanced_therapy("genetic") is True
    assert has_drug_intervention("genetic") is False


def test_modality_signals_do_not_change_is_drug_trial():
    # structural: the whole point of carrying these separately is that the definition
    # everything else rests on does not move
    for raw in ("genetic", "combination_product", "genetic|combination_product",
                "device", "drug", "biological"):
        expected = bool(set(parse_intervention_types(raw)) & DRUG_INTERVENTION_TYPES)
        assert has_drug_intervention(raw) == expected, raw


def test_drug_intervention_types_was_not_widened():
    # if a future edit folds the edge cases in, this fails loudly rather than silently
    # moving the scoping decision, the agreement cross-tabs and the population count
    assert not (DRUG_INTERVENTION_TYPES
                & (ADVANCED_THERAPY_TYPES | COMBINATION_PRODUCT_TYPES))


def test_wider_modality_is_the_union_of_the_three_sets():
    # derived from the constants, so changing any set cannot leave this asserting a
    # stale membership list
    for member in (DRUG_INTERVENTION_TYPES | ADVANCED_THERAPY_TYPES
                   | COMBINATION_PRODUCT_TYPES):
        assert is_drug_like_modality(member) is True
    assert is_drug_like_modality("device") is False


def test_modality_signals_are_tristate():
    # no intervention rows at all is UNKNOWN, not False: absent is not a claim
    for signal in modality_signals({"intervention_types": None}).values():
        assert signal is None
    for signal in modality_signals({}).values():
        assert signal is None


def test_modality_signals_cover_every_declared_column():
    assert set(modality_signals({"intervention_types": "drug"})) == set(MODALITY_SIGNALS)


# ===========================================================================
# SPONSOR CLASS and RESPONSIBLE PARTY
# ===========================================================================
def test_agency_class_normalised_to_a_token():
    assert normalize_agency_class("INDUSTRY") == "industry"
    assert normalize_agency_class("Other gov") == "other_gov"


def test_unrecognised_agency_class_passes_through_as_itself():
    # lesson 17: mapping it to None would hide a registry vocabulary change, exactly as
    # 'Estimated' would have been hidden had normalize_date_type returned None
    token = normalize_agency_class("SOVEREIGN_WEALTH_FUND")
    assert token == "sovereign_wealth_fund"
    assert token not in AGENCY_CLASSES


def test_unrecognised_class_is_flagged_rather_than_dropped():
    assert is_known_agency_class("INDUSTRY") is True
    assert is_known_agency_class("SOVEREIGN_WEALTH_FUND") is False
    assert is_known_agency_class("") is None          # absent makes no claim


def test_collaborator_classes_are_a_set_not_a_single_value():
    # a trial can have several collaborators of different classes
    assert parse_agency_classes("NIH|INDUSTRY|NIH") == ("industry", "nih")


def test_empty_collaborators_and_absent_lead_are_both_empty_tuples():
    # the two mean different things ("no collaborators" vs "lead not recorded") and are
    # distinguished by WHICH column is empty, not by this function
    assert parse_agency_classes(None) == ()
    assert parse_agency_classes("") == ()


def test_sponsor_signals_cover_every_declared_column():
    row = {"lead_sponsor_class": "INDUSTRY", "collaborator_classes": "NIH",
           "responsible_party_type": "Sponsor"}
    assert set(sponsor_signals(row)) == set(SPONSOR_COLS)


def test_multiple_leads_are_kept_not_silently_reduced_to_one():
    # picking one would hide the multiplicity; keeping both makes it visible in the audit
    out = sponsor_signals({"lead_sponsor_class": "INDUSTRY|NIH"})
    assert out["lead_sponsor_class"] == "industry|nih"


def test_responsible_party_type_normalised_and_flagged():
    assert (normalize_responsible_party_type("Principal Investigator")
            == "principal investigator")
    assert is_known_responsible_party_type("Sponsor") is True
    assert is_known_responsible_party_type("Data Monitoring Committee") is False
    assert is_known_responsible_party_type(None) is None


def test_sponsor_agreement_is_paired_not_marginal():
    # lesson 19: two fields whose totals match can still disagree on every row. Only the
    # joint distribution shows it, so this must return pairs.
    rows = [{"lead_sponsor_class": "industry", "responsible_party_type": "sponsor"},
            {"lead_sponsor_class": "nih", "responsible_party_type": "principal investigator"},
            {"lead_sponsor_class": "industry", "responsible_party_type": "principal investigator"}]
    out = sponsor_agreement(rows)
    assert sum(out["joint"].values()) == len(rows)
    assert out["joint"][("industry", "sponsor")] == 1
    assert out["joint"][("industry", "principal investigator")] == 1
    # the marginals would have been industry:2 / nih:1 either way; the pairing is the point
    assert len(out["joint"]) == 3


def test_sponsor_agreement_separates_the_four_coverage_cases():
    rows = [{"lead_sponsor_class": "industry", "responsible_party_type": "sponsor"},
            {"lead_sponsor_class": "industry"},
            {"responsible_party_type": "sponsor"},
            {}]
    out = sponsor_agreement(rows)
    assert out["coverage"] == {"both": 1, "lead_only": 1, "party_only": 1, "neither": 1}
    assert sum(out["coverage"].values()) == len(rows)


# ---- entity coverage ------------------------------------------------------
def test_entity_coverage_reports_each_source_separately():
    # pooling them would hide that one source covers 90% of trials and another 12%, which
    # is exactly the mistake of picking one name source and finding out later
    rows = [{"intervention_names": "Drug A"},
            {"intervention_other_names": "ABC-123"},
            {"intervention_names": "Drug B", "intervention_mesh_terms": "Aspirin"},
            {}]
    out = entity_coverage(rows)
    assert out["total"] == len(rows)
    assert out["present"]["intervention_names"] == 2
    assert out["present"]["intervention_other_names"] == 1
    assert out["present"]["intervention_mesh_terms"] == 1


def test_entity_coverage_any_drug_source_is_a_union_not_a_sum():
    # a trial with two name sources must count ONCE toward the union, or the reported
    # feasibility ceiling for the market target is inflated
    rows = [{"intervention_names": "Drug B", "intervention_mesh_terms": "Aspirin"}]
    out = entity_coverage(rows)
    assert out["any_drug_name_source"] == 1
    assert sum(out["present"][f] for f in
               ("intervention_names", "intervention_other_names",
                "intervention_mesh_terms")) == 2


def test_entity_coverage_blank_is_absent_not_present():
    out = entity_coverage([{"intervention_names": ""}, {"intervention_names": None}])
    assert out["present"]["intervention_names"] == 0


# ---- entity coverage must be mergeable across chunks ----------------------
# The pull streams to bound memory, so coverage is tallied per chunk. Addition therefore
# has to be defined in tested code rather than done inline in the script.
def test_merge_is_additive_on_every_field():
    a = entity_coverage([{"intervention_names": "x"}, {}])
    b = entity_coverage([{"intervention_names": "y", "condition_mesh_terms": "z"}])
    merged = merge_entity_coverage(a, b)
    assert merged["total"] == a["total"] + b["total"]
    for field in a["present"]:
        assert merged["present"][field] == a["present"][field] + b["present"][field]
    assert merged["any_drug_name_source"] == (a["any_drug_name_source"]
                                              + b["any_drug_name_source"])


def test_empty_is_the_additive_identity():
    one = entity_coverage([{"intervention_mesh_terms": "Aspirin"}])
    assert merge_entity_coverage(empty_entity_coverage(), one) == one


def test_empty_field_set_comes_from_the_declaration_not_the_first_chunk():
    # otherwise a first chunk missing a column would fix a short field set for the run
    assert set(empty_entity_coverage()["present"]) == set(ENTITY_COVERAGE_FIELDS)


def test_merging_mismatched_field_sets_raises_rather_than_unioning():
    # a silent union would give one column a different denominator from its neighbours,
    # making two percentages in the same table incomparable
    good = empty_entity_coverage()
    bad = {"total": 1, "present": {"intervention_names": 1}, "any_drug_name_source": 1}
    try:
        merge_entity_coverage(good, bad)
    except ValueError:
        return
    raise AssertionError("mismatched field sets must raise")


def test_chunked_merge_equals_the_whole_in_one_go():
    # the property that makes per-chunk tallying safe at all
    rows = [{"intervention_names": "a"}, {"condition_mesh_terms": "b"},
            {"intervention_other_names": "c", "intervention_names": "d"}, {}]
    whole = entity_coverage(rows)
    chunked = empty_entity_coverage()
    for i in range(0, len(rows), 2):
        chunked = merge_entity_coverage(chunked, entity_coverage(rows[i:i + 2]))
    assert chunked == whole


def test_drug_name_union_uses_the_declared_source_list():
    # the union was hand-listed at first; deriving it means a fourth source cannot be
    # added to the per-source table and forgotten in the ceiling figure
    row = {f: "value" for f in DRUG_NAME_FIELDS}
    out = entity_coverage([row])
    assert out["any_drug_name_source"] == 1
    assert all(out["present"][f] == 1 for f in DRUG_NAME_FIELDS)
    assert all(out["present"][f] == 0 for f in CONDITION_FIELDS)


# ---- entity coverage: the DRUG-TRIAL stratum and the joint MeSH ceiling ---
# The first widened pull reported "ANY drug-name source present: 100.0%" and that number
# carried no information: intervention_names is free text present on essentially every
# trial, including 'Placebo' and 'Standard of care'. Presence is not resolvability, and a
# percentage over a population that is half device and behavioural trials answers a
# question nobody asked once scoping is drug-only.
def test_coverage_is_reported_for_the_drug_trial_stratum():
    rows = [{"intervention_names": "a", "is_drug_trial": True},
            {"intervention_names": "b", "is_drug_trial": False},
            {"intervention_names": "c", "is_drug_trial": None}]
    out = entity_coverage(rows)
    assert out["total"] == 3
    assert out["drug_total"] == 1
    assert out["present"]["intervention_names"] == 3
    assert out["drug_present"]["intervention_names"] == 1


def test_unknown_is_drug_trial_is_not_counted_as_a_drug_trial():
    # tri-state discipline: absent is not a claim, so it must not inflate the denominator
    # the market gate is read against
    out = entity_coverage([{"intervention_names": "a", "is_drug_trial": None},
                           {"intervention_names": "b"}])
    assert out["drug_total"] == 0


def test_joint_mesh_is_an_intersection_not_a_minimum_of_two_rates():
    # two trials each carrying ONE MeSH side means the join can reach neither, even though
    # each side individually reads 50%. Inferring the joint from two percentages would
    # have said 50%.
    rows = [{"intervention_mesh_terms": "m", "is_drug_trial": True},
            {"condition_mesh_terms": "c", "is_drug_trial": True}]
    out = entity_coverage(rows)
    assert out["present"]["intervention_mesh_terms"] == 1
    assert out["present"]["condition_mesh_terms"] == 1
    assert out["both_mesh"] == 0
    assert out["drug_both_mesh"] == 0


def test_joint_mesh_counts_a_trial_carrying_both_sides():
    out = entity_coverage([{f: "v" for f in JOINT_MESH_FIELDS} | {"is_drug_trial": True}])
    assert out["both_mesh"] == 1 and out["drug_both_mesh"] == 1


def test_joint_mesh_never_exceeds_either_side():
    # structural: an intersection is bounded by both of its parts, in each stratum
    rows = [{"intervention_mesh_terms": "m", "condition_mesh_terms": "c",
             "is_drug_trial": True},
            {"intervention_mesh_terms": "m", "is_drug_trial": True},
            {"condition_mesh_terms": "c", "is_drug_trial": False}]
    out = entity_coverage(rows)
    for side in JOINT_MESH_FIELDS:
        assert out["both_mesh"] <= out["present"][side]
        assert out["drug_both_mesh"] <= out["drug_present"][side]


def test_drug_stratum_never_exceeds_the_whole():
    rows = [{f: "v" for f in ENTITY_COVERAGE_FIELDS} | {"is_drug_trial": True},
            {f: "v" for f in ENTITY_COVERAGE_FIELDS} | {"is_drug_trial": False}]
    out = entity_coverage(rows)
    assert out["drug_total"] <= out["total"]
    for field in ENTITY_COVERAGE_FIELDS:
        assert out["drug_present"][field] <= out["present"][field]
    assert out["drug_any_drug_name_source"] <= out["any_drug_name_source"]
    assert out["drug_both_mesh"] <= out["both_mesh"]


def test_chunked_merge_still_equals_the_whole_with_strata():
    rows = [{"intervention_names": "a", "is_drug_trial": True},
            {"intervention_mesh_terms": "m", "condition_mesh_terms": "c",
             "is_drug_trial": True},
            {"condition_mesh_terms": "c", "is_drug_trial": False},
            {"is_drug_trial": None}]
    whole = entity_coverage(rows)
    chunked = empty_entity_coverage()
    for i in range(0, len(rows), 3):
        chunked = merge_entity_coverage(chunked, entity_coverage(rows[i:i + 3]))
    assert chunked == whole


def test_empty_carries_every_stratified_key():
    # otherwise the first chunk fixes the shape and a later merge raises or drops a key
    empty = empty_entity_coverage()
    populated = entity_coverage([{"is_drug_trial": True}])
    assert set(empty) == set(populated)
    assert set(empty["drug_present"]) == set(ENTITY_COVERAGE_FIELDS)