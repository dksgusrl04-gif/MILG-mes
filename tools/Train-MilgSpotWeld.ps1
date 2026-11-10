param(
    [string]$Pack = (Join-Path $env:USERPROFILE "Downloads\MILG_Training_Packs_20261107\용접\MILG_03_Spot_Welding.zip")
)
$ErrorActionPreference = "Stop"
$Pack = (Resolve-Path -LiteralPath $Pack).Path
$ai = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
Push-Location $ai
try {
    & python -X utf8 -u -m mlng_ai.train_spot_weld --pack $Pack --out (Join-Path $ai "reports\spot-weld")
    if ($LASTEXITCODE -ne 0) { throw "Spot weld training failed. See the preceding error." }
} finally { Pop-Location }
