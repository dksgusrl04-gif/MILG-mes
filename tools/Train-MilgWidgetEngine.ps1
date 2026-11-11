param([string]$Summaries = "", [string]$StateDir = "")
$ErrorActionPreference = "Stop"
$repoDir = Split-Path -Parent $PSScriptRoot
$aiDir = Join-Path $repoDir "ai"
if (!$Summaries) { $Summaries = Join-Path $aiDir "reports\widget-engine" }
if (!$StateDir) { $StateDir = Join-Path $aiDir "state" }
if (!(Test-Path -LiteralPath (Join-Path $Summaries 'inj_quality.csv'))) { throw "Audited inj_quality.csv summary not found: $Summaries" }
Push-Location $aiDir
try {
    python -X utf8 -u -m mlng_ai.train_widget_engine --summaries "$Summaries" --state "$StateDir" --out "reports/widget-engine" --install
    if ($LASTEXITCODE -ne 0) { throw 'Widget model evaluation failed.' }
} finally { Pop-Location }
