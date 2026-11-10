param(
    [string]$Archive = (Join-Path $env:USERPROFILE "Downloads\MILG_Training_Packs_20261107\체결\YangZhenye_Datasets.rar")
)
$ErrorActionPreference = "Stop"
$Archive = (Resolve-Path -LiteralPath $Archive).Path
$ai = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
Push-Location $ai
try {
    & python -X utf8 -u -m mlng_ai.evaluate_yang_screw --archive $Archive --out (Join-Path $ai "reports\yang-screw")
    if ($LASTEXITCODE -ne 0) { throw "Yang screw research evaluation failed." }
} finally { Pop-Location }
