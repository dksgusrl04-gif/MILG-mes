param(
    [Parameter(Mandatory=$true)][string]$Run,
    [string]$Expansion = (Join-Path $env:USERPROFILE "Downloads\MILG_Training_Packs_20261107\용접\MILG_03_Spot_Welding_Expansion.zip")
)
$ErrorActionPreference = "Stop"
$Run = (Resolve-Path -LiteralPath $Run).Path
$Expansion = (Resolve-Path -LiteralPath $Expansion).Path
$ai = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
Push-Location $ai
try {
    & python -X utf8 -u -m mlng_ai.install_spot_weld_pilot --state (Join-Path $ai "state") --run $Run --expansion $Expansion
    if ($LASTEXITCODE -ne 0) { throw "Spot weld pilot installation failed." }
} finally { Pop-Location }
