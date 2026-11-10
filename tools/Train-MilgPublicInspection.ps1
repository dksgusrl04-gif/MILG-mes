param(
    [string]$PackDir = (Join-Path $env:USERPROFILE "Downloads\MILG_Training_Packs_20261107")
)
$ErrorActionPreference = "Stop"
$PackDir = (Resolve-Path -LiteralPath $PackDir).Path
$ai = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
Push-Location $ai
try {
    & python -X utf8 -u -m mlng_ai.train_public_inspection --cip (Join-Path $PackDir "MCT\CiP_DMD_research") --rddac (Join-Path $PackDir "프레스\RDDAC_CC_BY") --out (Join-Path $ai "reports\public-inspection")
    if ($LASTEXITCODE -ne 0) { throw "Public inspection training failed." }
} finally { Pop-Location }
