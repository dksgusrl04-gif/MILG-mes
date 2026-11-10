param([string]$StateDir = "", [int]$Port = 8765)
$ErrorActionPreference = "Stop"
$aiDir = Join-Path (Split-Path -Parent $PSScriptRoot) "ai"
if (-not $StateDir) { $StateDir = Join-Path $aiDir "state" }
Push-Location $aiDir
try {
    & python -X utf8 -u -m mlng_ai.pilot serve --state $StateDir --port $Port
    if ($LASTEXITCODE -ne 0) { throw "Pilot server exited with an error" }
} finally { Pop-Location }
