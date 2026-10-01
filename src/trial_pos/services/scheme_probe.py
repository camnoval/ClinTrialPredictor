"""What would each candidate repair to the class scheme buy? DIAGNOSTIC ONLY.

WHY THIS IS NOT A SCORING MODULE, AND THE DISTINCTION IS THE WHOLE POINT
=======================================================================
`falsify_endpoint_type.py` reports that the keyword rule's refusals are contradicted by
the sponsor on the large majority of the endpoints where both sides have a verdict, and
that the contradictions concentrate on nameable causes rather than spreading evenly. Two
of those causes live in the scheme's RESOLUTION STEP rather than in its patterns:

  * `CLASS_SAFETY` maps to not_applicable, so a safety endpoint the sponsor compared
    between arms is refused -- even though section 12.3's own justification for the class
    ("where it IS compared the trial's own posted analysis carries it") is an argument for
    ALLOWING it, since if the analysis carries the comparison the label reads it directly;
  * `CLASS_PRECEDENCE` puts safety and dose_finding ABOVE efficacy_shaped, so a title
    matching both loses its efficacy reading. Section 12.3 already flagged that order as
    "a reading, not a fact".

Both are expressible as parameters over `matched_classes()` output, which means their
effect can be MEASURED without touching a pattern. This module does that.

**IT DOES NOT RATIFY ANYTHING, AND MUST NEVER BE READ AS DOING SO.** The numbers it
produces are the candidate scheme's agreement with the SPONSOR REFERENCE -- the same
reference the candidate would have been chosen to agree with. Adopting a variant because
it scores well here and then citing this module as evidence for it is circular, and it is
the exact failure section 12.7 names: "if that carving is wrong, all of them will agree
with the rule and report a high kappa. A measurement that cannot come back negative is not
a measurement."

What this module is FOR is the opposite reading, which is not circular:

  1. **Attribution.** If a variant leaves the false-refusal share roughly unchanged, that
     cause does not explain the failure and the scheme's problem is elsewhere. A negative
     result here is trustworthy in a way a positive one is not.
  2. **Cost.** Every repair to over-refusal is a candidate INCREASE in under-refusal, and
     under-refusal is the worse failure -- it returns a probability for a question with no
     answer, which is the category error the gate exists to prevent. Reporting both cells
     per variant is what makes that trade visible instead of discovered later.

Ratification of any revised scheme still requires what it required before: the section
12.6 hand labels on REGISTERED text, and the ~25 adjudications of section 12.7 that are
the one step no automated reference can replace.

WHY THE RESOLUTION STEP IS RE-IMPLEMENTED HERE AND THE PATTERNS ARE NOT
=======================================================================
`endpoint_type.matched_classes` is the input, so every pattern decision is inherited
rather than copied -- there is exactly one place in the project where a title is matched
against a class. Only the two lines that RESOLVE a multi-match and MAP a class to a gate
are parameterised, and `test_the_defaults_reproduce_the_shipping_rule` asserts that at the
default arguments this module agrees with `classify_title` and `trial_gate` on every title
in the endpoint-type test corpus. If it ever disagrees, this module is wrong and the test
says so rather than the divergence turning up in a diagnostic somebody trusted.
"""
from __future__ import annotations

from typing import Iterable, Optional

from trial_pos.services.endpoint_type import (
    CLASS_EFFICACY, CLASS_GATE, CLASS_OTHER, CLASS_PRECEDENCE, CLASS_SAFETY,
    GATE_APPLICABLE, GATE_VERDICTS, matched_classes,
)

# ---- the resolution step, parameterised -----------------------------------
def resolve_class(matched: Iterable[str],
                  precedence: tuple = CLASS_PRECEDENCE) -> str:
    """Classes that fired -> the one the precedence order selects. CLASS_OTHER if none.

    `matched` is taken as a COLLECTION and re-ordered by `precedence`, not read in the
    order it arrives. `matched_classes` returns its hits already sorted by the shipping
    `CLASS_PRECEDENCE`, so a counterfactual order that simply iterated the input would
    silently reproduce the shipping order and every variant would look identical to
    baseline -- a diagnostic that cannot come back different.
    """
    present = set(matched)
    for endpoint_class in precedence:
        if endpoint_class in present:
            return endpoint_class
    return CLASS_OTHER


def gate_under(endpoint_class: str, class_gate: dict = CLASS_GATE) -> str:
    """One class -> its gate verdict under a candidate mapping. Raises on an unmapped class.

    Raising rather than defaulting, for the reason `endpoint_type.gate_for_class` raises: a
    class with no gate in a candidate mapping is a class the candidate forgot to decide
    about, and a default would pick a side silently and then be measured as if it had been
    chosen.
    """
    if endpoint_class not in class_gate:
        raise ValueError(f"{endpoint_class!r} has no gate under this candidate mapping; "
                         f"mapped classes are {sorted(class_gate)}")
    gate = class_gate[endpoint_class]
    if gate not in GATE_VERDICTS:
        raise ValueError(f"{gate!r} is not one of {GATE_VERDICTS}")
    return gate


def outcome_gate(title, precedence: tuple = CLASS_PRECEDENCE,
                 class_gate: dict = CLASS_GATE) -> str:
    """One endpoint text -> the gate verdict for that outcome under a candidate scheme.

    Per-outcome only, which is the unit this diagnostic works in and the unit section 12.6
    labels. The trial-level ANY/ALL roll-up is deliberately NOT parameterised here: it is
    the same roll-up as the label's `any_primary_met` and section 8.4 owns that choice, so
    a candidate scheme has no business moving it.
    """
    return gate_under(resolve_class(matched_classes(title), precedence), class_gate)


# ---- the candidates --------------------------------------------------------
# Each variant changes ONE thing, and the combination is included because repairs to a
# precedence order and to a gate mapping are not independent: promoting efficacy_shaped
# above safety removes some of the same endpoints that mapping safety to applicable would
# have rescued, so the two separately do not sum to the two together.
VARIANT_BASELINE = "baseline"
VARIANT_SAFETY_APPLICABLE = "safety_applicable"
VARIANT_EFFICACY_FIRST = "efficacy_first"
VARIANT_BOTH = "safety_applicable_and_efficacy_first"

# efficacy_shaped moved to the FRONT. Section 12.3 put it last on the ground that its
# patterns are the broadest, so anything more specific should have claimed the title
# already. That reasoning is about pattern breadth; the measured cost is that it hands
# every efficacy endpoint mentioning a dose or a toxicity to a refusing class.
_EFFICACY_FIRST = (CLASS_EFFICACY,) + tuple(c for c in CLASS_PRECEDENCE
                                            if c != CLASS_EFFICACY)

_SAFETY_APPLICABLE = dict(CLASS_GATE, **{CLASS_SAFETY: GATE_APPLICABLE})

VARIANTS = {
    VARIANT_BASELINE: (CLASS_PRECEDENCE, CLASS_GATE),
    VARIANT_SAFETY_APPLICABLE: (CLASS_PRECEDENCE, _SAFETY_APPLICABLE),
    VARIANT_EFFICACY_FIRST: (_EFFICACY_FIRST, CLASS_GATE),
    VARIANT_BOTH: (_EFFICACY_FIRST, _SAFETY_APPLICABLE),
}

VARIANT_ORDER = (VARIANT_BASELINE, VARIANT_SAFETY_APPLICABLE, VARIANT_EFFICACY_FIRST,
                 VARIANT_BOTH)

VARIANT_DOC = {
    VARIANT_BASELINE: "the shipping scheme, for the comparison to be against something",
    VARIANT_SAFETY_APPLICABLE: (
        "safety_tolerability -> applicable. Follows section 12.3's own stated reason for "
        "the class rather than its gate: where a safety endpoint IS compared, the posted "
        "analysis carries the comparison and the label reads it"),
    VARIANT_EFFICACY_FIRST: (
        "efficacy_shaped first in precedence. A title matching both an efficacy pattern "
        "and a refusing one is read as efficacy, so 'survival at the maximum tolerated "
        "dose' is no longer a dose-finding endpoint"),
    VARIANT_BOTH: (
        "both changes. Reported because they overlap: separately they rescue some of the "
        "same endpoints, so their effects do not add"),
}

# What CANNOT be probed this way, recorded so its absence is not read as its being fine.
# The pharmacokinetic pattern treats a bare AUC as decisive, and AUC is also a generic
# summary statistic over time for a clinical quantity -- FEV1 AUC, a symptom-score AUC, an
# ROC AUC (which the pattern's own comment already anticipates). Narrowing it is a PATTERN
# edit, so it is outside this module by construction, and it would be the single change
# most at risk of being tuned against this reference. It belongs with the hand labels.
UNPROBEABLE = (
    "the pharmacokinetic pattern's bare \\bauc\\b alternative: a pattern change, not a "
    "resolution change. Measure it against hand labels on registered text, never against "
    "the sponsor reference it would have been fitted to.",
)


def variant_gates(titles: Iterable, variant: str) -> list:
    """Endpoint texts -> their gate verdicts under one named variant, in the order given."""
    if variant not in VARIANTS:
        raise ValueError(f"{variant!r} is not one of {tuple(VARIANTS)}")
    precedence, class_gate = VARIANTS[variant]
    return [outcome_gate(title, precedence, class_gate) for title in titles]


def reclassified(titles: Iterable, variant: str) -> dict:
    """How many endpoints each variant moves, and between which verdicts.

    A variant that changes no verdicts is a variant that cannot explain anything, and that
    is worth seeing directly rather than inferring from two identical summary rows.
    """
    if variant not in VARIANTS:
        raise ValueError(f"{variant!r} is not one of {tuple(VARIANTS)}")
    ordered = list(titles)
    base = variant_gates(ordered, VARIANT_BASELINE)
    candidate = variant_gates(ordered, variant)
    moves: dict = {}
    for before, after in zip(base, candidate):
        if before != after:
            moves[(before, after)] = moves.get((before, after), 0) + 1
    return {"total": len(ordered), "moved": sum(moves.values()), "moves": moves}


def moved_share(report: dict) -> Optional[float]:
    """Share of endpoints a variant moves. None on an empty corpus, never 0.0."""
    total = report.get("total", 0)
    if not total:
        return None
    return report["moved"] / total