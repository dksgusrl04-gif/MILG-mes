param(
    [Parameter(Mandatory=$true)][string]$InputFile,
    [string]$StateDir = "",
    [string]$Report = "",
    [switch]$Train
)
$ErrorActionPreference = "Stop"
$InputFile = (Resolve-Path -LiteralPath $InputFile).Path
$ai = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
if ($Train -and !$StateDir) { throw "Specify a dedicated -StateDir for training." }
if (!$StateDir) { $StateDir = Join-Path $ai "state" }
$StateDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($StateDir)
$live = (Resolve-Path -LiteralPath (Join-Path $ai "state")).Path
if ($Train -and $StateDir -eq $live) { throw "Training requires a separate pilot state." }
if ($Train -and !(Test-Path -LiteralPath $StateDir)) {
    New-Item -ItemType Directory -Path $StateDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $live "pilot-active.json") -Destination (Join-Path $StateDir "pilot-active.json")
}
$StateDir = (Resolve-Path -LiteralPath $StateDir).Path
if ($Train -and $StateDir -eq $live) { throw "Training requires a separate pilot state." }
if (!$Report) { $Report = Join-Path (Split-Path -Parent $InputFile) "milg-field-quality-report.json" }
$Report = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Report)
$arguments = @("--input", $InputFile, "--state", $StateDir, "--report", $Report)
if ($Train) { $arguments += "--train" }
Push-Location $ai
try {
    & python -X utf8 -u -m mlng_ai.field_quality_batch @arguments
    if ($LASTEXITCODE -ne 0) { throw "Field quality batch validation or training failed." }
} finally { Pop-Location }
