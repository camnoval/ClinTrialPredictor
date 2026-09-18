<#
  run_all.ps1  --  trial-pos: set up, verify, and run TOP training end to end.

  What it does (stops on the first fatal error):
    1. checks Python 3.11+
    2. finds the TOP phase_*.csv files inside your clone
    3. creates .venv and installs the package (pulls xgboost, sklearn, pytest)
    4. prints the real schema of your data (inspect_top.py)
    5. runs the gate (import check + tests)
    6. trains on TOP's own per-phase split and prints AUROC + PR-AUC

  Run it:
    powershell -ExecutionPolicy Bypass -File .\run_all.ps1

  Optional overrides:
    powershell -ExecutionPolicy Bypass -File .\run_all.ps1 -Phase II
    powershell -ExecutionPolicy Bypass -File .\run_all.ps1 -ProjectRoot "D:\code\novartis" -TopRoot "D:\code\novartis\data\TOP"
#>
param(
    [string]$ProjectRoot = "C:\Users\camer\Documents\Coding\novartis",
    [string]$TopRoot     = "C:\Users\camer\Documents\Coding\novartis\data\TOP",
    [ValidateSet("I", "II", "III", "all")]
    [string]$Phase       = "all"
)

$ErrorActionPreference = "Stop"

function Say($msg)  { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Warn($msg) { Write-Host $msg -ForegroundColor Yellow }
function Run($file, [string[]]$argv) {
    & $file @argv
    if ($LASTEXITCODE -ne 0) { throw "command failed (exit $LASTEXITCODE): $file $($argv -join ' ')" }
}

# --- 0. project root + Python version -------------------------------------
Say "Project root"
if (-not (Test-Path $ProjectRoot)) { throw "ProjectRoot not found: $ProjectRoot" }
Set-Location $ProjectRoot
"cwd: $((Get-Location).Path)"

$py = "python"
$ver = & $py -c "import sys;print('%d.%d' % sys.version_info[:2])"
"python: $ver"
if ([version]$ver -lt [version]"3.11") { throw "Python 3.11+ required; found $ver" }

# --- 1. locate the TOP phase CSVs -----------------------------------------
Say "Locating TOP phase CSVs under $TopRoot"
if (-not (Test-Path $TopRoot)) { throw "TopRoot not found: $TopRoot" }
$csvs = Get-ChildItem $TopRoot -Recurse -Filter "phase_*.csv" -ErrorAction SilentlyContinue
if (-not $csvs) {
    Warn "No phase_*.csv found. Tree under $TopRoot (depth 2):"
    Get-ChildItem $TopRoot -Recurse -Depth 2 | Select-Object FullName | Format-Table -AutoSize
    throw "phase CSVs not present. This clone likely needs the repo's own preprocessing " +
          "(benchmark\data_split.py, which needs raw_data.csv first). Paste the tree above and I'll adjust."
}
$dataDir = ($csvs | Select-Object -First 1).Directory.FullName
"data dir -> $dataDir"
$csvs | Select-Object Name | Format-Table -AutoSize

# heads-up if any of the six expected split files are missing
$expected = foreach ($p in "I", "II", "III") { foreach ($s in "train", "valid", "test") { "phase_${p}_${s}.csv" } }
$present  = $csvs | ForEach-Object { $_.Name }
$missing  = $expected | Where-Object { $_ -notin $present }
if ($missing) { Warn ("note: expected files not found (those phases will be skipped): " + ($missing -join ", ")) }

# --- 2. venv + install -----------------------------------------------------
Say "Creating .venv and installing (xgboost, sklearn, pytest)"
$venvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Run $py @("-m", "venv", ".venv") }
Run $venvPy @("-m", "pip", "install", "--upgrade", "pip")
Run $venvPy @("-m", "pip", "install", "-e", ".[dev]")

# --- 3. confirm the real schema -------------------------------------------
Say "Schema of $($csvs[0].Name)"
& $venvPy scripts\inspect_top.py $csvs[0].FullName   # informational; non-fatal

# --- 4. gate ---------------------------------------------------------------
Say "Gate (import check + tests)"
& $venvPy scripts\run_checks.py
if ($LASTEXITCODE -ne 0) { throw "gate is RED -- fix before training." }

# --- 5. train + report -----------------------------------------------------
Say "Training on TOP's own split -> AUROC + PR-AUC per phase"
& $venvPy scripts\train_top.py --data-dir $dataDir --phase $Phase
$trainRc = $LASTEXITCODE
if ($trainRc -ne 0) {
    Warn "train_top.py exited $trainRc (a phase file may be missing, or a fit was skipped). See output above."
}

Say "Done"
"data dir : $dataDir"
"phase(s) : $Phase"
"To rerun a single phase:  .\.venv\Scripts\python.exe scripts\train_top.py --data-dir `"$dataDir`" --phase II"