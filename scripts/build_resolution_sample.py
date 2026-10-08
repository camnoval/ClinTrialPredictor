#!/usr/bin/env python3
"""Draw one tranche of the hand-labelled sample that measures trial-to-drug resolution.

Writes into --out-dir, refusing to overwrite:
  resolution_sample_t<N>.csv        the BLIND sheet: no resolver output, no stratum
  resolution_sample_t<N>.key.csv    the key: stratum, rank, the resolver's answer
  resolution_sample_instructions.md how to fill the sheet

The frame is market-eligible pivotal drug trials (eligibility.eligible_for_market at
--snapshot and --market-window, with the first-submitted date merged exactly as
audit_posting_bias.py merges it). Design and targets live in services/resolution_sample.py.
Tranche 2 continues down the same order: pass --tranche 2 --start <tranche-1 size>.

Usage (PowerShell):
  python scripts\\build_resolution_sample.py --snapshot 2026-10-01
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.aact_fields import output_name  # noqa: E402
from trial_pos.services.eligibility import (  # noqa: E402
    DEFAULT_MARKET_WINDOW_YEARS, ELIGIBLE, REGISTRATION_SUBMITTED_FIELD,
    eligible_for_market, merge_registration_timing,
)
from trial_pos.services.population import tribool  # noqa: E402
from trial_pos.services.resolution_sample import (  # noqa: E402
    ANSWERS, CONFIDENCE, DEFAULT_SALT, KEY_COLUMNS, NOT_IN_DRUGCENTRAL, PRECISION_GATE,
    RECALL_GATE, SHEET_COLUMNS, STRATA, TARGET_HALF_WIDTH, key_row, n_for_half_width,
    ordered_strata, presentation, sheet_row, strata, tranche,
)

csv.field_size_limit(10 ** 9)

DEFAULT_RESOLUTION = Path("data") / "labels" / "trial_drug_resolution.csv"
DEFAULT_LABELS = Path("data") / "aact" / "trial_labels.csv"
DEFAULT_FIELDS = Path("data") / "aact" / "trial_registration_fields.csv"
DEFAULT_OUT_DIR = Path("data") / "labels"
SHEET = "resolution_sample_t{n}.csv"
KEY = "resolution_sample_t{n}.key.csv"
INSTRUCTIONS = "resolution_sample_instructions.md"

INSTRUCTIONS_TEXT = f"""# Trial-to-drug resolution: labelling instructions

One row per trial. Open the `url` (ClinicalTrials.gov) and read the arms and interventions.

- **tested_agents**: the drug or drugs this trial TESTS, separated by `;`. Not placebo, not
  the comparator, not background therapy every arm receives. A combination tested together
  is listed as its parts.
- **drugcentral_ids**: for each tested agent, in the same order and separated by `;`, its
  DrugCentral id (search drugcentral.org; the id is the number in the drug page's URL).
  Write `{NOT_IN_DRUGCENTRAL}` for an agent DrugCentral does not have.
- **any_fda_approved**: `{ANSWERS[0]}` if at least one tested agent has ever been approved by
  the FDA, for any indication, as of 2026-10-06 (CDER or CBER); `{ANSWERS[1]}` if none;
  `{ANSWERS[2]}` if you cannot tell. Unclear is counted separately, never read as no.
- **approval_cber_only**: `{ANSWERS[0]}` if every approval you found is a CBER (biologics
  centre: vaccines, blood products, cell and gene therapy) approval; otherwise
  `{ANSWERS[1]}`. Blank when any_fda_approved is not `{ANSWERS[0]}`.
- **notes**: optional.

Do not look at any file other than this sheet and the trial's registry page. The sheet is
blind on purpose: it does not say what the resolver found, and the order says nothing.
Leave no required cell blank: a blank is a skipped row and the scorer refuses to run.
"""


def _rule(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def read_rows(path: Path, need: tuple):
    if not path.exists():
        raise SystemExit(f"!! {path} not found")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in need if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"!! {path} lacks {missing}; has {reader.fieldnames}")
        yield from reader


def main() -> int:
    # Redirected output on Windows is cp1252, which cannot encode e.g. the Greek beta in
    # 'interleukin-1\u03b2'. Escape what the console cannot show rather than crash.
    sys.stdout.reconfigure(errors="backslashreplace")
    target_n = n_for_half_width(PRECISION_GATE, TARGET_HALF_WIDTH)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--resolution", type=Path, default=DEFAULT_RESOLUTION)
    ap.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    ap.add_argument("--fields", type=Path, default=DEFAULT_FIELDS)
    ap.add_argument("--snapshot", required=True,
                    help="ISO date fixing 'now' for the window; never read from the clock")
    ap.add_argument("--market-window", type=int, default=DEFAULT_MARKET_WINDOW_YEARS)
    ap.add_argument("--salt", default=DEFAULT_SALT)
    ap.add_argument("--tranche", type=int, default=1)
    ap.add_argument("--start", type=int, default=0, help="first rank in each stratum")
    ap.add_argument("--per-stratum", type=int, default=target_n,
                    help="rows per stratum; default is the precision target at the gate")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = ap.parse_args()
    snapshot = date.fromisoformat(args.snapshot)
    out = {"sheet": args.out_dir / SHEET.format(n=args.tranche),
           "key": args.out_dir / KEY.format(n=args.tranche),
           "instructions": args.out_dir / INSTRUCTIONS}
    for name in ("sheet", "key"):
        if out[name].exists():
            raise SystemExit(f"!! {out[name]} exists; a drawn tranche is never redrawn")

    _rule("SETTINGS")
    print(f"  resolution {args.resolution} | labels {args.labels} | fields {args.fields}")
    print(f"  snapshot {snapshot} | market window {args.market_window}y | salt "
          f"{args.salt!r} | tranche {args.tranche} | start {args.start} | per stratum "
          f"{args.per_stratum}")
    print(f"  gates: recall {RECALL_GATE}, precision {PRECISION_GATE} | target half-width "
          f"{TARGET_HALF_WIDTH} at {CONFIDENCE}")
    print(f"  smallest n meeting the half-width: recall stratum "
          f"{n_for_half_width(RECALL_GATE, TARGET_HALF_WIDTH)} truly-approved trials, "
          f"precision {target_n} matched trials")

    column = output_name("studies", REGISTRATION_SUBMITTED_FIELD)
    submitted = {r["nct_id"].strip().upper(): r[column]
                 for r in read_rows(args.fields, ("nct_id", column))}
    labels = list(read_rows(args.labels, ("nct_id", "is_drug_trial", "phase")))
    frame = [r["nct_id"] for r in merge_registration_timing(labels, submitted)
             if eligible_for_market(r, snapshot, args.market_window)["verdict"] == ELIGIBLE]
    matched = {r["nct_id"]: tribool(r["matched"])
               for r in read_rows(args.resolution, ("nct_id", "matched", "selection_outcome",
                                                    "drugs"))}
    resolution = {r["nct_id"]: r for r in read_rows(args.resolution, ("nct_id",))}
    members = strata(matched, frame)
    ordered = ordered_strata(members, args.salt)
    chosen = tranche(ordered, args.start, args.per_stratum)
    shown = presentation(chosen, args.salt)

    _rule("FRAME AND DRAW")
    print(f"  market-eligible trials: {len(frame)}")
    for s in STRATA:
        print(f"  {s:12s} frame {len(members[s]):7d}  drawn {len(chosen[s]):5d}  ranks "
              f"{args.start}..{args.start + len(chosen[s]) - 1 if chosen[s] else '-'}")
        if len(chosen[s]) < args.per_stratum:
            print(f"  !! {s}: stratum exhausted; fewer rows than asked")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with out["sheet"].open("w", newline="", encoding="utf-8") as fs, \
            out["key"].open("w", newline="", encoding="utf-8") as fk:
        ws = csv.DictWriter(fs, fieldnames=SHEET_COLUMNS)
        wk = csv.DictWriter(fk, fieldnames=KEY_COLUMNS)
        ws.writeheader()
        wk.writeheader()
        for sample_id, s, rank, nct in shown:
            ws.writerow(sheet_row(sample_id, nct))
            wk.writerow(key_row(sample_id, s, rank, args.tranche, resolution[nct]))
    out["instructions"].write_text(INSTRUCTIONS_TEXT, encoding="utf-8")
    _rule("WRITTEN")
    for p in out.values():
        print(f"  {p}")
    print("  Fill the sheet only. Do not open the key until every row is labelled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())