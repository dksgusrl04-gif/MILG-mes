param(
    [string]$Pack = (Join-Path $env:USERPROFILE "Downloads\MILG_Training_Packs_20261107\용접\MILG_03_Spot_Welding.zip"),
    [switch]$Install
)
$ErrorActionPreference = "Stop"
$Pack = (Resolve-Path -LiteralPath $Pack).Path
$ai = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
$arguments = @("--pack", $Pack, "--state", (Join-Path $ai "state"), "--out", (Join-Path $ai "reports\spot-weld-quality"))
if ($Install) { $arguments += "--install" }
Push-Location $ai
try {
    & python -X utf8 -u -m mlng_ai.train_spot_weld_quality @arguments
    if ($LASTEXITCODE -ne 0) { throw "Spot weld quality training failed." }
} finally { Pop-Location }
