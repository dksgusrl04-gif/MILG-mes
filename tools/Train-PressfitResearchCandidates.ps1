param(
    [string]$DataDir = "",
    [string]$HoldoutTask = "",
    [double]$TargetFpr = 0.05
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$aiDir = Join-Path $repoRoot "ai"
$expectedTasks = @("insert_ni", "insert_plug_2", "insert_plug_3", "insert_typec")

# Keep non-ASCII folder names out of this script so Windows PowerShell 5 can
# discover the user's Korean Downloads path without source-encoding damage.
if ([string]::IsNullOrWhiteSpace($DataDir)) {
    $downloads = Join-Path $env:USERPROFILE "Downloads"
    if (-not (Test-Path -LiteralPath $downloads -PathType Container)) {
        throw "Downloads directory not found: $downloads. Pass -DataDir explicitly."
    }
    $frontier = @($downloads)
    $datasetMatches = @()
    for ($depth = 0; $depth -lt 5 -and $frontier.Count -gt 0 -and $datasetMatches.Count -eq 0; $depth++) {
        $children = @(
            foreach ($parent in $frontier) {
                Get-ChildItem -LiteralPath $parent -Directory -ErrorAction SilentlyContinue
            }
        )
        $datasetMatches = @(
            $children | Where-Object {
                $_.Name -eq "CRIA_insert_dataset" -and
                (Test-Path -LiteralPath (Join-Path $_.FullName "insert") -PathType Container)
            }
        )
        $frontier = @($children | ForEach-Object FullName)
    }
    if ($datasetMatches.Count -ne 1) {
        throw "Expected one CRIA_insert_dataset folder under Downloads, found $($datasetMatches.Count). Pass -DataDir with the intended dataset folder."
    }
    $DataDir = $datasetMatches[0].FullName
    Write-Host "Auto-detected CRIA data: $DataDir"
}

if (-not (Test-Path -LiteralPath (Join-Path $DataDir "insert") -PathType Container)) {
    throw "CRIA insert data directory not found: $DataDir\insert"
}
if ($TargetFpr -le 0 -or $TargetFpr -ge 1) {
    throw "TargetFpr must be between 0 and 1."
}

$inventory = foreach ($task in $expectedTasks) {
    $taskDir = Join-Path (Join-Path $DataDir "insert") $task
    $infoPath = Join-Path $taskDir "meta\info.json"
    if (-not (Test-Path -LiteralPath $infoPath -PathType Leaf)) {
        throw "Missing CRIA task metadata: $infoPath"
    }
    $info = Get-Content -Raw -LiteralPath $infoPath | ConvertFrom-Json
    $actual = @(Get-ChildItem -LiteralPath (Join-Path $taskDir "data") -Filter "*.parquet" -File -Recurse).Count
    if ($actual -ne [int]$info.total_episodes) {
        throw "$task is incomplete: metadata says $($info.total_episodes) episodes, found $actual parquet files."
    }
    [pscustomobject]@{ Task = $task; Episodes = $actual }
}

Write-Host "CRIA insertion research candidate training (not industrial interference-fit validation)"
$inventory | Format-Table -AutoSize | Out-Host
Write-Host "This creates isolated candidate models only. It does not modify MILG routes or production weights."

$dependencyCheck = @(
    "import numpy, pandas, scipy, pyarrow, sklearn, joblib, threadpoolctl"
)
& python -c $dependencyCheck
if ($LASTEXITCODE -ne 0) {
    throw "Missing training dependency. Run: python -m pip install -r `"$(Join-Path $aiDir 'requirements-cria.txt')`""
}

$runScript = Join-Path $PSScriptRoot "Train-CriaInsertion.ps1"
if ($HoldoutTask) {
    & $runScript -DataDir $DataDir -TargetFpr $TargetFpr -HoldoutTask $HoldoutTask
} else {
    & $runScript -DataDir $DataDir -TargetFpr $TargetFpr
}
if ($LASTEXITCODE -ne 0) { throw "CRIA candidate training failed. See the error above." }
