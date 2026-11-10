param(
    [string]$Pack = (Join-Path $env:USERPROFILE "Downloads\MILG_Training_Packs_20261107\프레스\MILG_04_Press_Forming.zip"),
    [switch]$Install
)
$ErrorActionPreference = "Stop"
$Pack = (Resolve-Path -LiteralPath $Pack).Path
$ai = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
$arguments = @("--pack", $Pack, "--out", (Join-Path $ai "reports\press-pump"), "--state", (Join-Path $ai "state"))
if ($Install) { $arguments += "--install" }
Push-Location $ai
try {
    & python -X utf8 -u -m mlng_ai.train_press_pump @arguments
    if ($LASTEXITCODE -ne 0) { throw "Press pump training failed." }
} finally { Pop-Location }
