$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$runRoot = Join-Path $repo 'ai\reports\shared-private'
$run = Get-ChildItem -LiteralPath $runRoot -Directory | Sort-Object Name -Descending | Select-Object -First 1
if (-not $run -or -not (Test-Path -LiteralPath (Join-Path $run.FullName 'frozen.json'))) {
    throw "Frozen shared-private candidate not found under $runRoot"
}
$screwDir = Join-Path $env:USERPROFILE 'Downloads\체결'
$mctDir = Join-Path $env:USERPROFILE 'Downloads\MCT-20261105T021906Z-1-002\MCT'
$extraDir = Join-Path $env:USERPROFILE 'Downloads\체결'
foreach ($path in @($screwDir, $mctDir, $extraDir)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required source folder missing: $path" }
}
$env:PYTHONPATH = Join-Path $repo 'ai'
python -X utf8 -u -m mlng_ai.failure_analysis `
    --run $run.FullName `
    --out (Join-Path $repo 'ai\reports\failure-analysis') `
    --screw-dir $screwDir `
    --mct-dir $mctDir `
    --extra-dir $extraDir `
    --max-rows 100000
if ($LASTEXITCODE -ne 0) { throw 'Failure diagnostics did not finish; production state was not changed.' }

