param([string]$StateDir = '', [string]$OutDir = '', [switch]$IncludeWheels)
$ErrorActionPreference = 'Stop'
$repoDir = Split-Path -Parent $PSScriptRoot
if (-not $StateDir) { $StateDir = Join-Path $repoDir 'ai\state' }
if (-not $OutDir) { $OutDir = Join-Path ([Environment]::GetFolderPath('UserProfile')) ('Downloads\MILG_Delivery_' + (Get-Date -Format 'yyyyMMdd_HHmmss')) }
$env:OMP_NUM_THREADS = '2'
$env:OPENBLAS_NUM_THREADS = '2'
Push-Location (Join-Path $repoDir 'ai')
try {
    if ($IncludeWheels) {
        & python -X utf8 -m mlng_ai.delivery package --state $StateDir --out $OutDir --with-wheels
    } else {
        & python -X utf8 -m mlng_ai.delivery package --state $StateDir --out $OutDir
    }
    if ($LASTEXITCODE -ne 0) { throw 'Delivery acceptance/package failed; inspect verification report' }
} finally { Pop-Location }
