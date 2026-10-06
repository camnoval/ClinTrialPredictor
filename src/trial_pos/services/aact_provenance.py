"""Per-column provenance for AACT fields: could this value have been known at registration?

Every field pulled for features carries one of three provenances, decided here and nowhere
else. The feature builder gates on it; this module only classifies.

THREE STATES, NOT TWO
=====================
AACT is a CURRENT SNAPSHOT. A registration-type field holds its latest edit, not the value
at registration, and ClinicalTrials.gov's version history is not in AACT. So "knowable at
registration" cannot be asserted for any field from AACT alone. The line drawn instead is
whether the registry workflow overwrites the field AS A MATTER OF COURSE while the trial
runs:

  registration            describes the protocol. Edits are possible (amendments) but
                          not systematic, and not routinely tied to how the trial went.
  editable_current_value  registration-type, but systematically updated during conduct:
                          estimated dates becoming actual, sites added and closed, outcome
                          lists revised at results posting. The current value may encode
                          what happened.
  post_hoc                exists only because the trial ran, or is derived from its
                          conduct or results. actual_duration is the type case: a trial
                          stopped for futility is short, so duration encodes failure.

`enrollment` is post_hoc, not editable: at completion it is overwritten with the actual
count and the planned figure is not retained, and a trial stopped early enrolls fewer.

RETROSPECTIVE REGISTRATION IS A ROW-LEVEL OVERRIDE
==================================================
Column provenance assumes the record was written before the trial ran. A trial first
submitted after its primary completion wrote every "registration" field knowing the
outcome. That is a property of the ROW, which no column classification can express, so
the feature builder must apply it on top: see RETROSPECTIVE_REGISTRATION.

CARDINALITY IS DECLARED BEFORE IT IS MEASURED
=============================================
Each table states whether it should have at most one row per trial. The probe measures it
and reports contradictions, so a one-to-many table assumed one-to-one fails loudly rather
than multiplying rows three steps downstream (lesson 62, ledger 18).
"""
from __future__ import annotations

from typing import Iterable, NamedTuple, Optional

from trial_pos.services.endpoint_label import LABEL_DERIVED_FIELDS

PROVENANCE_REGISTRATION = "registration"
PROVENANCE_EDITABLE = "editable_current_value"
PROVENANCE_POST_HOC = "post_hoc"
PROVENANCES = (PROVENANCE_REGISTRATION, PROVENANCE_EDITABLE, PROVENANCE_POST_HOC)

PROVENANCE_DOC = {
    PROVENANCE_REGISTRATION: "describes the protocol; edits possible but not systematic",
    PROVENANCE_EDITABLE: ("registration-type but systematically updated during conduct, "
                          "so the current value may encode what happened"),
    PROVENANCE_POST_HOC: "exists only because the trial ran, or derives from its results",
}

CARDINALITY_ONE = "at_most_one_per_trial"
CARDINALITY_MANY = "many_per_trial"
CARDINALITIES = (CARDINALITY_ONE, CARDINALITY_MANY)

VERDICT_CONSISTENT = "consistent"
VERDICT_CONTRADICTED = "contradicted"

# Keys, never classified: they identify rows rather than describe trials.
KEY_COLUMNS = frozenset({"id", "nct_id"})

# Row-level: registration-provenance fields of such a trial are post hoc. Named so the
# feature builder and the audit agree on the comparison; the rule itself is not built
# until the pull carries the first-submitted date and the share is measured.
RETROSPECTIVE_REGISTRATION = ("study_first_submitted_date", "primary_completion_date")

_R, _E, _P = PROVENANCE_REGISTRATION, PROVENANCE_EDITABLE, PROVENANCE_POST_HOC


class TableSpec(NamedTuple):
    table: str
    cardinality: str
    columns: dict          # column -> provenance
    notes: dict            # column -> why, where the provenance is not obvious


TABLES: tuple[TableSpec, ...] = (
    TableSpec("studies", CARDINALITY_ONE, {
        "study_type": _R, "phase": _R, "number_of_arms": _R, "number_of_groups": _R,
        "is_fda_regulated_drug": _R, "is_fda_regulated_device": _R, "is_us_export": _R,
        "brief_title": _R, "official_title": _R, "acronym": _R, "source": _R,
        "study_first_submitted_date": _R, "study_first_submitted_qc_date": _R,
        "study_first_posted_date": _R, "study_first_posted_date_type": _R,
        "source_class": _R, "has_dmc": _R, "target_duration": _R, "patient_registry": _R,
        "biospec_retention": _R, "biospec_description": _R,
        "is_ppsd": _R, "is_unapproved_device": _R,
        "has_expanded_access": _E, "expanded_access_nctid": _E,
        "expanded_access_status_for_nctid": _E, "expanded_access_type_individual": _E,
        "expanded_access_type_intermediate": _E, "expanded_access_type_treatment": _E,
        "plan_to_share_ipd": _E, "plan_to_share_ipd_description": _E,
        "ipd_time_frame": _E, "ipd_access_criteria": _E, "ipd_url": _E,
        "verification_date": _E, "verification_month_year": _E,
        "start_date": _E, "start_date_type": _E, "start_month_year": _E,
        "enrollment": _P, "enrollment_type": _P,
        "primary_completion_date": _P, "primary_completion_date_type": _P,
        "primary_completion_month_year": _P,
        "completion_date": _P, "completion_date_type": _P, "completion_month_year": _P,
        "overall_status": _P, "why_stopped": _P, "last_known_status": _P,
        "results_first_submitted_date": _P, "results_first_posted_date": _P,
        "results_first_submitted_qc_date": _P, "results_first_posted_date_type": _P,
        "disposition_first_submitted_date": _P, "disposition_first_submitted_qc_date": _P,
        "disposition_first_posted_date": _P, "disposition_first_posted_date_type": _P,
        "last_update_posted_date": _P, "last_update_posted_date_type": _P,
        "last_update_submitted_date": _P, "last_update_submitted_qc_date": _P,
        "baseline_population": _P, "baseline_type_units_analyzed": _P,
        "limitations_and_caveats": _P, "delayed_posting": _P, "fdaaa801_violation": _P,
        "created_at": _P, "updated_at": _P, "nlm_download_date_description": _P,
    }, {
        "study_first_submitted_date": ("registration-time for the TRIAL, but a trial "
                                       "registered after it completed wrote every "
                                       "registration field post hoc: see "
                                       "RETROSPECTIVE_REGISTRATION"),
        "last_known_status": ("non-null exactly when overall_status is UNKNOWN, so its "
                              "presence encodes a label input"),
        "results_first_submitted_qc_date": "non-null exactly when results were posted",
        "verification_date": ("re-verified as a matter of course; a stale date marks an "
                              "abandoned record"),
        "created_at": "AACT load metadata, not a property of the trial",
        "has_expanded_access": "expanded access is often added once a drug looks promising",
        "enrollment": "overwritten with the actual count; the planned figure is lost",
        "enrollment_type": "ACTUAL versus ESTIMATED is itself a fact about conduct",
        "primary_completion_date": "an early stop moves it; it encodes duration",
    }),
    TableSpec("designs", CARDINALITY_ONE, {
        "allocation": _R, "intervention_model": _R, "observational_model": _R,
        "primary_purpose": _R, "time_perspective": _R, "masking": _R,
        "masking_description": _R, "intervention_model_description": _R,
        "subject_masked": _R, "caregiver_masked": _R, "investigator_masked": _R,
        "outcomes_assessor_masked": _R,
    }, {}),
    TableSpec("eligibilities", CARDINALITY_ONE, {
        "sampling_method": _R, "gender": _R, "minimum_age": _R, "maximum_age": _R,
        "healthy_volunteers": _R, "population": _R, "criteria": _R,
        "gender_description": _R, "gender_based": _R, "adult": _R, "child": _R,
        "older_adult": _R,
    }, {
        "criteria": ("amendments that broaden criteria to rescue recruitment exist, but "
                     "are not systematic"),
    }),
    TableSpec("calculated_values", CARDINALITY_ONE, {
        "registered_in_calendar_year": _R,
        "minimum_age_num": _R, "maximum_age_num": _R,
        "minimum_age_unit": _R, "maximum_age_unit": _R,
        "number_of_facilities": _E, "has_us_facility": _E, "has_single_facility": _E,
        "number_of_primary_outcomes_to_measure": _E,
        "number_of_secondary_outcomes_to_measure": _E,
        "number_of_other_outcomes_to_measure": _E,
        "actual_duration": _P, "were_results_reported": _P,
        "months_to_report_results": _P, "number_of_sae_subjects": _P,
        "number_of_nsae_subjects": _P, "nlm_download_date": _P,
    }, {
        "number_of_facilities": "sites are added and closed during conduct",
        "number_of_primary_outcomes_to_measure": "outcome lists are revised at posting",
        "actual_duration": "a futility stop is short, so duration encodes failure",
        "nlm_download_date": "a snapshot artefact, not a property of the trial",
    }),
    TableSpec("brief_summaries", CARDINALITY_ONE, {"description": _R}, {}),
    TableSpec("responsible_parties", CARDINALITY_ONE, {
        "responsible_party_type": _R, "name": _R, "title": _R, "organization": _R,
        "affiliation": _R, "old_name_title": _R,
    }, {}),
    TableSpec("sponsors", CARDINALITY_MANY, {
        "agency_class": _R, "lead_or_collaborator": _R, "name": _R,
    }, {
        "name": ("a collaborator can be added after a positive readout (partnering), "
                 "which is not systematic but is outcome-correlated when it happens"),
    }),
    TableSpec("conditions", CARDINALITY_MANY, {"name": _R, "downcase_name": _R}, {}),
    TableSpec("keywords", CARDINALITY_MANY, {"name": _R, "downcase_name": _R}, {}),
    TableSpec("browse_conditions", CARDINALITY_MANY, {
        "mesh_term": _R, "downcase_mesh_term": _R, "mesh_type": _R,
    }, {}),
    TableSpec("browse_interventions", CARDINALITY_MANY, {
        "mesh_term": _R, "downcase_mesh_term": _R, "mesh_type": _R,
    }, {}),
    TableSpec("interventions", CARDINALITY_MANY, {
        "intervention_type": _R, "name": _R, "description": _R,
    }, {}),
    TableSpec("intervention_other_names", CARDINALITY_MANY, {
        "intervention_id": _R, "name": _R,
    }, {}),
    TableSpec("design_groups", CARDINALITY_MANY, {
        "group_type": _R, "title": _R, "description": _R,
    }, {}),
    TableSpec("id_information", CARDINALITY_MANY, {
        "id_source": _R, "id_type": _R, "id_value": _R, "id_type_description": _R,
        "id_link": _R,
    }, {}),
    TableSpec("countries", CARDINALITY_MANY, {"name": _E, "removed": _E}, {
        "name": "the list grows as sites open; removed countries stay with a flag",
    }),
    TableSpec("facilities", CARDINALITY_MANY, {
        "status": _E, "name": _E, "city": _E, "state": _E, "zip": _E, "country": _E,
        "latitude": _E, "longitude": _E,
    }, {"status": "site recruitment status at snapshot, including withdrawn sites"}),
    TableSpec("design_outcomes", CARDINALITY_MANY, {
        "outcome_type": _E, "measure": _E, "time_frame": _E, "population": _E,
        "description": _E,
    }, {
        "measure": ("revised at results posting; byte-identical to the posted title "
                    "where both exist (rev 8 section 12.15). Registry history, checked by "
                    "hand 2026-10-06: in 3 of 3 posted trials the results-posting version "
                    "also edited the registered Outcome Measures section, and 2 of 3 edited "
                    "it during conduct too"),
    }),
)


def table_spec(table: str) -> TableSpec:
    for spec in TABLES:
        if spec.table == table:
            return spec
    raise KeyError(table)


def provenance_for(table: str, column: str) -> str:
    """-> the column's provenance. Raises on an unclassified column rather than defaulting:
    a default would pick a side silently, and the safe side differs per use."""
    spec = table_spec(table)
    if column not in spec.columns:
        raise ValueError(f"{table}.{column} has no provenance; classify it in "
                         f"aact_provenance.TABLES deliberately")
    return spec.columns[column]


def unclassified_columns(table: str, found: Iterable[str]) -> list:
    """Columns the server has that the registry does not classify, keys excluded."""
    spec = table_spec(table)
    return sorted(set(found) - set(spec.columns) - KEY_COLUMNS)


def missing_columns(table: str, found: Iterable[str]) -> list:
    """Columns the registry classifies that the server does not have."""
    spec = table_spec(table)
    return sorted(set(spec.columns) - set(found))


def cardinality_verdict(cardinality: str, n_rows: int, n_trials: int) -> Optional[str]:
    """Observed row and trial counts against the declared cardinality.

    None when the table is empty: nothing was observed. A MANY table that happens to be
    one-to-one is consistent -- multiplicity is permitted, not required.
    """
    if cardinality not in CARDINALITIES:
        raise ValueError(f"{cardinality!r} is not one of {CARDINALITIES}")
    if n_rows < n_trials:
        raise ValueError(f"{n_rows} rows cannot cover {n_trials} distinct trials")
    if n_rows == 0:
        return None
    if cardinality == CARDINALITY_ONE and n_rows != n_trials:
        return VERDICT_CONTRADICTED
    return VERDICT_CONSISTENT


def columns_by_provenance(provenance: str) -> list:
    """Every (table, column) with this provenance, in declaration order."""
    if provenance not in PROVENANCES:
        raise ValueError(f"{provenance!r} is not one of {PROVENANCES}")
    return [(spec.table, column) for spec in TABLES
            for column, value in spec.columns.items() if value == provenance]


def label_derived_violations() -> list:
    """Classified columns named in LABEL_DERIVED_FIELDS that are not post_hoc. Must be
    empty: a label input marked as knowable at registration would pass the feature gate."""
    derived = set(LABEL_DERIVED_FIELDS)
    return [(spec.table, column) for spec in TABLES
            for column, value in spec.columns.items()
            if column in derived and value != PROVENANCE_POST_HOC]