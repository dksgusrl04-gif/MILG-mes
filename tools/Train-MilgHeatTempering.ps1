param(
    [string]$Pack = (Join-Path $env:USERPROFILE "Downloads\MILG_Training_Packs_20261107\열처리\drive-download-20261108T095632Z-1-001.zip"),
    [switch]$Install
)
$ErrorActionPreference = "Stop"
$Pack = (Resolve-Path -LiteralPath $Pack).Path
$ai = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
$arguments = @("--pack", $Pack, "--state", (Join-Path $ai "state"), "--out", (Join-Path $ai "reports\heat-tempering"))
if ($Install) { $arguments += "--install" }
Push-Location $ai
try {
    & python -X utf8 -u -m mlng_ai.train_heat_tempering @arguments
    if ($LASTEXITCODE -ne 0) { throw "Tempering training failed." }
} finally { Pop-Location }
