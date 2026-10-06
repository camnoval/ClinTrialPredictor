# trial-pos — clinical-trial success predictor (Gen 3)

Predicts, for a registered clinical trial, three separately calibrated probabilities: that
it meets its primary endpoint, that its programme advances, and that its drug reaches the
US market for the trial's indication. Built on ClinicalTrials.gov (via AACT), DrugCentral
and Drugs@FDA. The working record is in `docs/` — the handoffs, read newest first.

## Run it

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/camnoval/ClinTrialPredictor)

The button builds the environment, downloads the pinned data bundle from Zenodo, verifies
every file's sha256, and runs the checks. Nothing else is needed: no credentials, no
database.

Locally:

```
conda env create -f environment.yml
conda activate trial-pos
python scripts/bootstrap.py
```

`bootstrap.py --tier run rebuild` also fetches the inputs needed to rebuild the derived
files from source (the DrugCentral archive, the raw AACT dumps, the Drugs@FDA zip).

## Data

Pinned bundle: DOI [10.5281/zenodo.23197696](https://doi.org/10.5281/zenodo.23197696) — every file, its size and sha256 are in
`data_sources.json`. The sources cannot be re-fetched at the version used here (AACT's
live database changes nightly, DrugCentral's download serves only the latest release,
Drugs@FDA is overwritten daily), which is why the bundle exists. Rebuilding from source
produces a NEW bundle version; it never overwrites a pinned one.

Licences of the redistributed sources are recorded per source in
`src/trial_pos/services/data_registry.py`.

## Layout

```
src/trial_pos/services/   all logic, with tests
scripts/                  thin I/O: pulls, extraction, audits, bootstrap
audit/                    scratch probes and diagnostics
tests/                    python scripts/run_checks.py
archive/                  two superseded projects; not current
```
