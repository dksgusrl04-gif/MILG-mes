<#
  MLNG MES 자체 서버를 내 PC(Windows)에서 띄운다 — 무료 (Docker Desktop 개인 사용 무료)
  PowerShell 에서 (server 폴더 기준, 한 줄씩):
    powershell -ExecutionPolicy Bypass -File scripts\local.ps1              # 띄우기 → http://localhost:8080
    powershell -ExecutionPolicy Bypass -File scripts\local.ps1 -Public      # + 인터넷 공개 주소 (https://xxxx.trycloudflare.com)
    powershell -ExecutionPolicy Bypass -File scripts\local.ps1 -Stop        # 멈추기 (데이터는 남음)
    powershell -ExecutionPolicy Bypass -File scripts\local.ps1 -Reset       # 멈추고 DB 까지 지우기
  하는 일: Docker 확인 → .env (무작위 비밀번호) → docker compose up --build → 요청 시험 13개 → 브라우저 열기
#>
param([int]$Port = 8080, [switch]$Public, [switch]$Stop, [switch]$Reset)
$ErrorActionPreference = 'Continue'   # docker 가 진행 상황을 stderr 로 쓰므로 Stop 이면 PowerShell 5.1 이 오류로 착각한다
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
Set-Location (Split-Path $PSScriptRoot -Parent)          # server\

function Say($t, $c = 'Cyan') { Write-Host $t -ForegroundColor $c }

# 1) Docker
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Say 'Docker Desktop 이 없습니다. 아래 한 줄로 설치한 뒤 PC 를 다시 시작하고, Docker Desktop 을 한 번 실행하세요:' Yellow
  Say '  winget install -e --id Docker.DockerDesktop' Yellow
  exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { Say 'Docker Desktop 을 실행해 주세요 (작업 표시줄 고래 아이콘이 «Engine running» 이 될 때까지 기다린 뒤 다시 실행)' Yellow; exit 1 }

if ($Stop -or $Reset) {
  if ($Reset) { docker compose --profile public down -v } else { docker compose --profile public down }
  Say '멈췄습니다.' Green; exit 0
}

# 2) .env — 처음 한 번만 (BOM 없는 UTF-8 로 써야 docker compose 가 읽는다)
if (-not (Test-Path .env)) {
  function Rand($n) { -join ((1..$n) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) }) }
  $envText = (Get-Content .env.example -Raw -Encoding UTF8)
  $envText = $envText -replace '(?m)^POSTGRES_PASSWORD=.*', "POSTGRES_PASSWORD=$(Rand 24)"
  $envText = $envText -replace '(?m)^API_DB_PASSWORD=.*', "API_DB_PASSWORD=$(Rand 24)"
  $envText = $envText -replace '(?m)^PUBLIC_API_KEY=.*', "PUBLIC_API_KEY=mlng_pub_$(Rand 12)"
  $envText = $envText -replace '(?m)^WEB_PORT=.*', "WEB_PORT=$Port"
  $envText = $envText -replace "`r`n", "`n"
  [IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $envText, (New-Object System.Text.UTF8Encoding($false)))
  Say "[.env] 만들었습니다 (비밀번호 무작위 · 포트 $Port). Claude 질의를 쓰려면 .env 의 ANTHROPIC_API_KEY 를 채우고 다시 실행하세요." Green
}
# 2-1) 포트 — 이미 다른 프로그램이 쓰고 있으면 비어 있는 포트로 바꿔 .env 에 적는다 (이 서버가 이미 떠 있으면 그대로)
function PortFree($p) { try { $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $p); $l.Start(); $l.Stop(); return $true } catch { return $false } }
function SetEnv($k, $v) {
  $t = [IO.File]::ReadAllText((Join-Path (Get-Location) '.env'))
  if ($t -match "(?m)^$k=") { $t = $t -replace "(?m)^$k=.*", "$k=$v" } else { $t = $t.TrimEnd("`n") + "`n$k=$v`n" }
  [IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $t, (New-Object System.Text.UTF8Encoding($false)))
}
function EnvVal($k) { $m = Select-String -Path .env -Pattern "^$k=(.*)$" -Encoding UTF8 | Select-Object -Last 1; if ($m) { $m.Matches[0].Groups[1].Value } else { '' } }
$webUp = (docker compose ps -q --status running web 2>$null)
if (-not $webUp) {
  $cur = [int](EnvVal 'WEB_PORT'); if (-not $cur) { $cur = $Port }
  if (-not (PortFree $cur)) {
    $new = @(8080, 8088, 8090, 8888, 18080, 28080) | Where-Object { $_ -ne $cur -and (PortFree $_) } | Select-Object -First 1
    if (-not $new) { Say '비어 있는 포트를 찾지 못했습니다 — .env 의 WEB_PORT 를 직접 바꿔 주세요' Red; exit 1 }
    Say "[포트] $cur 은(는) 다른 프로그램이 사용 중 → $new 으로 바꿉니다" Yellow; SetEnv 'WEB_PORT' $new
  }
  # 내 PC 에서는 HTTPS(443) 를 쓰지 않으므로 겹치지 않는 포트로 비켜 둔다
  $hp = [int](EnvVal 'WEB_HTTPS_PORT')
  if (-not $hp -or -not (PortFree $hp)) { $nh = @(8443, 18443, 28443) | Where-Object { PortFree $_ } | Select-Object -First 1; if ($nh) { SetEnv 'WEB_HTTPS_PORT' $nh } }
}
$envMap = @{}; Get-Content .env -Encoding UTF8 | Where-Object { $_ -match '^\s*([A-Z_]+)=(.*)$' } | ForEach-Object { $envMap[$Matches[1]] = $Matches[2] }
$Port = [int]($envMap['WEB_PORT']); if (-not $Port) { $Port = 80 }
$Base = if ($Port -eq 80) { 'http://localhost' } else { "http://localhost:$Port" }

# 3) 실행 (처음에는 이미지 받기 · 빌드로 몇 분)
Say '[실행] docker compose up --build (처음은 3~5분)'
docker compose up -d --build --wait --wait-timeout 300
if ($LASTEXITCODE -ne 0) { Say '실행 실패 — 아래 로그를 확인하세요' Red; docker compose logs --tail 40; exit 1 }

# 4) 요청 시험 (scripts/smoke.sh 와 같은 13개)
$Key = $envMap['PUBLIC_API_KEY']; $H = @{ apikey = $Key }
function Code($method, $url, $body, $headers) {
  try {
    $p = @{ Uri = $url; Method = $method; UseBasicParsing = $true; Headers = $headers }
    if ($body) { $p.Body = [Text.Encoding]::UTF8.GetBytes($body); $p.ContentType = 'application/json' }
    return [int](Invoke-WebRequest @p).StatusCode
  } catch { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } return 0 }
}
$id = "SMOKE-$([DateTimeOffset]::Now.ToUnixTimeSeconds())"; $now = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
$tests = @(
  @('화면 index.html', 200, (Code GET "$Base/" $null @{})),
  @('상태 /health', 200, (Code GET "$Base/health" $null @{})),
  @('표 읽기', 200, (Code GET "$Base/rest/v1/mlng_lots?select=id&limit=1" $null $H)),
  @('공개 키 없으면 거부', 401, (Code GET "$Base/rest/v1/mlng_lots?select=id&limit=1" $null @{})),
  @('허용된 쓰기 (발주 추가)', 201, (Code POST "$Base/rest/v1/mlng_purchase_orders" "{`"id`":`"$id`",`"created_at`":`"$now`",`"vendor`":`"점검`"}" $H)),
  @('허용된 수정 (발주 결재)', 204, (Code PATCH "$Base/rest/v1/mlng_purchase_orders?id=eq.$id" '{"status":"approved"}' $H)),
  @('막힌 쓰기 (LOT 표) → DB 권한 거부', 403, (Code POST "$Base/rest/v1/mlng_lots" '{"id":"X"}' $H)),
  @('조건 없는 수정 거부', 400, (Code PATCH "$Base/rest/v1/mlng_purchase_orders" '{"status":"approved"}' $H)),
  @('잘못된 열 이름 거부 (인젝션)', 400, (Code GET "$Base/rest/v1/mlng_lots?select=id;drop%20table%20x" $null $H)),
  @('없는 표 거부', 404, (Code GET "$Base/rest/v1/pg_shadow" $null $H)),
  @('삭제 요청 거부', 405, (Code DELETE "$Base/rest/v1/mlng_purchase_orders?id=eq.$id" $null $H)),
  @('공정 통합 AI 상태', 200, (Code GET "$Base/ai/v1/status" $null @{}))
)
$ok = 0; foreach ($t in $tests) { if ($t[1] -eq $t[2]) { $ok++; Say "✓ $($t[0])" Green } else { Say "✕ $($t[0]) (기대 $($t[1]), 실제 $($t[2]))" Red } }
$rt = try { (Invoke-WebRequest "$Base/js/runtime.js" -UseBasicParsing).Content } catch { '' }
if ($rt -match 'location.origin') { $ok++; Say '✓ 화면 설정 runtime.js (이 서버 주소)' Green } else { Say '✕ 화면 설정 runtime.js' Red }
Say "smoke $ok/13" $(if ($ok -eq 13) { 'Green' } else { 'Red' })

# 5) (선택) 인터넷 공개 주소
if ($Public) {
  Say '[공개] Cloudflare Quick Tunnel 시작 (무료 · 계정 불필요 · 켤 때마다 주소가 바뀜)'
  docker compose --profile public up -d tunnel | Out-Null
  $url = $null
  for ($i = 0; $i -lt 30 -and -not $url; $i++) { Start-Sleep 2; $m = (docker compose logs tunnel 2>&1 | Out-String) | Select-String -Pattern 'https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com' ; if ($m) { $url = $m.Matches[0].Value } }
  if ($url) { Say "공개 주소: $url  (다른 기기 · 휴대폰에서도 접속 가능, HTTPS)" Green; Start-Process $url } else { Say '공개 주소를 아직 못 받았습니다 — docker compose logs tunnel 로 확인' Yellow }
}

Say "`n완료 → $Base  (데이터 상태: 더보기 › 시스템 › 데이터 상태 = «연결됨»)" Green
Say '로그: docker compose logs -f api   ·   멈추기: scripts\local.ps1 -Stop' Gray
Start-Process $Base
