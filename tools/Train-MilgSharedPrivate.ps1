param(
    [string]$MctDir = "C:\Users\dksgu\Downloads\MCT-20261105T021906Z-1-002\MCT",
    [string]$ScrewDir = "C:\Users\dksgu\Downloads\체결",
    [string]$ExtraDir = "C:\Users\dksgu\Downloads\압입-20261105T113317Z-1-001\압입",
    [int]$Epochs = 40,
    [int]$Threads = 4,
    [string]$StateDir = "",
    [switch]$Evaluate,
    [switch]$Install
)
$ErrorActionPreference = "Stop"
if ($Install -and -not $Evaluate) { throw "-Install requires -Evaluate. Training alone never changes the engine." }
$repoDir = Split-Path -Parent $PSScriptRoot
$aiDir = Join-Path $repoDir "ai"
if (-not $StateDir) { $StateDir = Join-Path $aiDir "state" }
$outputRoot = Join-Path $aiDir "reports\shared-private"
function Invoke-MilgNeural {
    param([string[]]$Arguments)
    & python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "MILG neural command failed; inspect the preceding error." }
}
Push-Location $aiDir
try {
    Invoke-MilgNeural -Arguments @("-m", "pip", "install", "-r", "requirements-neural.txt")
    Invoke-MilgNeural -Arguments @("-X", "utf8", "-u", "-m", "mlng_ai.shared_private", "train",
        "--mct-dir", $MctDir, "--screw-dir", $ScrewDir, "--extra-dir", $ExtraDir,
        "--out", $outputRoot, "--epochs", "$Epochs", "--threads", "$Threads")
    $latest = Get-ChildItem -LiteralPath $outputRoot -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "frozen.json") } |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) { throw "No completed neural candidate found." }
    $runDir = $latest.FullName
    if ($Evaluate) {
        Invoke-MilgNeural -Arguments @("-X", "utf8", "-u", "-m", "mlng_ai.shared_private", "evaluate", "--run", $runDir)
    }
    if ($Install) {
        if (Test-Path -LiteralPath (Join-Path $runDir "deployment.json")) {
            Invoke-MilgNeural -Arguments @("-X", "utf8", "-m", "mlng_ai.shared_private", "install",
                "--run", $runDir, "--state", $StateDir, "--by", "MILG-local")
            Invoke-MilgNeural -Arguments @("-X", "utf8", "-u", "-m", "mlng_ai.joint_engine", "verify",
                "--state", $StateDir, "--mct-dir", $MctDir, "--screw-dir", $ScrewDir, "--extra-dir", $ExtraDir,
                "--out", (Join-Path $runDir "engine-verification.json"))
        } else { Write-Host "Regression/discrimination gate failed. Existing engine models retained." }
    }
    Write-Host "Neural model artifacts:" $runDir
    if (-not $Evaluate) {
        Write-Host "Test not scored. Separate frozen-model evaluation command:"
        Write-Host "python -X utf8 -u -m mlng_ai.shared_private evaluate --run `"$runDir`""
    }
} finally { Pop-Location }
