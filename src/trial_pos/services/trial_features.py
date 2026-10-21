"""Trial features and the leakage registry that guards them (D-56, D-60, D-62, D-63). Pure.

EVERY FEATURE DECLARES ITS SOURCES as AACT (table, column) pairs, and the registry resolves
each one through `aact_provenance.provenance_for`, which raises on anything it does not
classify. So a feature cannot read DrugCentral, Drugs@FDA, a label file or a resolution
output: none of those is an AACT table. `leakage_violations(specs, target)` then refuses:
  - any source whose provenance is `post_hoc` (it does not exist when a trial is scored);
  - any source column in `endpoint_label.LABEL_DERIVED_FIELDS` (R7), for every target;
  - for endpoint met, any feature or source named in `endpoint_type.GATE_ONLY_FIELDS`.
The builder calls it for all three targets and stops on any violation.

GROUPS. `SERVED_GROUPS` make the headline model. `calendar` (D-60) and `entity` (D-62) are
toggles fitted as named variants, never served by default. The registration-only
sensitivity (D-56) is every served feature whose sources are all `registration`.

MISSING IS NEVER A VALUE. A tri-state stays True / False / None; a blank count is None, not
0; a trial with no MeSH condition has unknown disease flags, not False ones.

HISTORY IS AS OF REGISTRATION. A history count reads only trials registered in the
`lookback` years BEFORE this trial's own registration date (half-open: same-day trials and
the trial itself are excluded), so nothing registered later can reach it.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from bisect import bisect_left
from datetime import date
from typing import Iterable, NamedTuple, Optional

from trial_pos.services.aact_aggregates import AGG_SEPARATOR
from trial_pos.services.aact_fields import output_columns, output_name
from trial_pos.services.aact_provenance import (
    PROVENANCE_POST_HOC, PROVENANCE_REGISTRATION, provenance_for,
)
from trial_pos.services.advancement_label import PHASE_RANK
from trial_pos.services.eligibility import normalize_phase, years_after
from trial_pos.services.endpoint_label import LABEL_DERIVED_FIELDS
from trial_pos.services.endpoint_type import GATE_ONLY_FIELDS
from trial_pos.services.population import (
    classified_agency_class, normalize_agency_class, parse_date, tribool,
)

TARGET_ENDPOINT_MET = "endpoint_met"
TARGET_ADVANCEMENT = "advancement"
TARGET_MARKET = "market"
TARGETS = (TARGET_ENDPOINT_MET, TARGET_ADVANCEMENT, TARGET_MARKET)

GROUP_DESIGN = "design"            # registration fields (D-60)
GROUP_EDITABLE = "editable"        # editable_current_value fields (D-56)
GROUP_DISEASE = "disease"          # MeSH C categories (D-63)
GROUP_TEXT = "eligibility_text"    # D-63
GROUP_HISTORY = "drug_history"     # as of registration (D-60, D-63)
GROUP_CALENDAR = "calendar"        # toggle: sensitivity only (D-60)
GROUP_ENTITY = "entity"            # toggle: experience level (D-62)
# Toggle (D-65): registration fields that ClinicalTrials.gov added around 2017. A trial read
# out before then carries them only if its record was edited afterwards, and for a completed
# trial that edit is usually the results posting, whose timing feeds endpoint met (rev 8). In
# build_features_20261001.txt `is_fda_regulated_drug` is filled on 7.5% of endpoint-met
# training trials and 99.4% of test trials. Their PRESENCE would report how the trial ended.
GROUP_LATE_FIELDS = "post_2017_fields"
LATE_FIELD_FEATURES = ("is_fda_regulated_drug", "is_fda_regulated_device", "is_us_export",
                       "plan_to_share_ipd", "ipd_url_present")
SERVED_GROUPS = (GROUP_DESIGN, GROUP_EDITABLE, GROUP_DISEASE, GROUP_TEXT, GROUP_HISTORY)
TOGGLE_GROUPS = (GROUP_CALENDAR, GROUP_ENTITY, GROUP_LATE_FIELDS)
# D-69's pre-registered bundle (attempt 2): the two strongest registration-time features in
# the literature that the first attempt lacked (Lo, Siah & Wong 2019; Siah et al. 2021):
GROUP_PRIOR_APPROVAL = "prior_approval"    # a tested drug FDA-approved before registration
GROUP_TRACK_RECORD = "track_record"        # earlier public endpoint outcomes, drug and sponsor
BUNDLE_GROUPS = (GROUP_PRIOR_APPROVAL, GROUP_TRACK_RECORD)
GROUPS = SERVED_GROUPS + TOGGLE_GROUPS + BUNDLE_GROUPS

# AS-OF SOURCES (D-69): the only sources outside AACT a feature may name, and only a
# BUNDLE_GROUPS feature. Each is used solely through a date strictly before the trial's own
# registration, so nothing that happened later can reach it:
#   approval dates: a date before registration is a fact at registration. Resolution itself
#     is never a feature: an agent that does not resolve counts as 'not known approved',
#     exactly like one approved later, so the flag cannot reveal a later approval;
#   other trials' endpoint outcomes, each dated by when its results became public; the
#     trial's own outcome is excluded by construction (and by its own date).
AS_OF_APPROVALS = ("drugs_at_fda_and_drugcentral", "fda_approval_date")
AS_OF_OUTCOMES = ("other_trials", "endpoint_met_label_public_before_registration")
AS_OF_SOURCES = (AS_OF_APPROVALS, AS_OF_OUTCOMES)

KIND_NUMERIC = "numeric"
KIND_TRISTATE = "tristate"
KIND_CATEGORY = "category"
KINDS = (KIND_NUMERIC, KIND_TRISTATE, KIND_CATEGORY)


class FeatureSpec(NamedTuple):
    name: str
    group: str
    kind: str
    sources: tuple            # ((table, column), ...) in AACT
    note: str


def _f(table: str, column: str) -> tuple:
    return (table, column)


# ---- the fields file: which output columns derive from which AACT columns -----------------
_FIELD_SOURCES = {c.name: tuple((c.table, s) for s in c.sources) for c in output_columns(False)}


_FIELD_PROVENANCE = {c.name: c.provenance for c in output_columns(False)}


def field_provenance(column: str) -> str:
    """The code registry's provenance for a fields-file column (the file's own provenance
    file must agree; the builder checks)."""
    return _FIELD_PROVENANCE[column]


def field_sources(column: str) -> tuple:
    """The AACT (table, column) sources of a fields-file column. Raises on an unknown one."""
    return _FIELD_SOURCES[column]


SUBMITTED = output_name("studies", "study_first_submitted_date")
START = output_name("studies", "start_date")

_CATEGORY_FIELDS = (  # (feature, fields column, group)
    ("phase", output_name("studies", "phase"), GROUP_DESIGN),
    ("allocation", output_name("designs", "allocation"), GROUP_DESIGN),
    ("intervention_model", output_name("designs", "intervention_model"), GROUP_DESIGN),
    ("masking", output_name("designs", "masking"), GROUP_DESIGN),
    ("primary_purpose", output_name("designs", "primary_purpose"), GROUP_DESIGN),
    ("gender", output_name("eligibilities", "gender"), GROUP_DESIGN),
    ("lead_agency_class", output_name("sponsors", "lead_agency_class"), GROUP_DESIGN),
    ("responsible_party_type", output_name("responsible_parties", "responsible_party_type"),
     GROUP_DESIGN),
    ("plan_to_share_ipd", output_name("studies", "plan_to_share_ipd"), GROUP_EDITABLE),
)
_TRISTATE_FIELDS = (
    *((f"{w}_masked", output_name("designs", f"{w}_masked"), GROUP_DESIGN)
      for w in ("subject", "caregiver", "investigator", "outcomes_assessor")),
    *((w, output_name("eligibilities", w), GROUP_DESIGN)
      for w in ("healthy_volunteers", "adult", "child", "older_adult")),
    *((w, output_name("studies", w), GROUP_DESIGN)
      for w in ("has_dmc", "is_fda_regulated_drug", "is_fda_regulated_device",
                "is_us_export")),
    ("has_expanded_access", output_name("studies", "has_expanded_access"), GROUP_EDITABLE),
)
_ARM_TYPES = ("experimental", "active_comparator", "placebo_comparator", "sham_comparator",
              "no_intervention", "other")
_NUMERIC_FIELDS = (
    ("number_of_arms", output_name("studies", "number_of_arms"), GROUP_DESIGN),
    *((f"arms_{t}", output_name("design_groups", f"n_{t}"), GROUP_DESIGN) for t in _ARM_TYPES),
    ("n_collaborators", output_name("sponsors", "n_collaborators"), GROUP_DESIGN),
    ("n_conditions", output_name("conditions", "n_conditions"), GROUP_DESIGN),
    ("n_keywords", output_name("keywords", "n_keywords"), GROUP_DESIGN),
    ("n_condition_mesh", output_name("browse_conditions", "n_mesh_list"), GROUP_DESIGN),
    ("n_intervention_mesh", output_name("browse_interventions", "n_mesh_list"), GROUP_DESIGN),
    ("n_interventions", output_name("interventions", "n_distinct"), GROUP_DESIGN),
    ("n_drug_interventions", output_name("interventions", "n_drug"), GROUP_DESIGN),
    ("n_other_names", output_name("intervention_other_names", "n_distinct"), GROUP_DESIGN),
    ("n_secondary_ids", output_name("id_information", "n_secondary_ids"), GROUP_DESIGN),
    ("n_sites", output_name("facilities", "n_distinct"), GROUP_EDITABLE),
    ("n_us_sites", output_name("facilities", "n_us_distinct"), GROUP_EDITABLE),
    ("n_site_countries", output_name("facilities", "n_countries"), GROUP_EDITABLE),
    ("n_countries", output_name("countries", "n_current"), GROUP_EDITABLE),
    ("n_countries_removed", output_name("countries", "n_removed"), GROUP_EDITABLE),
    *((f"n_{t}_outcomes", output_name("design_outcomes", f"n_{t}"), GROUP_EDITABLE)
      for t in ("primary", "secondary", "other")),
)
def fields_columns_read() -> tuple:
    """Every fields-file column `fields_features` reads, sorted."""
    cols = {c for _n, c, _g in _CATEGORY_FIELDS + _TRISTATE_FIELDS + _NUMERIC_FIELDS}
    cols |= {ARMS_PLACEBO, ARMS_ACTIVE, ARMS_SHAM, ARMS_NONE, COLLAB_N, COLLAB_CLASSES,
             EUDRACT, ACRONYM, IPD_URL, TYPES, SITES, US_SITES, SITE_COUNTRIES, START,
             SUBMITTED, REGISTERED_YEAR, *AGE["minimum"], *AGE["maximum"]}
    return tuple(sorted(cols))


# D-68: the registered outcome counts, revised by sponsors when they post results. Every
# test trial is finished too, so a temporal test cannot see how they differ at serving.
REGISTERED_OUTCOME_COUNT_FEATURES = tuple(f"n_{t}_outcomes"
                                          for t in ("primary", "secondary", "other"))


ARMS_PLACEBO = output_name("design_groups", "n_placebo_comparator")
ARMS_ACTIVE = output_name("design_groups", "n_active_comparator")
ARMS_SHAM = output_name("design_groups", "n_sham_comparator")
ARMS_NONE = output_name("design_groups", "n_no_intervention")
COLLAB_N = output_name("sponsors", "n_collaborators")
COLLAB_CLASSES = output_name("sponsors", "collaborator_agency_classes")
EUDRACT = output_name("id_information", "n_eudract_ids")
ACRONYM = output_name("studies", "acronym")
IPD_URL = output_name("studies", "ipd_url")
TYPES = output_name("interventions", "types")
SITES, US_SITES = output_name("facilities", "n_distinct"), output_name("facilities", "n_us_distinct")
SITE_COUNTRIES = output_name("facilities", "n_countries")
AGE = {b: (output_name("calculated_values", f"{b}_age_num"),
           output_name("calculated_values", f"{b}_age_unit")) for b in ("minimum", "maximum")}
REGISTERED_YEAR = output_name("calculated_values", "registered_in_calendar_year")

# Intervention types other than drug (every trial here is a drug trial), AACT's tokens.
INTERVENTION_TYPES = ("biological", "genetic", "device", "procedure", "behavioral",
                      "radiation", "dietary_supplement", "combination_product",
                      "diagnostic_test", "other")
DAYS_PER_YEAR = 365.25
# AACT age units -> years. Singular, lower case; a plural 's' is stripped before lookup.
AGE_UNIT_YEARS = {"year": 1.0, "month": 1.0 / 12, "week": 7.0 / DAYS_PER_YEAR,
                  "day": 1.0 / DAYS_PER_YEAR, "hour": 1.0 / (DAYS_PER_YEAR * 24),
                  "minute": 1.0 / (DAYS_PER_YEAR * 24 * 60)}
CLASS_INDUSTRY, CLASS_NIH = "industry", "nih"

# ---- eligibility text --------------------------------------------------------------------
# Patient-selection biomarkers named in eligibility criteria (D-63; Thomas et al. 2016 found
# higher success with selection biomarkers). Each must end at a word boundary, so 'ALK' does
# not match 'alkaline phosphatase' and 'RET' not 'retinopathy' (the first probe's bug).
BIOMARKER_TERMS = ("egfr", "her2", "erbb2", "alk", "ros1", "braf", "kras", "nras", "brca1",
                   "brca2", "pd-l1", "msi-h", "dmmr", "ntrk", "flt3", "idh1", "idh2", "fgfr",
                   "ret", "met exon", "mutation", "mutations", "mutated", "amplification",
                   "amplified", "rearrangement", "rearranged", "fusion", "fusions")
_BIOMARKER = re.compile(r"(?<![0-9a-z])(" + "|".join(re.escape(t) for t in BIOMARKER_TERMS)
                        + r")(?![0-9a-z])", re.I)
_LIST_ITEM = re.compile(r"^\s*(?:[-*\u2022\u00b7]|\d{1,3}[.)])\s+")

# ---- MeSH disease categories -------------------------------------------------------------
MESH_DISEASE_BRANCH = "C"
TOP_CODE_LENGTH = 3            # 'C04'
MESH_LIST_TYPE = "mesh-list"   # browse_conditions.mesh_type for indexed terms

# ---- entity names (D-62) -------------------------------------------------------------------
# Trailing words that differ between spellings of one organisation ('Pfizer' / 'Pfizer Inc.'),
# removed from the END only. probe_feature_candidates_20261001.txt: lead-sponsor names are
# nearly one spelling each (44,379 raw, 44,262 keys); sites merge 915,741 to 843,346.
CORPORATE_SUFFIXES = frozenset({
    "inc", "incorporated", "ltd", "limited", "llc", "lp", "corp", "corporation", "co",
    "company", "gmbh", "ag", "sa", "nv", "bv", "plc", "spa", "srl", "kk", "ab", "as", "oy",
})
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


# ---- the specs -------------------------------------------------------------------------------
def _spec_from_field(name, column, group, kind, note=""):
    return FeatureSpec(name, group, kind, field_sources(column), note)


DISEASE_PREFIX = "disease_"


def disease_feature_name(code: str) -> str:
    return f"{DISEASE_PREFIX}{code}"


def disease_codes_from_names(names: Iterable[str]) -> list:
    """The C codes behind a feature list's disease flags ('disease_C04' -> 'C04'), sorted.
    Other features sharing the prefix ('disease_crowding') are not codes."""
    out = []
    for n in names:
        code = n[len(DISEASE_PREFIX):] if n.startswith(DISEASE_PREFIX) else ""
        if (len(code) == TOP_CODE_LENGTH and code.startswith(MESH_DISEASE_BRANCH)
                and code[1:].isdigit()):
            out.append(code)
    return sorted(out)


def build_specs(disease_codes: Iterable[str]) -> tuple:
    """Every feature, in output order. `disease_codes`: the top-level C codes found in the
    MeSH vocabulary (one flag each)."""
    out = [_spec_from_field(n, c, g, KIND_CATEGORY) for n, c, g in _CATEGORY_FIELDS]
    out += [_spec_from_field(n, c, g, KIND_TRISTATE) for n, c, g in _TRISTATE_FIELDS]
    out += [_spec_from_field(n, c, g, KIND_NUMERIC) for n, c, g in _NUMERIC_FIELDS]
    out += [
        _spec_from_field("placebo_arm", ARMS_PLACEBO, GROUP_DESIGN, KIND_TRISTATE),
        _spec_from_field("active_comparator_arm", ARMS_ACTIVE, GROUP_DESIGN, KIND_TRISTATE),
        _spec_from_field("sham_arm", ARMS_SHAM, GROUP_DESIGN, KIND_TRISTATE),
        _spec_from_field("no_intervention_arm", ARMS_NONE, GROUP_DESIGN, KIND_TRISTATE),
        FeatureSpec("industry_collaborator", GROUP_DESIGN, KIND_TRISTATE,
                    field_sources(COLLAB_N) + field_sources(COLLAB_CLASSES), ""),
        FeatureSpec("nih_collaborator", GROUP_DESIGN, KIND_TRISTATE,
                    field_sources(COLLAB_N) + field_sources(COLLAB_CLASSES), ""),
        _spec_from_field("eu_registered", EUDRACT, GROUP_DESIGN, KIND_TRISTATE,
                         "an EudraCT id is listed"),
        _spec_from_field("acronym_present", ACRONYM, GROUP_DESIGN, KIND_TRISTATE),
        *(_spec_from_field(f"intervention_{t}", TYPES, GROUP_DESIGN, KIND_TRISTATE)
          for t in INTERVENTION_TYPES),
        *(FeatureSpec(f"{b}_age_years", GROUP_DESIGN, KIND_NUMERIC,
                      field_sources(AGE[b][0]) + field_sources(AGE[b][1]), "")
          for b in ("minimum", "maximum")),
        _spec_from_field("ipd_url_present", IPD_URL, GROUP_EDITABLE, KIND_TRISTATE),
        FeatureSpec("us_site_share", GROUP_EDITABLE, KIND_NUMERIC,
                    field_sources(SITES) + field_sources(US_SITES), ""),
        _spec_from_field("multi_country", SITE_COUNTRIES, GROUP_EDITABLE, KIND_TRISTATE),
        FeatureSpec("start_minus_registration_days", GROUP_EDITABLE, KIND_NUMERIC,
                    field_sources(START) + field_sources(SUBMITTED),
                    "negative: registered after the start"),
        _spec_from_field("registration_year", REGISTERED_YEAR, GROUP_CALENDAR, KIND_NUMERIC,
                         "calendar time: a sensitivity, never served (D-60)"),
    ]
    out = [s._replace(group=GROUP_LATE_FIELDS) if s.name in LATE_FIELD_FEATURES else s
           for s in out]
    mesh = (_f("browse_conditions", "downcase_mesh_term"), _f("browse_conditions", "mesh_type"))
    out += [FeatureSpec(disease_feature_name(c), GROUP_DISEASE, KIND_TRISTATE, mesh, "")
            for c in sorted(disease_codes)]
    out.append(FeatureSpec("n_disease_categories", GROUP_DISEASE, KIND_NUMERIC, mesh, ""))
    crit = (_f("eligibilities", "criteria"),)
    out += [FeatureSpec("eligibility_chars", GROUP_TEXT, KIND_NUMERIC, crit, ""),
            FeatureSpec("eligibility_items", GROUP_TEXT, KIND_NUMERIC, crit, "listed criteria"),
            FeatureSpec("selection_biomarker", GROUP_TEXT, KIND_TRISTATE, crit,
                        "names a BIOMARKER_TERMS word")]
    when = (_f("studies", "study_first_submitted_date"),)
    agent = (_f("interventions", "name"), _f("intervention_other_names", "name"),
             _f("design_groups", "group_type"))
    out += [
        FeatureSpec("drug_prior_trials", GROUP_HISTORY, KIND_NUMERIC, agent + when,
                    "registered trials sharing a tested-agent name key, in the lookback"),
        FeatureSpec("drug_prior_max_phase", GROUP_HISTORY, KIND_NUMERIC, agent + when
                    + (_f("studies", "phase"),), "highest PHASE_RANK among them, 0 if none"),
        FeatureSpec("drug_prior_same_disease", GROUP_HISTORY, KIND_NUMERIC,
                    agent + when + mesh, "of them, sharing a MeSH condition term"),
        FeatureSpec("disease_crowding", GROUP_HISTORY, KIND_NUMERIC, mesh + when,
                    "trials registered with this trial's most-registered MeSH term in the "
                    "lookback, as a share of all registered then"),
    ]
    sponsor = (_f("sponsors", "name"), _f("sponsors", "lead_or_collaborator"))
    site = (_f("facilities", "name"), _f("facilities", "city"), _f("facilities", "country"))
    out += [
        FeatureSpec("prior_fda_approval", GROUP_PRIOR_APPROVAL, KIND_TRISTATE,
                    agent + when + (AS_OF_APPROVALS,),
                    "a tested drug had an FDA approval, any form or indication, before this "
                    "trial's registration (Lo et al. 2019: prior approval for another "
                    "indication)"),
        FeatureSpec("drug_track_record", GROUP_TRACK_RECORD, KIND_NUMERIC,
                    agent + when + (AS_OF_OUTCOMES,),
                    "Laplace-smoothed share of earlier trials of a tested drug whose endpoint "
                    "was met, among those public before registration (Siah et al. 2021)"),
        FeatureSpec("sponsor_track_record", GROUP_TRACK_RECORD, KIND_NUMERIC,
                    sponsor + when + (AS_OF_OUTCOMES,),
                    "the same for the lead sponsor's earlier trials (Lo et al. 2019: "
                    "sponsor track record)"),
    ]
    out += [
        FeatureSpec("sponsor_prior_trials", GROUP_ENTITY, KIND_NUMERIC, sponsor + when,
                    "lead sponsor's registered trials in the lookback"),
        FeatureSpec("sponsor_prior_same_category", GROUP_ENTITY, KIND_NUMERIC,
                    sponsor + when + mesh, "of them, sharing a disease category"),
        FeatureSpec("site_prior_median", GROUP_ENTITY, KIND_NUMERIC, site + when,
                    "median over the trial's sites of their registered trials in the lookback"),
        FeatureSpec("site_prior_max", GROUP_ENTITY, KIND_NUMERIC, site + when, ""),
    ]
    return tuple(out)


# ---- the leakage registry -------------------------------------------------------------------
def leakage_violations(specs: Iterable[FeatureSpec], target: str) -> list:
    """Every way `specs` would leak into `target`. Must be empty. Raises (via
    provenance_for) on a source the AACT registry does not classify."""
    if target not in TARGETS:
        raise ValueError(f"target {target!r} not in {TARGETS}")
    label_derived = set(LABEL_DERIVED_FIELDS)
    gate_only = set(GATE_ONLY_FIELDS) if target == TARGET_ENDPOINT_MET else set()
    out = []
    for s in specs:
        if any(src in AS_OF_SOURCES for src in s.sources) and s.group not in BUNDLE_GROUPS:
            out.append(f"{s.name}: an as-of source outside D-69's bundle groups")
        if s.group not in GROUPS or s.kind not in KINDS:
            out.append(f"{s.name}: unknown group {s.group!r} or kind {s.kind!r}")
        if not s.sources:
            out.append(f"{s.name}: declares no source")
        if s.name in gate_only:
            out.append(f"{s.name}: a gate-only field (rev 7 section 12.8)")
        for table, column in s.sources:
            if (table, column) in AS_OF_SOURCES:
                continue                      # dated before registration by construction
            if provenance_for(table, column) == PROVENANCE_POST_HOC:
                out.append(f"{s.name}: {table}.{column} is post_hoc")
            if column in label_derived:
                out.append(f"{s.name}: {table}.{column} feeds a label (R7)")
            if column in gate_only:
                out.append(f"{s.name}: {table}.{column} is gate-only")
    return out


PROVENANCE_AS_OF = "as_of_registration"


def source_provenance(table: str, column: str) -> str:
    """Any declared source's provenance: D-69's as-of sources by name, AACT ones through the
    registry (which raises on anything else)."""
    if (table, column) in AS_OF_SOURCES:
        return PROVENANCE_AS_OF
    return provenance_for(table, column)


def is_registration_only(spec: FeatureSpec) -> bool:
    """True when every source is `registration`: the D-56 sensitivity's features. A bundle
    feature is not (its as-of sources are not registration fields)."""
    return all((t, c) not in AS_OF_SOURCES and provenance_for(t, c) == PROVENANCE_REGISTRATION
               for t, c in spec.sources)


def served(specs: Iterable[FeatureSpec], with_bundle: bool = False) -> tuple:
    """The served groups, plus D-69's bundle when `with_bundle` (attempt 2)."""
    groups = SERVED_GROUPS + (BUNDLE_GROUPS if with_bundle else ())
    return tuple(s for s in specs if s.group in groups)


# ---- per-trial computations -----------------------------------------------------------------
def _text(raw) -> Optional[str]:
    t = "" if raw is None else str(raw).strip()
    return t or None


def _int(raw) -> Optional[int]:
    t = _text(raw)
    if t is None:
        return None
    try:
        return int(float(t))
    except ValueError:
        return None


def _category(raw) -> Optional[str]:
    t = _text(raw)
    return None if t is None else t.casefold()


def _present(n: Optional[int]) -> Optional[bool]:
    return None if n is None else n > 0


def age_years(num, unit) -> Optional[float]:
    """An AACT age bound in years. None when either part is blank or the unit unknown."""
    n, u = _int(num), _category(unit)
    if n is None or u is None:
        return None
    u = u[:-1] if u.endswith("s") else u
    factor = AGE_UNIT_YEARS.get(u)
    return None if factor is None else n * factor


def _tokens(raw) -> Optional[set]:
    t = _text(raw)
    if t is None:
        return None
    return {x.strip().casefold().replace(" ", "_") for x in t.split(AGG_SEPARATOR) if x.strip()}


def fields_features(row: dict) -> dict:
    """Design, editable and calendar features from one fields-file row."""
    out = {}
    for name, col, _g in _CATEGORY_FIELDS:
        out[name] = _category(row.get(col))
    out["phase"] = normalize_phase(row.get(output_name("studies", "phase")))
    out["lead_agency_class"] = classified_agency_class(
        row.get(output_name("sponsors", "lead_agency_class")))
    for name, col, _g in _TRISTATE_FIELDS:
        out[name] = tribool(row.get(col))
    for name, col, _g in _NUMERIC_FIELDS:
        out[name] = _int(row.get(col))
    out["placebo_arm"] = _present(_int(row.get(ARMS_PLACEBO)))
    out["active_comparator_arm"] = _present(_int(row.get(ARMS_ACTIVE)))
    out["sham_arm"] = _present(_int(row.get(ARMS_SHAM)))
    out["no_intervention_arm"] = _present(_int(row.get(ARMS_NONE)))
    n_collab = _int(row.get(COLLAB_N))
    classes = {normalize_agency_class(c) for c in (_tokens(row.get(COLLAB_CLASSES)) or ())}
    for name, cls in (("industry_collaborator", CLASS_INDUSTRY), ("nih_collaborator", CLASS_NIH)):
        out[name] = None if n_collab is None else (n_collab > 0 and cls in classes)
    out["eu_registered"] = _present(_int(row.get(EUDRACT)))
    out["acronym_present"] = _text(row.get(ACRONYM)) is not None
    out["ipd_url_present"] = _text(row.get(IPD_URL)) is not None
    types = _tokens(row.get(TYPES))
    for t in INTERVENTION_TYPES:
        out[f"intervention_{t}"] = None if types is None else t in types
    for b in ("minimum", "maximum"):
        out[f"{b}_age_years"] = age_years(row.get(AGE[b][0]), row.get(AGE[b][1]))
    sites, us = _int(row.get(SITES)), _int(row.get(US_SITES))
    out["us_site_share"] = (us / sites) if sites and us is not None else None
    n_c = _int(row.get(SITE_COUNTRIES))
    out["multi_country"] = None if n_c is None else n_c > 1
    start, sub = parse_date(row.get(START)), parse_date(row.get(SUBMITTED))
    out["start_minus_registration_days"] = (start - sub).days if start and sub else None
    out["registration_year"] = _int(row.get(REGISTERED_YEAR))
    return out


def text_features(criteria) -> dict:
    t = _text(criteria)
    if t is None:
        return {"eligibility_chars": None, "eligibility_items": None,
                "selection_biomarker": None}
    return {"eligibility_chars": len(t),
            "eligibility_items": sum(bool(_LIST_ITEM.match(line)) for line in t.splitlines()),
            "selection_biomarker": bool(_BIOMARKER.search(t))}


def top_codes(tree_numbers: Iterable[str]) -> set:
    """Top-level MeSH C codes ('C04') of a term's tree numbers."""
    return {tn[:TOP_CODE_LENGTH] for tn in tree_numbers
            if tn and tn.startswith(MESH_DISEASE_BRANCH) and len(tn) >= TOP_CODE_LENGTH}


def disease_features(n_mesh_terms: int, codes: set, all_codes: Iterable[str]) -> dict:
    """Flags per C code. A trial with no mesh-list term has UNKNOWN flags; one with terms
    but no disease category (healthy volunteers) has False ones."""
    out = {}
    known = n_mesh_terms > 0
    for c in sorted(all_codes):
        out[disease_feature_name(c)] = (c in codes) if known else None
    out["n_disease_categories"] = len(codes) if known else None
    return out


def entity_key(name) -> str:
    """Accents folded, casefolded, punctuation to spaces, trailing corporate suffixes off.
    '' when nothing is left."""
    t = unicodedata.normalize("NFKD", str(name or ""))
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    words = _NON_ALNUM.sub(" ", t).split()
    while len(words) > 1 and words[-1] in CORPORATE_SUFFIXES:
        words.pop()
    return " ".join(words)


def site_key(name, city, country) -> str:
    """A site: its name, city and country. '' when the name is blank."""
    if not _text(name):
        return ""
    return " | ".join(entity_key(x) for x in (name, city, country))


# ---- history, as of registration ------------------------------------------------------------
class HistoryIndex:
    """key -> registration dates of the trials carrying it (one per trial), for half-open
    lookback counts. Build with `add`, then `freeze` before counting."""

    def __init__(self):
        self._by_key = {}
        self._frozen = False

    def add(self, key, nct_id: str, registered: Optional[date]) -> None:
        if self._frozen:
            raise RuntimeError("index is frozen")
        if key in ("", None) or registered is None:
            return
        self._by_key.setdefault(key, {})[nct_id] = registered

    def freeze(self) -> "HistoryIndex":
        self._by_key = {k: sorted(v.values()) for k, v in self._by_key.items()}
        self._frozen = True
        return self

    def count(self, key, registered: date, lookback_years: int) -> int:
        """Trials carrying `key` registered in [registered - lookback, registered)."""
        if not self._frozen:
            raise RuntimeError("freeze the index before counting")
        dates = self._by_key.get(key, ())
        return (bisect_left(dates, registered)
                - bisect_left(dates, years_after(registered, -lookback_years)))


def window_bounds(registered: date, lookback_years: int) -> tuple:
    return years_after(registered, -lookback_years), registered


class DrugHistory:
    """Name key -> (registered, nct_id, phase rank, mesh terms) of drug trials, for the
    drug-history features. Trials without a registration date or a ranked phase are not
    indexed."""

    def __init__(self, entries: Iterable[tuple]):
        by = {}
        for key, registered, nct, phase, mesh in entries:
            if registered is None or phase not in PHASE_RANK:
                continue
            by.setdefault(key, {})[nct] = (registered, nct, PHASE_RANK[phase], frozenset(mesh))
        self._by_key = {k: sorted(v.values()) for k, v in by.items()}
        self._dates = {k: [e[0] for e in v] for k, v in self._by_key.items()}

    def features(self, keys: Iterable[str], registered: Optional[date], mesh: Iterable[str],
                 lookback_years: int) -> dict:
        keys = sorted(set(keys))
        if not keys or registered is None:
            return {"drug_prior_trials": None, "drug_prior_max_phase": None,
                    "drug_prior_same_disease": None}
        lo, hi = window_bounds(registered, lookback_years)
        mesh = frozenset(mesh)
        seen = {}
        for k in keys:
            dates = self._dates.get(k, [])
            for e in self._by_key.get(k, [])[bisect_left(dates, lo):bisect_left(dates, hi)]:
                seen[e[1]] = e
        prior = list(seen.values())
        return {"drug_prior_trials": len(prior),
                "drug_prior_max_phase": max((e[2] for e in prior), default=0),
                "drug_prior_same_disease": sum(bool(e[3] & mesh) for e in prior)}


def disease_crowding(term_index: HistoryIndex, all_index: HistoryIndex, terms: Iterable[str],
                     registered: Optional[date], lookback_years: int) -> Optional[float]:
    terms = sorted(set(terms))
    if not terms or registered is None:
        return None
    total = all_index.count(ALL_TRIALS_KEY, registered, lookback_years)
    if not total:
        return None
    return max(term_index.count(t, registered, lookback_years) for t in terms) / total


ALL_TRIALS_KEY = "*"


def site_features(site_index: HistoryIndex, sites: Iterable[str], registered: Optional[date],
                  lookback_years: int) -> dict:
    sites = sorted(set(s for s in sites if s))
    if not sites or registered is None:
        return {"site_prior_median": None, "site_prior_max": None}
    counts = [site_index.count(s, registered, lookback_years) for s in sites]
    return {"site_prior_median": statistics.median(counts), "site_prior_max": max(counts)}


# ---- D-69: prior approval and track records, as of registration ----------------------------
def prior_approval(approval_dates: Iterable[date], registered: Optional[date]) -> Optional[bool]:
    """True when any dated FDA approval of a tested drug falls strictly before registration.
    `approval_dates`: every dated approval of every tested agent that resolved; an agent that
    did not resolve contributes none, so it reads like a drug approved later (never as
    'unknown', which would reveal resolution)."""
    if registered is None:
        return None
    return any(d < registered for d in approval_dates)


def laplace_share(met: int, n: int) -> Optional[float]:
    """(met + 1) / (n + 2), Laplace's rule of succession: one trial cannot read as 0 or 1.
    None without a single earlier outcome."""
    if n < 0 or not 0 <= met <= n:
        raise ValueError(f"bad counts met={met} n={n}")
    return None if n == 0 else (met + 1) / (n + 2)


class OutcomeHistory:
    """key -> earlier endpoint outcomes (public date, nct_id, 0/1). `track_record` reads
    only outcomes public STRICTLY BEFORE the asking trial's registration, never its own."""

    def __init__(self, entries: Iterable[tuple]):
        by = {}
        for key, nct, public, label in entries:
            if key in ("", None) or public is None:
                continue
            if label not in (0, 1):
                raise ValueError(f"{nct}: outcome {label!r} is not 0 or 1")
            by.setdefault(key, {})[nct] = (public, nct, label)
        self._by_key = {k: sorted(v.values()) for k, v in by.items()}
        self._dates = {k: [e[0] for e in v] for k, v in self._by_key.items()}

    def track_record(self, keys: Iterable, registered: Optional[date],
                     own_nct: str) -> Optional[float]:
        keys = sorted(k for k in set(keys) if k not in ("", None))
        if not keys or registered is None:
            return None
        seen = {}
        for k in keys:
            entries = self._by_key.get(k, [])
            for e in entries[:bisect_left(self._dates.get(k, []), registered)]:
                if e[1] != own_nct:
                    seen[e[1]] = e[2]
        return laplace_share(sum(seen.values()), len(seen))


# ---- output -----------------------------------------------------------------------------------
def csv_cell(value) -> str:
    """'' for unknown, '1'/'0' for a tri-state, a fixed format for floats (deterministic)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
