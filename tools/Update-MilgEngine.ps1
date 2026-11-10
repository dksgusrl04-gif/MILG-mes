param(
    [string]$MctDir = "C:\Users\dksgu\Downloads\MCT-20261105T021906Z-1-002\MCT",
    [string]$ScrewDir = "C:\Users\dksgu\Downloads\체결",
    [string]$ExtraDir = "C:\Users\dksgu\Downloads\압입-20261105T113317Z-1-001\압입",
    [string]$StateDir = "",
    [int]$MaxRows = 100000,
    [int]$Threads = 4,
    [switch]$Install
)
$ErrorActionPreference = "Stop"
$repoDir = Split-Path -Parent $PSScriptRoot
$aiDir = Join-Path $repoDir "ai"
if (-not $StateDir) { $StateDir = Join-Path $aiDir "state" }
$outputRoot = Join-Path $aiDir "reports\joint-engine"

function Invoke-MilgPython {
    param([string[]]$Arguments)
    & python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "MILG command failed; inspect the preceding error." }
}
function Find-MilgResult {
    param([string]$Root)
    if (-not (Test-Path -LiteralPath $Root)) { return $null }
    $latest = Get-ChildItem -LiteralPath $Root -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "report.json") } |
        Sort-Object Name -Descending | Select-Object -First 1
    if ($latest) { return $latest.FullName }
    return $null
}

Push-Location $aiDir
try {
    Invoke-MilgPython -Arguments @("-m", "pip", "install", "-r", "requirements-extra-screw.txt")
    $screwOutput = Join-Path $ScrewDir "_milg\screw_candidates"
    $extraOutput = Join-Path $ScrewDir "_milg\extra_screw_candidates"
    $screwResult = Find-MilgResult -Root $screwOutput
    $extraResult = Find-MilgResult -Root $extraOutput
    if (-not $screwResult) {
        Invoke-MilgPython -Arguments @("-X", "utf8", "-u", "-m", "mlng_ai.train_screw", "--data-dir", $ScrewDir,
            "--out", $screwOutput, "--threads", "$Threads")
        $screwResult = Find-MilgResult -Root $screwOutput
    }
    if ($extraResult) {
        $existing = Get-Content -LiteralPath (Join-Path $extraResult "report.json") -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $existing.aursad_training.cleaning -or -not $existing.clamping_training) { $extraResult = $null }
    }
    if (-not $extraResult) {
        Invoke-MilgPython -Arguments @("-X", "utf8", "-u", "-m", "mlng_ai.extra_screw", "--data-dir", $ExtraDir,
            "--out", $extraOutput, "--train", "both", "--threads", "$Threads")
        $extraResult = Find-MilgResult -Root $extraOutput
    }
    Write-Host "Training the existing MILG common-coefficient model and checking previous processes."
    Invoke-MilgPython -Arguments @("-X", "utf8", "-u", "-m", "mlng_ai.joint_engine", "build",
        "--mct-dir", $MctDir, "--screw-dir", $ScrewDir, "--extra-dir", $ExtraDir,
        "--out", $outputRoot, "--max-rows", "$MaxRows", "--threads", "$Threads")
    $resultDir = Find-MilgResult -Root $outputRoot
    $report = Get-Content -LiteralPath (Join-Path $resultDir "report.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Install) {
        Invoke-MilgPython -Arguments @("-X", "utf8", "-m", "mlng_ai.joint_engine", "install-specialists",
            "--screw-result", $screwResult, "--extra-result", $extraResult, "--state", $StateDir, "--by", "MILG-local")
        if ($report.gate.passed) {
            Invoke-MilgPython -Arguments @("-X", "utf8", "-m", "mlng_ai.joint_engine", "install-joint",
                "--bundle", (Join-Path $resultDir "joint.json"), "--state", $StateDir, "--by", "MILG-local")
            Write-Host "Joint model installed: MCT, synthetic press-fit, screw and AURSAD."
        } else {
            Write-Host "Joint regression gate failed. Existing MCT/press-fit routes are retained."
            Write-Host "Validation-selected screw/AURSAD specialists installed; regression candidate installed only if it beats the baseline."
        }
        Invoke-MilgPython -Arguments @("-X", "utf8", "-u", "-m", "mlng_ai.joint_engine", "verify",
            "--state", $StateDir, "--mct-dir", $MctDir, "--screw-dir", $ScrewDir, "--extra-dir", $ExtraDir,
            "--out", (Join-Path $resultDir "engine-verification.json"))
    }
    Write-Host "Evaluation:" (Join-Path $resultDir "report.md")
    Write-Host "State directory:" $StateDir
    Write-Host "This updates the local Python engine. Server deployments must use the same code and the intended tenant state directory."
} finally {
    Pop-Location
}
