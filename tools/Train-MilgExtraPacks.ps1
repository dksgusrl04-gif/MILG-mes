param(
    [string]$RawDir = "",
    [string]$StateDir = ""
)
$ErrorActionPreference = "Stop"
$repoDir = Split-Path -Parent $PSScriptRoot
$aiDir = Join-Path $repoDir "ai"
if (!$RawDir) { $RawDir = Join-Path $aiDir "reports\packs-recheck\raw" }
if (!$StateDir) { $StateDir = Join-Path $aiDir "state" }
if (!(Test-Path -LiteralPath $RawDir -PathType Container)) { throw "Audited raw pack directory not found: $RawDir" }
Push-Location $aiDir
try {
    python -X utf8 -u -m mlng_ai.packs_extra --raw "$RawDir" --out "reports/packs-recheck" --state "$StateDir" --install
    if ($LASTEXITCODE -ne 0) { throw "Extra pack training failed; inspect the error above." }
} finally { Pop-Location }
