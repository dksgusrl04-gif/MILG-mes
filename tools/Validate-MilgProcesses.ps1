param(
    [string]$DataDir = "C:\Users\dksgu\Downloads\MCT-20261105T021906Z-1-002\MCT",
    [int]$MaxRows = 100000,
    [double]$TargetFpr = 0.05,
    [switch]$Quick
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$aiDir = Join-Path $repoRoot "ai"
$runArgs = @("-X", "utf8", "-m", "mlng_ai.strict_evaluation",
    "--data-dir", $DataDir, "--max-rows", "$MaxRows",
    "--target-fpr", $TargetFpr.ToString([Globalization.CultureInfo]::InvariantCulture),
    "--out", (Join-Path $aiDir "reports\strict-validation"))
if ($Quick) { $runArgs += "--quick" }
Push-Location -LiteralPath $aiDir
try {
    & python @runArgs
    if ($LASTEXITCODE -ne 0) { throw "Validation failed. See the error above." }
} finally { Pop-Location }
