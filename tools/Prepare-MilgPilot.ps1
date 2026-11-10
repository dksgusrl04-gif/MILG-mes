param(
    [string]$PublicDir = "",
    [string]$ScrewDir = "",
    [string]$MctSummary = "",
    [string]$StateDir = "",
    [int]$Threads = 4,
    [switch]$SkipDependencies
)
$ErrorActionPreference = "Stop"
$repoDir = Split-Path -Parent $PSScriptRoot
$aiDir = Join-Path $repoDir "ai"
if (-not $PublicDir) { $PublicDir = Join-Path $env:USERPROFILE "Downloads\MILG_Public_Data_20261107" }
if (-not $StateDir) { $StateDir = Join-Path $aiDir "state" }
# ASCII-only script: compatible with Windows PowerShell 5.1 encoding.
if (-not $ScrewDir) {
    $matches = @(Get-ChildItem -LiteralPath (Join-Path $env:USERPROFILE "Downloads") -Filter "s05_variations-in-upper-workpiece-fabrication.zip" -File -Recurse |
        Where-Object { $_.DirectoryName -notlike '*pilot-releases*' -and $_.DirectoryName -notlike '*PyScrew_v1.2.4_eval*' })
    if ($matches.Count -ne 1) { throw "Pass -ScrewDir explicitly: expected one source folder containing s05/s06." }
    $ScrewDir = $matches[0].DirectoryName
}
if (-not $MctSummary) {
    $matches = @(Get-ChildItem -LiteralPath (Join-Path $env:USERPROFILE "Downloads") -Filter "hmotp_cuts.csv" -File -Recurse |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.DirectoryName "stiehl_runs.csv") })
    if ($matches.Count -ne 1) { throw "Pass -MctSummary explicitly: expected one folder containing hmotp_cuts.csv and stiehl_runs.csv." }
    $MctSummary = $matches[0].DirectoryName
}
Push-Location $aiDir
try {
    if (-not $SkipDependencies) {
        & python -m pip install -r requirements-extra-screw.txt
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
    }
    & python -X utf8 -u -m mlng_ai.prepare_pilot --state $StateDir --public $PublicDir --screw-dir $ScrewDir --mct-summary $MctSummary --threads $Threads
    if ($LASTEXITCODE -ne 0) { throw "Pilot preparation failed. Active release was not replaced before verification." }
    Write-Host "Pilot engine applied. Start the local console with tools/Start-MilgPilot.ps1"
} finally { Pop-Location }
