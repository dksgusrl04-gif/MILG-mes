param(
    [string]$DataDir = "C:\Users\dksgu\Downloads\MCT-20261105T021906Z-1-002\MCT",
    [string]$ModelDir = "",
    [switch]$Quick
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$aiDir = Join-Path $repoRoot "ai"
$mctData = Join-Path $DataDir "_milg"
if (-not $ModelDir) { $ModelDir = Join-Path $mctData "causalman_pressfit" }
foreach ($required in @(
    (Join-Path $mctData "hmotp_cuts.csv"),
    (Join-Path $ModelDir "model.json"),
    (Join-Path $ModelDir "preprocessing.json"),
    (Join-Path $ModelDir "report.json"),
    (Join-Path $aiDir "state\base\mct.json"),
    (Join-Path $DataDir "data.zip.001"),
    (Join-Path $DataDir "data.zip.002"),
    (Join-Path $DataDir "data.zip.003")
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing file: $required" }
}
$evalArgs = @("-X", "utf8", "-m", "mlng_ai.evaluate_processes", "--mct-data", $mctData,
    "--pressfit-data", $DataDir, "--pressfit-model", $ModelDir,
    "--out", (Join-Path $aiDir "reports\process-evaluation"))
if ($Quick) { $evalArgs += "--quick" }
Push-Location -LiteralPath $aiDir
try {
    & python @evalArgs
    if ($LASTEXITCODE -ne 0) { throw "Evaluation failed. Check the error above." }
} finally { Pop-Location }
