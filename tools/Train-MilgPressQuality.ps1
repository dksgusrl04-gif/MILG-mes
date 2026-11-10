param(
    [string]$Data = (Join-Path $env:USERPROFILE "Downloads\MILG_Training_Packs_20261107\프레스\P2S_progressive_press_proxy"),
    [switch]$Install
)
$ErrorActionPreference = "Stop"
$Data = (Resolve-Path -LiteralPath $Data).Path
$ai = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
$arguments = @("--data", $Data, "--state", (Join-Path $ai "state"), "--out", (Join-Path $ai "reports\press-quality"))
if ($Install) { $arguments += "--install" }
Push-Location $ai
try {
    & python -X utf8 -u -m mlng_ai.train_press_quality @arguments
    if ($LASTEXITCODE -ne 0) { throw "Press quality training failed." }
} finally { Pop-Location }
