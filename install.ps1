# epiplan installer for Windows.
# Run it in PowerShell from this folder:
#     powershell -ExecutionPolicy Bypass -File install.ps1
#
$ErrorActionPreference = "Stop"
$Src        = Split-Path -Parent $MyInvocation.MyCommand.Path
$App        = Join-Path $env:LOCALAPPDATA "epiplan-app"
$BinDir     = Join-Path $env:LOCALAPPDATA "epiplan-bin"
$ConfigDir  = Join-Path $env:USERPROFILE ".config\epiplan"

function Say($m) { Write-Host "`n$m" -ForegroundColor Cyan }

# ---- 1. Python -------------------------------------------------------------
$Py = $null
foreach ($c in @("py", "python")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { $Py = $c; break }
}
if (-not $Py) {
    Write-Host "Python 3 is required. Install it from https://python.org (tick 'Add to PATH'), then run this again." -ForegroundColor Red
    exit 1
}

# ---- 2. Copy the app -------------------------------------------------------
Say "Installing epiplan into $App"
New-Item -ItemType Directory -Force -Path $App, $BinDir, $ConfigDir | Out-Null
Copy-Item (Join-Path $Src "epiplan.py")    (Join-Path $App "epiplan.py")    -Force
foreach ($doc in @("README.md", "README.fr.md")) {
    $p = Join-Path $Src $doc
    if (Test-Path $p) { Copy-Item $p (Join-Path $App $doc) -Force }
}

# ---- 3. Virtual env + windows-curses (required) + Playwright (optional) ----
Say "Setting up Python packages - this may take a minute"
& $Py -m venv (Join-Path $App ".venv")
$VenvPy = Join-Path $App ".venv\Scripts\python.exe"
& $VenvPy -m pip install --quiet --upgrade pip windows-curses | Out-Null
Write-Host "  planner UI ready (windows-curses installed)."
try {
    & $VenvPy -m pip install --quiet playwright | Out-Null
    Write-Host "  automatic login ready (uses your installed Google Chrome)."
} catch {
    Write-Host "  could not install Playwright - you can still log in with: epiplan login --paste"
}

# ---- 4. The 'epiplan' command ---------------------------------------------
$Launcher = Join-Path $BinDir "epiplan.cmd"
"@echo off`r`n`"$VenvPy`" `"$App\epiplan.py`" %*" | Set-Content -Encoding ASCII $Launcher

# put epiplan-bin on the user PATH (once)
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User")
    Write-Host "  added epiplan to your PATH (open a NEW terminal to use it)."
}

# ---- 5. Language + daily break --------------------------------------------
Say "Choose a language / Choisis une langue"
Write-Host "  1) English"
Write-Host "  2) Francais"
Write-Host "  3) Auto-detect / detection automatique"
$choice = Read-Host "Your choice [1/2/3, default 3]"
switch ($choice) { "1" { $Lang = "en" } "2" { $Lang = "fr" } default { $Lang = "" } }

Say "Daily break to keep free / Pause quotidienne a garder libre"
Write-Host "  e.g. a lunch break. Leave empty for none."
$Break = Read-Host "From-to time [HH:MM-HH:MM, e.g. 12:30-13:30, empty = none]"

$env:LANG_CHOICE = $Lang
$env:BREAK = $Break
$env:EPIPLAN_CFG = Join-Path $ConfigDir "config.json"
# Single-quoted here-string so PowerShell leaves the Python (and its $ names) untouched; the code is
# piped to `python -`, and its inputs arrive through environment variables set just above.
$ConfigCode = @'
import json, os, re
path = os.environ["EPIPLAN_CFG"]
try:
    with open(path) as f:
        cfg = json.load(f)
except (FileNotFoundError, ValueError):
    cfg = {}
cfg["lang"] = os.environ.get("LANG_CHOICE", "")
m = re.match(r"\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$", os.environ.get("BREAK", ""))
if m:
    cfg["breaks"] = [{"days": "mon-fri", "start": m.group(1), "end": m.group(2), "label": "Lunch"}]
elif os.environ.get("BREAK", "").strip() == "":
    cfg["breaks"] = []
with open(path, "w") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
'@
$ConfigCode | & $VenvPy -

# ---- 6. Where things are + next steps -------------------------------------
$Readme = Join-Path $App "README.md"
if ($Lang -eq "fr" -and (Test-Path (Join-Path $App "README.fr.md"))) { $Readme = Join-Path $App "README.fr.md" }

Say "Done!"
Write-Host "The guide (README) is here:"
Write-Host "    $Readme"
Write-Host ""
Write-Host "Open a NEW terminal, then:"
Write-Host "    epiplan login             # connect your Epitech intra account (once)"
Write-Host "    epiplan                   # open the planner"
Write-Host "    epiplan notifications on  # reminders before activities"
Write-Host ""
Write-Host "Switch language any time with:  epiplan lang en   |   epiplan lang fr"
