param(
    [string]$DataDir = "C:\Users\dksgu\Downloads\압입-20261105T113317Z-1-001\압입\CRIA_insert_dataset",
    [string]$HoldoutTask = "",
    [double]$TargetFpr = 0.05
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$aiDir = Join-Path $repoRoot "ai"
if (-not (Test-Path -LiteralPath (Join-Path $DataDir "insert") -PathType Container)) {
    throw "Expected DataDir\insert folder: $DataDir"
}
$runArgs = @("-X", "utf8", "-u", "-m", "mlng_ai.train_cria", "--data-dir", $DataDir,
    "--out", (Join-Path $DataDir "_milg\cria_candidates"),
    "--target-fpr", $TargetFpr.ToString([Globalization.CultureInfo]::InvariantCulture))
if ($HoldoutTask) { $runArgs += @("--holdout-task", $HoldoutTask) }
Push-Location -LiteralPath $aiDir
try {
    & python @runArgs
    if ($LASTEXITCODE -ne 0) { throw "CRIA training failed. See the error above." }
} finally { Pop-Location }
