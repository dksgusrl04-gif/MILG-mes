param([int]$Port = 8765, [switch]$InstallDependencies, [switch]$Offline,
      [string]$LearningPolicy = '', [string[]]$AllowedOrigins = @(), [switch]$NoAutoLearning)
$ErrorActionPreference = 'Stop'
$deliveryRoot = $PSScriptRoot
if ($LearningPolicy) { $LearningPolicy = (Resolve-Path -LiteralPath $LearningPolicy).Path }
$venvPython = Join-Path $deliveryRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    if (-not $InstallDependencies) { throw 'First run: .\Start-MilgDelivery.ps1 -InstallDependencies (requires Python and network/package mirror)' }
    & python -m venv (Join-Path $deliveryRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python venv creation failed' }
}
if ($InstallDependencies) {
    if ($Offline) {
        $wheelDir = Join-Path $deliveryRoot 'wheels'
        if (-not (Test-Path -LiteralPath $wheelDir)) { throw 'This package does not contain offline wheels' }
        & $venvPython -m pip install --no-index --find-links $wheelDir -r (Join-Path $deliveryRoot 'requirements-runtime.txt')
    } else {
        & $venvPython -m pip install -r (Join-Path $deliveryRoot 'requirements-runtime.txt')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency installation failed' }
}
$aiDir = Join-Path $deliveryRoot 'ai'
$stateDir = Join-Path $deliveryRoot 'runtime-state'
$env:OMP_NUM_THREADS = '2'
$env:OPENBLAS_NUM_THREADS = '2'
Push-Location $aiDir
try {
    & $venvPython -X utf8 -m mlng_ai.delivery verify-package --source $deliveryRoot
    if ($LASTEXITCODE -ne 0) { throw 'Package verification failed' }
    if (-not (Test-Path -LiteralPath $stateDir)) {
        & $venvPython -X utf8 -m mlng_ai.delivery install --source (Join-Path $deliveryRoot 'snapshot') --state $stateDir
        if ($LASTEXITCODE -ne 0) { throw 'Snapshot installation failed' }
    }
    if ($LearningPolicy) {
        & $venvPython -X utf8 -m mlng_ai.learning configure --state $stateDir --input $LearningPolicy
        if ($LASTEXITCODE -ne 0) { throw 'Learning policy configuration failed' }
    }
    $serverArgs = @('-X','utf8','-u','-m','mlng_ai.pilot','serve','--state',$stateDir,'--port',"$Port")
    if (-not $NoAutoLearning) { $serverArgs += '--auto-learn' }
    foreach ($origin in $AllowedOrigins) { $serverArgs += @('--allow-origin',$origin) }
    & $venvPython @serverArgs
    if ($LASTEXITCODE -ne 0) { throw 'Engine stopped with an error' }
} finally { Pop-Location }
