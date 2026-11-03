# MLNG MES 자체 서버

Supabase(관리형 서비스)가 해 주던 일을 **내 서버에서 직접** 한다. 화면 코드(`web/`)는 그대로 두고, 서버만 갈아 끼운다.

```
                       ┌──────────────────────── VM (Ubuntu) · docker compose ─────────────────────────┐
 브라우저 ── HTTPS ──▶ │ Nginx :443 ──┬── /            → web/ 정적 파일 (화면)                         │
                       │              ├── /js/runtime.js → "이 서버를 써라" (주소 · 공개 키)             │
                       │              └── /rest /functions /health ──▶ FastAPI :8000 ──▶ PostgreSQL :5432 │
                       │                                                     │                           │
                       │                                                     └──▶ Claude API (키는 .env) │
                       │  backup: 매일 pg_dump → backups/ (14일)    certbot: 인증서 발급 · 갱신           │
                       └───────────────────────────────────────────────────────────────────────────────┘
 GitHub ── push ──▶ Actions: 시험(화면 70 · API 13 · 컨테이너 12) ──통과──▶ SSH 로 VM 에 배포 ──▶ 확인
```

| Supabase 일 때 | 자체 서버 | 파일 |
|---|---|---|
| GitHub Pages (화면) | Nginx 정적 파일 + 캐시 · 압축 · 보안 헤더 | `nginx/templates/` |
| Supabase REST API | FastAPI — 같은 PostgREST 문법 (`?col=eq.값&order=…`) | `api/app/rest.py` |
| Supabase PostgreSQL · RLS | PostgreSQL 16 — **같은 SQL 파일**로 같은 표 · 권한 · 행 보안 | `db/` + `../supabase/new_project_setup.sql` |
| Edge Function (mlng-ask) | FastAPI 경로 — 같은 지시 · 도구 (자동 추출) | `api/app/ask.py` |
| Supabase Secrets | `server/.env` (깃에 안 올림) | `.env.example` |
| 자동 배포 | GitHub Actions → SSH → `docker compose up` | `../.github/workflows/deploy.yml` |
| 백업 · 인증서 | `backup` 컨테이너 · certbot | `docker-compose.yml`, `scripts/` |

## 0. 카드 없이 띄우기 (무료)

| | A. 내 PC (Windows) | B. GitHub Codespaces (클라우드) |
|---|---|---|
| 필요한 것 | Docker Desktop (개인 무료) | GitHub 계정만 (개인 월 무료 사용량 안) |
| 주소 | `http://localhost:8080` (+ `-Public` 이면 `https://xxxx.trycloudflare.com`) | `https://<codespace>-8080.app.github.dev` |
| 켜져 있는 시간 | PC 가 켜져 있는 동안 | 쓰는 동안 (30분 안 쓰면 자동 정지 → 다시 열면 재개) |

**A. 내 PC**
1. `winget install -e --id Docker.DockerDesktop` → PC 다시 시작 → Docker Desktop 실행 (고래 아이콘 «Engine running»)
2. PowerShell (한 줄씩)
   ```
   cd C:\Users\dksgu\Desktop\code\mlng-mes\mlng-mes\server
   powershell -ExecutionPolicy Bypass -File scripts\local.ps1
   ```
   → `.env` 자동 생성 · 빌드 · 요청 시험 12개 · 브라우저 열림
3. 인터넷 공개: `scripts\local.ps1 -Public` · 멈추기: `-Stop` · DB 까지 지우기: `-Reset`

**B. GitHub Codespaces**
1. https://github.com/dksgusrl04-gif/mlng-mes → 초록 **Code** 단추 → **Codespaces** 탭 → **Create codespace on main**
2. 3~5분 기다리면 (`.devcontainer` 가 `scripts/up.sh` 자동 실행) 아래 **포트** 탭에 `8080 MLNG MES` → 지구본 아이콘으로 열기
3. 다른 사람에게 보여 주려면: 포트 탭 8080 우클릭 → **포트 표시 유형 → Public**
4. 끝나면: github.com/codespaces 에서 **Stop** (무료 사용량 절약) · 다시 쓸 때 같은 codespace 열기

Linux · Mac 에서는 `bash server/scripts/up.sh` (`--public` · `--stop`).

## 1. AWS Free 플랜으로 띄우기 (카드 등록 필요 · 과금 없음 · 약 20분)

비용: t3.micro + 디스크 20GB + 공인 IPv4 ≈ 월 $15 → 6개월 $90 — 가입 크레딧($100) 안. Free 플랜은 크레딧을 넘는 과금이 없다.
6개월 뒤에는 유료로 바꾸지 않으면 계정이 닫힌다 (그 전에 `backups/` 받아 두기).

**1-1. (선택, HTTPS 용) DuckDNS 무료 도메인** — https://www.duckdns.org → GitHub 로 로그인 → 이름(예 `mlngmes`) 추가 → 위의 token 복사

**1-2. 서버 만들기 — 둘 중 하나**

| A. CloudFormation (한 번에, 추천) | B. EC2 콘솔에서 직접 |
|---|---|
| 콘솔 위 리전 **서울** → CloudFormation → 스택 생성 → 새 리소스 사용 | 리전 **서울** → EC2 → 인스턴스 시작 |
| 템플릿 파일 업로드: `server/aws/cloudformation.yml` | 이름 `mlng-mes` · **Ubuntu 24.04** · **t3.micro** · 키 페어 새로 만들기(.pem) |
| 스택 이름 `mlng-mes`, (선택) DuckDNS 이름 · 토큰 | 네트워크: **HTTP · HTTPS 허용** 체크, SSH 는 **내 IP** |
| 마지막 화면 «IAM 리소스 생성 승인» 체크 → 제출 | 스토리지 20GiB gp3 |
| | 고급 세부 정보 → 사용자 데이터에 `server/aws/user-data.sh` 내용 붙여넣기 (DuckDNS 두 줄은 선택) → 인스턴스 시작 |

**1-3. 기다리기 (약 10분)** — 서버가 스스로 Docker 설치 → 빌드 → 실행 → 요청 시험 12개 → (DuckDNS 면) HTTPS 인증서 → 자동 업데이트 등록

**1-4. 접속**
- A: 스택 → «출력» 탭 → `Url`
- B: 인스턴스 → 퍼블릭 IPv4 주소 → 브라우저에 `http://<IP>` (DuckDNS 면 `https://<이름>.duckdns.org`)
- 확인: 더보기 › 시스템 › 데이터 상태 = «연결됨» · `/health` · `/api/docs`

**1-5. 서버 터미널이 필요할 때** (설치 기록 보기 · .env 고치기)
- A: «출력» 탭 `Connect` 링크 (Session Manager, 브라우저 터미널 · SSH 키 불필요) → `sudo -iu ubuntu`
- B: PowerShell `ssh -i C:\Users\dksgu\Downloads\mlng-mes.pem ubuntu@<IP>`
- 설치 기록 `sudo tail -n 50 /var/log/mlng-setup.log` · 접속 정보 `cat ~/MLNG_접속정보.txt`
- Claude 질의 켜기: `nano ~/mlng-mes/server/.env` → `ANTHROPIC_API_KEY=` 채우기 → `cd ~/mlng-mes/server && docker compose up -d`
- DuckDNS 를 나중에 넣기: `.env` 에 `DUCKDNS_SUBDOMAIN=` · `DUCKDNS_TOKEN=` → `sudo bash ~/mlng-mes/server/aws/bootstrap.sh`

**1-6. 크레딧 더 받기 (+$20씩, Free 플랜 활동)** — 콘솔 홈의 «Explore AWS» 활동: EC2 시작(1-2 로 이미 됨) · **AWS Budgets 예산 만들기**(월 $10 알림 추천) 등

## 2. 자동 업데이트 (push → 시험 통과 → 서버 반영)

서버가 5분마다 GitHub 을 확인해 **CI(«MLNG MES 시험») 가 모두 통과한 새 커밋만** 받아 다시 빌드한다 (`scripts/autoupdate.sh`, systemd 타이머).
배포 뒤 요청 시험이 실패하면 **이전 커밋으로 자동 되돌림**. GitHub 에 SSH 키 · 비밀값을 줄 필요가 없다 (서버가 공개 저장소를 읽기만).
- 기록: `journalctl -u mlng-update -n 50` · 지금 바로: `sudo systemctl start mlng-update`
- (대안) GitHub 이 서버로 밀어 넣는 방식: `.github/workflows/deploy.yml` (Secrets: SSH_HOST · SSH_USER · SSH_KEY, Variables: DEPLOY_ENABLED=true)

## 3. HTTPS (내 도메인이 따로 있을 때)

DNS A 레코드 → 서버 IP, `.env` 에 `DOMAIN=` · `LETSENCRYPT_EMAIL=` → `bash scripts/enable-https.sh` (인증서 발급 · `NGINX_MODE=https` · 매일 자동 갱신)

## 4. 운영

| 할 일 | 명령 (server/ 에서) |
|---|---|
| 상태 | `docker compose ps` · `curl localhost/health` |
| 로그 | `docker compose logs -f api` (web · db 도 같음) |
| 다시 시작 | `docker compose restart api` |
| 업데이트 (수동) | `git pull && docker compose up -d --build && docker compose up -d --force-recreate web` |
| 백업 목록 | `ls backups/` (매일 자동, 14일 보관) |
| 복원 | `bash scripts/restore.sh backups/mlng_YYYYMMDD_HHMM.sql.gz` |
| DB 직접 보기 | `docker compose exec db psql -U postgres -d mlng` |
| API 문서 | `http://<주소>/api/docs` |
| 공정 통합 AI | `curl <주소>/ai/v1/status` · 새 데이터 학습 `POST /ai/v1/update {process, rows}` (상태는 docker 볼륨 `aidata`) |
| 파트너 키 | 발급 `docker compose exec api python -m app.tenants issue --tenant acme --partner "에이스MES"` · 목록 `… list` · 해지 `… revoke --tenant acme` · 사용량 `… usage` |

## 4-1. AI 엔진 API (파트너 판매용)

파트너(MES · SI 업체)는 키 하나로 엔진을 쓴다. 파트너마다 모델 상태가 따로 쌓이고(공통 모델에서 출발), 요청마다 사용량이 기록된다.

| 요청 | 하는 일 |
|---|---|
| `GET /ai/v1/status` | 이 파트너의 공정 목록 · 학습 행 수 · 신뢰 게이트 |
| `GET /ai/v1/usage` | 이번 달 요청 · 처리 행 수 · 한도 |
| `POST /ai/v1/map` | 칼럼 해석 `{rows | csv, meta}` |
| `POST /ai/v1/update` | 누적 학습 `{process, rows | csv}` — 먼저 예측해 채점한 뒤 학습 |
| `POST /ai/v1/predict` | 위험도만 `{process, rows | csv}` — 행마다 기대 불량 · 확률 · 키운 칼럼 (처음 보는 공정은 학습 0건 순위) |
| `POST /ai/v1/diagnose` | 데이터 진단 `{csv, unit_cost, name, format:'html'}` — 칼럼 해석 · 품질 · 불량 원인 후보 · 손실 금액 리포트 |

인증: 헤더 `X-MLNG-Key: mlng_…` (또는 `Authorization: Bearer …`). 키 원문은 서버에 저장하지 않는다(SHA-256 만).
월 한도를 넘으면 402, 키가 틀리면 401. 키 파일이 없으면 데모용 «열린 모드», 운영은 `.env` 에 `AI_REQUIRE_KEY=1`.

```bash
curl -s -X POST https://<주소>/ai/v1/diagnose -H "X-MLNG-Key: $KEY" -H 'content-type: application/json' \
  --data "$(jq -Rs '{csv: ., unit_cost: 15000, name: "line2.csv", format: "html"}' line2.csv)" > 진단.html
```

오프라인(파일 하나): `cd ai && python -m mlng_ai diagnose 고객데이터.csv --unit-cost 15000`

## 5. 보안 설계 (질문 받으면 이렇게)

- **밖에 열린 포트는 80 · 443 뿐.** DB(5432) · API(8000) 는 컨테이너 내부망에만 있다.
- **API 는 DB 관리자 계정을 쓰지 않는다.** `mlng_api` 계정은 표 권한이 없고, 요청마다 `SET LOCAL ROLE anon` 으로 바꿔
  Supabase 와 똑같은 GRANT · RLS 를 DB 가 직접 강제한다 → API 코드에 실수가 있어도 LOT 표 쓰기 · 삭제는 DB 가 막는다.
- **SQL 인젝션 차단:** 표 · 열 이름은 DB 목록에 있는 것만, 값은 전부 바인딩 변수.
- **조건 없는 수정 거부 · 삭제 경로 없음 · 행 수 상한 · 요청 크기 상한 · IP 당 횟수 제한(Nginx + API).**
- **비밀값은 `.env` 에만** (권한 600, 깃 제외). Claude API 키는 브라우저에 절대 내려가지 않는다.
- 컨테이너는 root 가 아닌 사용자로 실행, Nginx 버전 숨김, HSTS · nosniff · frame 제한 헤더.

## 6. 공장 안 서버(온프레미스)로 옮길 때

같은 `docker compose` 를 공장 PC · 미니 서버에 그대로 올리면 된다. 인터넷이 끊겨도 화면 · DB 는 계속 동작하고(질의응답만 멈춤),
설비 데이터가 공장 밖으로 나가지 않는다. 다음 단계: 로그인(JWT · 작업자/관리자 권한) → 설비 신호 수집기(OPC UA · Modbus) 컨테이너 추가.

## 시험

```bash
cd server/api && python -m pytest -q            # PostgREST 해석 · SQL 만들기 13개 (DB 없이)
node server/scripts/export_ask_spec.mjs --check # Edge Function 과 지시 · 도구가 같은지
bash server/scripts/smoke.sh http://localhost   # 실제 서버에 12개 요청
```
