#!/usr/bin/env bash
# 배포 뒤 확인 — 서버가 정말 동작하는지 요청을 보내 본다 (CI · 배포 스크립트 · 사람이 같이 쓴다)
#   bash server/scripts/smoke.sh [주소]      기본 http://localhost
set -u
BASE="${1:-http://localhost}"
KEY="$(grep -E '^PUBLIC_API_KEY=' "$(dirname "$0")/../.env" 2>/dev/null | cut -d= -f2-)"
# 막 다시 시작한 직후일 수 있으니 /health 가 응답할 때까지 최대 60초 기다린다
for i in $(seq 1 30); do [ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/health")" = 200 ] && break; sleep 2; done
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "✓ $1"; pass=$((pass+1)); else echo "✕ $1 (기대 $2, 실제 $3)"; fail=$((fail+1)); fi; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
H=(-H "apikey: $KEY" -H "Content-Type: application/json")
ID="SMOKE-$(date +%s)"

check "화면 index.html"                  200 "$(code "$BASE/")"
check "상태 /health"                      200 "$(code "$BASE/health")"
check "화면 설정 runtime.js (이 서버 주소)" 1 "$(curl -s "$BASE/js/runtime.js" | grep -c 'location.origin')"
check "표 읽기"                           200 "$(code "${H[@]}" "$BASE/rest/v1/mlng_lots?select=id&limit=1")"
[ -n "$KEY" ] && check "공개 키 없으면 거부"   401 "$(code "$BASE/rest/v1/mlng_lots?select=id&limit=1")"
check "허용된 쓰기 (발주 추가)"            201 "$(code "${H[@]}" -X POST "$BASE/rest/v1/mlng_purchase_orders" -d "{\"id\":\"$ID\",\"created_at\":\"$(date '+%F %T')\",\"vendor\":\"점검\",\"note\":\"smoke\"}")"
check "허용된 수정 (발주 결재)"            204 "$(code "${H[@]}" -X PATCH "$BASE/rest/v1/mlng_purchase_orders?id=eq.$ID" -d '{"status":"approved"}')"
check "막힌 쓰기 (LOT 표) → DB 권한 거부"   403 "$(code "${H[@]}" -X POST "$BASE/rest/v1/mlng_lots" -d '{"id":"X"}')"
check "조건 없는 수정 거부"               400 "$(code "${H[@]}" -X PATCH "$BASE/rest/v1/mlng_purchase_orders" -d '{"status":"approved"}')"
check "잘못된 열 이름 거부 (인젝션)"        400 "$(code "${H[@]}" "$BASE/rest/v1/mlng_lots?select=id;drop%20table%20x")"
check "없는 표 거부"                      404 "$(code "${H[@]}" "$BASE/rest/v1/pg_shadow")"
check "삭제 요청 거부"                     405 "$(code "${H[@]}" -X DELETE "$BASE/rest/v1/mlng_purchase_orders?id=eq.$ID")"
check "공정 통합 AI 상태"                200 "$(code "$BASE/ai/v1/status")"
echo "smoke $pass/$((pass+fail))"
[ "$fail" -eq 0 ]
