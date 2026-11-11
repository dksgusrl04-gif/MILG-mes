param(
    [string]$Data = (Join-Path $env:USERPROFILE "Downloads\MILG_Training_Packs_20261107\프레스\DDACS_CC_BY_simulation"),
    [switch]$Install
)
$ErrorActionPreference = "Stop"
$Data = (Resolve-Path -LiteralPath $Data).Path
$ai = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
$arguments = @("--data", $Data, "--state", (Join-Path $ai "state"), "--out", (Join-Path $ai "reports\ddacs"))
if ($Install) { $arguments += "--install" }
Push-Location $ai
try {
    & python -X utf8 -u -m mlng_ai.train_ddacs @arguments
    if ($LASTEXITCODE -ne 0) { throw "DDACS candidate training failed." }
} finally { Pop-Location }
