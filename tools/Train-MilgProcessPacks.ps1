param(
    [string]$PackDir = (Join-Path $env:USERPROFILE "Downloads\MILG_Training_Packs_20261107"),
    [string]$StateDir = "",
    [switch]$Install
)
$ErrorActionPreference = "Stop"
$ai = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
$PackDir = (Resolve-Path -LiteralPath $PackDir).Path
if (-not $StateDir) { $StateDir = Join-Path $ai "state" }
$StateDir = (Resolve-Path -LiteralPath $StateDir).Path
$arguments = @("--packs", $PackDir, "--state", $StateDir, "--out", (Join-Path $ai "reports\process-packs"))
if ($Install) { $arguments += "--install" }
Push-Location $ai
try {
    & python -X utf8 -u -m mlng_ai.train_process_packs @arguments
    if ($LASTEXITCODE -ne 0) { throw "Process pack training failed. See the preceding error." }
} finally { Pop-Location }
