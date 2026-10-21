"""Published success rates, printed BESIDE each target's reference forecast (D-55). Data only.

They are context, never the reference. A Brier skill score's reference must be a forecast on
the SAME label and the SAME trials, which is the phase rate on the training side of the
target's temporal split; a published rate from another definition is miscalibrated against
this label by construction, and a model would beat it by learning the base rate alone.

RATES MOVE WITH TIME, so an anchor is never quoted without its period. BIO's three cohorts
put the likelihood of approval from phase I at 10.4% (2003-2011), 9.6% (2006-2015) and 7.9%
(2011-2020); Wong et al. (2019) estimate rates by year. This project's own endpoint-met rate
also falls by readout band (probe_training_design_20261001.txt section 1).

EVERY FIGURE CARRIES ITS VERIFICATION. `primary` means read from the source document itself
(the article, or the report PDF); `secondary` names where it was read instead. A secondary
figure is printed with that label and should be checked against the primary before it is
relied on.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

TARGET_ENDPOINT_MET = "endpoint_met"
TARGET_ADVANCEMENT = "advancement"
TARGET_MARKET = "market"
TARGETS = (TARGET_ENDPOINT_MET, TARGET_ADVANCEMENT, TARGET_MARKET)

VERIFIED_PRIMARY = "primary"
VERIFIED_SECONDARY = "secondary"
VERIFICATIONS = (VERIFIED_PRIMARY, VERIFIED_SECONDARY)


class Figure(NamedTuple):
    what: str                     # the quantity, in the source's own terms
    percent: Optional[float]      # as published; None when only counts are given
    numerator: Optional[int]      # counts, when the source gives them
    denominator: Optional[int]    # the source's n
    verification: str
    seen_in: str                  # where it was read: the source itself or the secondary


class Anchor(NamedTuple):
    key: str
    targets: tuple
    citation: str
    locator: str                  # doi or url
    period: str                   # the period the DATA cover, not the publication date
    counts: str                   # what is counted, so the definition gap is visible
    figures: tuple


_THOMAS_2016_PDF = ("https://cdn.vanderbilt.edu/t2-main/medschool-prd/wp-content/uploads/"
                    "sites/106/2025/05/BiomedtrackerPDF.pdf (copy of the BIO report)")
_BIO_2021_PAGE = ("https://www.bio.org/clinical-development-success-rates-and-contributing-"
                  "factors-2011-2020")

ANCHORS: tuple = (
    Anchor(
        "dechartres_2016", (TARGET_ENDPOINT_MET,),
        "Dechartres A, Bond EG, Scheer J, Riveros C, Atal I, Ravaud P. Reporting of "
        "statistically significant results at ClinicalTrials.gov for completed superiority "
        "randomized controlled trials. BMC Medicine 2016;14:192.",
        "doi:10.1186/s12916-016-0740-1",
        "ClinicalTrials.gov searched 2 June 2015, no date limit (17,536 studies with results)",
        "completed, randomized, two-arm phase 3 or 4 superiority trials with results posted; "
        "the FIRST primary outcome; significant at p < 0.05",
        (Figure("significant, among trials posting an effect estimate or p-value", None,
                844, 1400, VERIFIED_PRIMARY, "the article"),
         Figure("significant, p-value computed from per-arm results where none was posted",
                None, 342, 929, VERIFIED_PRIMARY, "the article")),
    ),
    Anchor(
        "hay_2014", (TARGET_ADVANCEMENT, TARGET_MARKET),
        "Hay M, Thomas DW, Craighead JL, Economides C, Rosenthal J. Clinical development "
        "success rates for investigational drugs. Nature Biotechnology 2014;32(1):40-51.",
        "Nature Biotechnology 32(1):40-51, January 2014",
        "phase transitions 1 January 2003 to 31 December 2011 (BioMedTracker)",
        "phase transitions of independent development paths (drug x indication), 835 "
        "companies, 5,820 transitions",
        (Figure("likelihood of approval from phase I", 10.4, None, 5820, VERIFIED_SECONDARY,
                "Thomas et al. 2016, Methods ('near 10.4%')"),),
    ),
    Anchor(
        "thomas_2016", (TARGET_ADVANCEMENT, TARGET_MARKET),
        "Thomas DW, Burns J, Audette J, Carroll A, Dow-Hygelund C, Hay M. Clinical "
        "Development Success Rates 2006-2015. BIO Industry Analysis, June 2016 "
        "(Biotechnology Innovation Organization, Biomedtracker, Amplion).",
        _THOMAS_2016_PDF,
        "phase transitions 1 January 2006 to 31 December 2015",
        "company-sponsored, FDA registration-enabling programmes per indication; success = "
        "advanced / (advanced + suspended); 9,985 transitions, 7,455 programmes, 1,103 "
        "companies; any indication, no time window",
        (Figure("phase I to phase II", 63.2, None, 3582, VERIFIED_PRIMARY, "the report"),
         Figure("phase II to phase III", 30.7, None, 3862, VERIFIED_PRIMARY, "the report"),
         Figure("phase III to NDA/BLA submission", 58.1, None, 1491, VERIFIED_PRIMARY,
                "the report"),
         Figure("NDA/BLA to approval", 85.3, None, 1050, VERIFIED_PRIMARY, "the report"),
         Figure("likelihood of approval from phase III", 49.6, None, 2541, VERIFIED_PRIMARY,
                "the report"),
         Figure("likelihood of approval from phase I", 9.6, None, 9985, VERIFIED_PRIMARY,
                "the report")),
    ),
    Anchor(
        "bio_2021", (TARGET_ADVANCEMENT, TARGET_MARKET),
        "Biotechnology Innovation Organization, Informa Pharma Intelligence, QLS Advisors. "
        "Clinical Development Success Rates and Contributing Factors 2011-2020. BIO, "
        "February 2021.",
        _BIO_2021_PAGE,
        "phase transitions 1 January 2011 to 30 November 2020 (Biomedtracker)",
        "same method as Thomas et al. 2016; 12,728 transitions, 9,704 programmes",
        (Figure("likelihood of approval from phase I", 7.9, None, None, VERIFIED_PRIMARY,
                "BIO's own announcement (bio.org, GoodDayBIO archive)"),
         Figure("phase II to phase III", 28.9, None, None, VERIFIED_SECONDARY,
                "the report's key findings as reproduced by cn-healthcare.com, 2021-05-08"),
         Figure("phase I to phase II", 52.0, None, 4414, VERIFIED_SECONDARY,
                "lenz.io summary table; also a University of St. Gallen review"),
         Figure("phase III to NDA/BLA submission", 57.8, None, None, VERIFIED_SECONDARY,
                "University of St. Gallen review (alexandria.unisg.ch)")),
    ),
    Anchor(
        "wong_2019", (TARGET_ADVANCEMENT, TARGET_MARKET),
        "Wong CH, Siah KW, Lo AW. Estimation of clinical trial success rates and related "
        "parameters. Biostatistics 2019;20(2):273-286. Corrigendum: Biostatistics "
        "2019;20(2):366, doi:10.1093/biostatistics/kxy072 (Tables 1 and 2 corrected).",
        "doi:10.1093/biostatistics/kxx069",
        "trial data 1 January 2000 to 31 October 2015, estimated BY YEAR",
        "406,038 trial entries, 21,143 compounds (Trialtrove, Pharmaprojects); phase "
        "transitions inferred per drug development path",
        (),            # no figure quoted: read the CORRECTED tables before using any
    ),
)


def anchors_for(target: str) -> tuple:
    """The anchors for one target, in the order declared (oldest data period first)."""
    if target not in TARGETS:
        raise ValueError(f"target {target!r} not in {TARGETS}")
    return tuple(a for a in ANCHORS if target in a.targets)


def figure_percent(f: Figure) -> Optional[float]:
    """The figure as a percent: as published, or from its counts."""
    if f.percent is not None:
        return f.percent
    if f.numerator is not None and f.denominator:
        return 100.0 * f.numerator / f.denominator
    return None


def format_anchor(a: Anchor) -> list:
    """Printable lines: citation, period, what is counted, each figure with its n and
    verification. Never a single bare number."""
    lines = [f"{a.citation} [{a.locator}]", f"  data: {a.period}", f"  counts: {a.counts}"]
    if not a.figures:
        lines.append("  figures: none quoted here; read them from the source (see citation)")
    for f in a.figures:
        p = figure_percent(f)
        n = f" (n={f.denominator})" if f.denominator else ""
        counts = f" [{f.numerator}/{f.denominator}]" if f.numerator is not None else ""
        lines.append(f"  {f.what}: {p:.1f}%{counts}{n} -- {f.verification}, {f.seen_in}")
    return lines


# ---- D-69: the literature behind the bundle and the second reference ------------------------
class Support(NamedTuple):
    key: str
    citation: str
    locator: str
    supports: str                 # what this project takes from it
    seen_in: str                  # where the claim was read, 2026-10-19


FEATURE_LITERATURE: tuple = (
    Support("lo_2019",
            "Lo AW, Siah KW, Wong CH. Machine learning with statistical imputation for "
            "predicting drug approvals. Harvard Data Science Review 2019;1(1).",
            "doi:10.1162/99608f92.5c5f0525",
            "prior approval for another indication and sponsor track record are among the "
            "most important features for predicting approval (with trial outcome, status, "
            "accrual and duration, which exist only after a trial runs and are excluded here); "
            "data 2003 to 2015, AUC 0.78 phase 2 to approval and 0.81 phase 3 to approval",
            "the article's abstract (cancerx.mit.edu)"),
    Support("siah_2021",
            "Siah KW, Kelley NW, Ballerstedt S, Holzhauer B, Lyu T, Mettler D, Sun S, Wandel S, "
            "Zhong Y, Zhou B, Pan S, Zhou Y, Lo AW. Predicting drug approvals: the Novartis "
            "data science and artificial intelligence challenge. Patterns 2021;2(8):100312.",
            "doi:10.1016/j.patter.2021.100312",
            "drugs whose past trials more often had positive outcomes were more likely to "
            "succeed, prior approval interacts with trial outcomes, and sponsor track record "
            "matters (the D-69 track records and prior approval); developers typically "
            "benchmark with historical approval rates by therapeutic class and phase (the "
            "phase x disease-area reference); the challenge's own limits: features reflecting "
            "decisions already taken, and test-set feedback through a public leaderboard",
            "the article (MIT Open Access copy)"),
    Support("thomas_2016",
            "Thomas DW, Burns J, Audette J, Carroll A, Dow-Hygelund C, Hay M. Clinical "
            "Development Success Rates 2006-2015. BIO Industry Analysis, June 2016.",
            _THOMAS_2016_PDF,
            "phase transition success differs by disease area, the basis of a phase x "
            "disease-area benchmark",
            "SECONDARY: a pharmacytimes.com summary of BIO's disease-area results (the report's "
            "disease-area tables were not read here; check them before quoting)"),
)


def format_support(x: Support) -> list:
    return [f"{x.citation} [{x.locator}]", f"  supports: {x.supports}",
            f"  read in: {x.seen_in}"]
