"""MLNG MES 자체 서버 — FastAPI

Supabase 가 해 주던 일 두 가지를 직접 한다.
  /rest/v1/{표}            표 읽기 · 쓰기 (PostgREST 문법, app/rest.py)
  /functions/v1/mlng-ask   질의응답 — Claude API 키를 서버에만 두고 중계 (app/ask.py)
  /health                  상태 확인 (Nginx · docker · 배포 스크립트가 본다)
  /ai/v1/*                 공정 통합 AI 엔진 — 파트너 키(X-MLNG-Key) · 테넌트별 모델 · 사용량 (app/tenants.py)
                           status · usage · map · update · predict · diagnose
                           ingest · models (후보 학습 → 이후 기간 검증 → 승인 → 복구, 공장별 독립)

요청 흐름
  브라우저 ─ HTTPS ─> Nginx ─ /rest, /functions ─> FastAPI(uvicorn) ─> PostgreSQL
                       └ 그 밖 ─> web/ 정적 파일
"""
from __future__ import annotations

import datetime as dt
import decimal
import json
import time
import uuid
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from . import ask, settings as S, tenants
from .rest import BadRequest, Catalog, insert_sql, parse_filter, parse_query, select_sql, update_sql

pool: AsyncConnectionPool | None = None
CATALOG: Catalog = {}
CATALOG_AT = 0.0


async def load_catalog(force: bool = False) -> Catalog:
    """public 스키마의 표 · 열 · 타입 목록 (60초 캐시). 여기 없는 이름은 SQL 에 들어갈 수 없다."""
    global CATALOG, CATALOG_AT
    if CATALOG and not force and time.time() - CATALOG_AT < 60:
        return CATALOG
    async with pool.connection() as con:
        cur = await con.execute("""
            select c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
              from pg_attribute a join pg_class c on c.oid = a.attrelid join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'public' and c.relkind in ('r', 'v', 'p') and a.attnum > 0 and not a.attisdropped""")
        cat: Catalog = {}
        for t, col, typ in await cur.fetchall():
            cat.setdefault(t, {})[col] = typ
    CATALOG, CATALOG_AT = cat, time.time()
    return cat


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = AsyncConnectionPool(S.DATABASE_URL, min_size=1, max_size=10, open=False, kwargs={'autocommit': False})
    await pool.open(wait=False)
    yield
    await pool.close()
    await ask.close()


app = FastAPI(title='MLNG MES API', version=S.VERSION, lifespan=lifespan, docs_url='/api/docs', openapi_url='/api/openapi.json')
app.add_middleware(CORSMiddleware, allow_origins=S.ALLOW_ORIGINS, allow_methods=['GET', 'POST', 'PATCH', 'OPTIONS'],
                   allow_headers=['apikey', 'authorization', 'content-type', 'prefer', 'x-client-info', 'x-mlng-key'])


def _default(o):
    if isinstance(o, (dt.datetime, dt.date, dt.time)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, uuid.UUID):
        return str(o)
    return str(o)


def out(data, status: int = 200) -> Response:
    return Response(json.dumps(data, default=_default, ensure_ascii=False), status_code=status, media_type='application/json')


def err(msg: str, code: str, status: int) -> Response:
    return JSONResponse({'message': msg, 'code': code}, status_code=status)


# ── 공개 키 · 쓰기 횟수 제한
_hits: dict[str, list[float]] = {}


def limited(key: str, n: int, minutes: int) -> bool:
    now, win = time.time(), minutes * 60
    a = [t for t in _hits.get(key, []) if now - t < win]
    a.append(now)
    _hits[key] = a
    if len(_hits) > 10_000:
        _hits.clear()
    return len(a) > n


def client_ip(req: Request) -> str:
    return (req.headers.get('x-forwarded-for') or '').split(',')[0].strip() or (req.client.host if req.client else 'unknown')


def key_ok(req: Request) -> bool:
    return not S.PUBLIC_API_KEY or req.headers.get('apikey') == S.PUBLIC_API_KEY


async def run(stmt, args, fetch: bool = True):
    """한 요청 = 한 트랜잭션. 역할을 anon 으로 바꿔 DB 권한 · RLS 를 그대로 받는다."""
    async with pool.connection() as con:
        async with con.transaction():
            await con.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(S.DB_REQUEST_ROLE)))
            await con.execute("SET LOCAL statement_timeout = '15s'")
            cur = con.cursor(row_factory=dict_row)
            await cur.execute(stmt, args)
            return await cur.fetchall() if fetch and cur.description else None


def pg_error(e: psycopg.Error) -> Response:
    code = getattr(e, 'sqlstate', None) or 'XX000'
    status = 403 if code == '42501' else 409 if code == '23505' else 400 if code and code[:2] in ('22', '23', '42') else 500
    msg = (e.diag.message_primary if getattr(e, 'diag', None) and e.diag.message_primary else str(e)).split('\n')[0]
    return err(msg, code, status)


@app.get('/health')
async def health():
    try:
        async with pool.connection() as con:
            await con.execute('select 1')
        cat = await load_catalog()
        return {'ok': True, 'db': 'ok', 'tables': len(cat), 'version': S.VERSION, 'ask': bool(S.ANTHROPIC_API_KEY)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({'ok': False, 'db': str(e).split('\n')[0]}, status_code=503)


@app.get('/rest/v1/{table}')
async def rest_get(table: str, req: Request):
    if not key_ok(req):
        return err('apikey 가 없거나 다릅니다', 'PGRST301', 401)
    try:
        cat = await load_catalog()
        q = parse_query(table, list(req.query_params.multi_items()), S.MAX_ROWS)
        stmt, args = select_sql(cat, q)
        return out(await run(stmt, args))
    except BadRequest as e:
        return err(e.msg, e.code, e.status)
    except psycopg.Error as e:
        return pg_error(e)


async def _body(req: Request):
    raw = await req.body()
    if len(raw) > S.MAX_BODY:
        raise BadRequest('요청이 너무 큽니다', 'PGRST413', 413)
    try:
        return json.loads(raw or b'null')
    except json.JSONDecodeError:
        raise BadRequest('JSON 이 아닙니다', 'PGRST102')


@app.post('/rest/v1/{table}')
async def rest_post(table: str, req: Request):
    if not key_ok(req):
        return err('apikey 가 없거나 다릅니다', 'PGRST301', 401)
    if limited('w:' + client_ip(req), S.WRITE_RATE_N, S.WRITE_RATE_MIN):
        return err('쓰기 요청이 너무 잦습니다', 'PGRST429', 429)
    try:
        body = await _body(req)
        rows = body if isinstance(body, list) else [body]
        want = 'return=representation' in (req.headers.get('prefer') or '')
        stmt, args = insert_sql(await load_catalog(), table, rows, want)
        res = await run(stmt, args, fetch=want)
        return out(res, 201) if want else Response(status_code=201)
    except BadRequest as e:
        return err(e.msg, e.code, e.status)
    except psycopg.Error as e:
        return pg_error(e)


@app.patch('/rest/v1/{table}')
async def rest_patch(table: str, req: Request):
    if not key_ok(req):
        return err('apikey 가 없거나 다릅니다', 'PGRST301', 401)
    if limited('w:' + client_ip(req), S.WRITE_RATE_N, S.WRITE_RATE_MIN):
        return err('쓰기 요청이 너무 잦습니다', 'PGRST429', 429)
    try:
        body = await _body(req)
        filters = [parse_filter(k, v) for k, v in req.query_params.multi_items() if k not in ('select', 'order', 'limit', 'offset')]
        want = 'return=representation' in (req.headers.get('prefer') or '')
        stmt, args = update_sql(await load_catalog(), table, body, filters, want)
        res = await run(stmt, args, fetch=want)
        return out(res) if want else Response(status_code=204)
    except BadRequest as e:
        return err(e.msg, e.code, e.status)
    except psycopg.Error as e:
        return pg_error(e)


@app.post('/functions/v1/mlng-ask')
async def mlng_ask(req: Request):
    if limited('a:' + client_ip(req), S.ASK_RATE_N, S.ASK_RATE_MIN):
        return JSONResponse({'error': '질문이 너무 잦습니다 — 잠시 후 다시 시도하세요'}, status_code=429)
    status, body = await ask.handle(await req.body())
    return JSONResponse(body, status_code=status)


# ── 공정 통합 AI (ai/mlng_ai) — 파트너(테넌트)마다 따로 누적 학습. 무거워서 처음 부를 때 불러온다
#   인증: X-MLNG-Key (또는 Authorization: Bearer) — app/tenants.py · 키 파일이 없으면 열린 모드(데모)
_AI: dict = {}


def ai_service(tenant: str = 'public'):
    """테넌트별 서비스 — 상태는 <AI_STATE_DIR>/tenants/<tenant>/, 첫 상태는 공통 모델(전이 학습)"""
    if tenant not in _AI:
        import os
        from mlng_ai.service import Service
        _AI[tenant] = Service(tenants.state_dir() / 'tenants' / tenant, os.environ.get('AI_SEED', '/srv/ai_seed/model.json'))
    return _AI[tenant]


class AIError(Exception):
    def __init__(self, msg: str, code: str, status: int = 400):
        self.msg, self.code, self.status = msg, code, status


def _tenant(req: Request) -> dict:
    t = tenants.resolve(req.headers)
    if t is None:
        raise AIError('X-MLNG-Key 가 없거나 유효하지 않습니다', 'AI401', 401)
    return t


async def _ai_rows(req: Request, limit: int):
    """{rows:[{…}]} 또는 {csv:"칼럼,…\n값,…"} → DataFrame"""
    import io
    import pandas as pd
    raw = await req.body()
    if len(raw) > limit:
        raise AIError('요청이 너무 큽니다', 'AI413', 413)
    try:
        body = json.loads(raw or b'{}')
    except json.JSONDecodeError:
        raise AIError('JSON 이 아닙니다', 'AI400')
    if not isinstance(body, dict):
        raise AIError('JSON 객체여야 합니다', 'AI400')
    meta = body.get('meta') if isinstance(body.get('meta'), dict) else {}
    if isinstance(body.get('file_b64'), str) and body['file_b64']:
        import base64
        from mlng_ai.diagnose import read_table
        try:
            df = read_table(base64.b64decode(body['file_b64'], validate=True), str(body.get('name') or ''))
        except Exception:  # noqa: BLE001
            raise AIError('파일을 읽지 못했습니다 (CSV · 엑셀만)', 'AI400')
        if df.empty:
            raise AIError('파일에 행이 없습니다', 'AI400')
        return body, df, meta
    if isinstance(body.get('csv'), str) and body['csv'].strip():
        try:
            df = pd.read_csv(io.StringIO(body['csv']))
        except Exception:  # noqa: BLE001
            raise AIError('csv 를 읽지 못했습니다', 'AI400')
        if df.empty:
            raise AIError('csv 에 행이 없습니다', 'AI400')
        return body, df, meta
    rows = body.get('rows')
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        raise AIError('rows 는 객체 배열이어야 합니다 (예: [{"temp": 850, "bad": 0}, …]) — 또는 csv 문자열', 'AI400')
    return body, pd.DataFrame(rows), meta


async def _ai_call(req: Request, endpoint: str, limit: int, fn, rate: int = 60):
    """공통: 테넌트 확인 → 요청 수 제한 → 월 한도 → 실행 → 사용량 기록"""
    import asyncio
    t = None
    try:
        t = _tenant(req)
        if limited(f"ai:{t['tenant']}:{client_ip(req)}", rate, 10):
            raise AIError('요청이 너무 잦습니다', 'AI429', 429)
        body, df, meta = await _ai_rows(req, limit)
        if tenants.over_quota(t, len(df)):
            raise AIError('이번 달 처리 한도를 넘었습니다 — 플랜을 확인하세요', 'AI402', 402)
        res = await asyncio.to_thread(fn, ai_service(t['tenant']), body, df, meta)
        tenants.record(t['tenant'], endpoint, len(df), ip=client_ip(req), key_hint=t.get('hint', ''))
        return res
    except AIError as e:
        return err(e.msg, e.code, e.status)
    except ValueError as e:
        return err(str(e), 'AI400', 400)
    except Exception as e:  # noqa: BLE001
        return err(f'AI 처리 중 오류: {e.__class__.__name__}', 'AI500', 500)


@app.get('/ai/v1/status')
async def ai_status(req: Request):
    try:
        t = _tenant(req)
        s = await __import__('asyncio').to_thread(ai_service(t['tenant']).status)
        return {**s, 'tenant': t['tenant'], 'partner': t.get('partner'), 'plan': t.get('plan'), 'open_mode': tenants.open_mode()}
    except AIError as e:
        return err(e.msg, e.code, e.status)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({'error': f'AI 모듈을 불러오지 못했습니다: {e.__class__.__name__}'}, status_code=503)


@app.get('/ai/v1/usage')
async def ai_usage(req: Request):
    """이번 달 사용량과 한도 — 파트너 정산 · 대시보드용"""
    try:
        t = _tenant(req)
    except AIError as e:
        return err(e.msg, e.code, e.status)
    u = tenants.usage(t['tenant']).get(t['tenant'], {'requests': 0, 'rows': 0, 'by_endpoint': {}})
    return {'tenant': t['tenant'], 'month': dt.date.today().strftime('%Y-%m'), 'quota_rows': t.get('quota'), **u}


@app.post('/ai/v1/map')
async def ai_map(req: Request):
    """처음 보는 표의 칼럼 해석 — {rows | csv, meta} → 칼럼마다 역할 · 물리량 · 근거"""
    return await _ai_call(req, 'map', 2_000_000, lambda S, b, df, m: {'columns': S.map(df.head(5000), m)})


@app.post('/ai/v1/update')
async def ai_update(req: Request):
    """누적 학습 1회 — {process:'press_line2', rows | csv, meta} → 먼저 예측해 채점한 결과 + 새 모델 버전"""
    return await _ai_call(req, 'update', 4_000_000, lambda S, b, df, m: S.update(str(b.get('process', '')), df, m), rate=30)


@app.post('/ai/v1/predict')
async def ai_predict(req: Request):
    """학습 없이 위험도 — {process, rows | csv} → 행마다 기대 불량 · 확률 · 키운 쪽. 처음 보는 공정은 공통 효과로 위험 순위"""
    return await _ai_call(req, 'predict', 2_000_000, lambda S, b, df, m: S.predict(str(b.get('process', '')), df, m))


@app.post('/ai/v1/diagnose')
async def ai_diagnose(req: Request):
    """데이터 진단 — {rows | csv, meta, unit_cost, name, format:'json'|'html'} → 칼럼 해석 · 품질 · 원인 후보 · 손실"""
    def run_dx(S, b, df, m):
        try:
            uc = float(b['unit_cost']) if b.get('unit_cost') not in (None, '') else None
        except (TypeError, ValueError):
            raise ValueError('unit_cost 는 숫자여야 합니다')
        r = S.diagnose(df.head(200_000), m, unit_cost=uc, name=str(b.get('name') or 'data.csv')[:80])
        if b.get('format') == 'html':
            from mlng_ai.diagnose import render_html
            return Response(render_html(r), media_type='text/html; charset=utf-8')
        return r
    return await _ai_call(req, 'diagnose', 8_000_000, run_dx, rate=20)


@app.delete('/ai/v1/process/{process}')
async def ai_forget(process: str, req: Request):
    """공정 데이터 파기 — 저장된 원본 행을 지우고 이후 학습에서 뺀다 (계약 종료 · 고객 요청 대응, 감사 로그에 남음)"""
    try:
        t = _tenant(req)
        r = await __import__('asyncio').to_thread(ai_service(t['tenant']).forget, process)
        tenants.record(t['tenant'], 'forget', 0, ip=client_ip(req), key_hint=t.get('hint', ''))
        return r
    except AIError as e:
        return err(e.msg, e.code, e.status)
    except ValueError as e:
        return err(str(e), 'AI400', 400)


@app.post('/ai/v1/ingest')
async def ai_ingest(req: Request):
    """데이터만 쌓는다 (운영 모델은 그대로) — {process, rows | csv | file_b64}"""
    return await _ai_call(req, 'ingest', 8_000_000, lambda S, b, df, m: S.ingest(str(b.get('process', '')), df, m), rate=30)


async def _ai_json(req: Request) -> dict:
    raw = await req.body()
    if len(raw) > 20_000:
        raise AIError('요청이 너무 큽니다', 'AI413', 413)
    try:
        b = json.loads(raw or b'{}')
    except json.JSONDecodeError:
        raise AIError('JSON 이 아닙니다', 'AI400')
    if not isinstance(b, dict):
        raise AIError('JSON 객체여야 합니다', 'AI400')
    return b


async def _ai_ops(req: Request, endpoint: str, fn):
    import asyncio
    try:
        t = _tenant(req)
        if limited(f"ai:{t['tenant']}:{client_ip(req)}", 20, 10):
            raise AIError('요청이 너무 잦습니다', 'AI429', 429)
        b = await _ai_json(req)
        res = await asyncio.to_thread(fn, ai_service(t['tenant']), b)
        tenants.record(t['tenant'], endpoint, 0, ip=client_ip(req), key_hint=t.get('hint', ''))
        return res
    except AIError as e:
        return err(e.msg, e.code, e.status)
    except ValueError as e:
        return err(str(e), 'AI400', 400)


@app.get('/ai/v1/models')
async def ai_models(req: Request):
    """이 공장의 모델 버전 · 평가 · 승인 · 복구 기록"""
    try:
        t = _tenant(req)
    except AIError as e:
        return err(e.msg, e.code, e.status)
    return ai_service(t['tenant']).registry()


@app.post('/ai/v1/models/candidate')
async def ai_candidate(req: Request):
    """후보 모델 학습 — {process} → 앞 기간 학습, 이후 기간에서 운영 모델과 비교 (운영 모델은 그대로)"""
    return await _ai_ops(req, 'candidate', lambda S, b: S.train_candidate(str(b.get('process', ''))))


@app.post('/ai/v1/models/promote')
async def ai_promote(req: Request):
    """담당자 승인 — {version, by} → 운영 모델 교체, 이전 버전 보관"""
    return await _ai_ops(req, 'promote', lambda S, b: S.promote(str(b.get('version', '')), str(b.get('by', ''))[:40]))


@app.post('/ai/v1/models/rollback')
async def ai_rollback(req: Request):
    """직전 운영 모델로 복구 — {by}"""
    return await _ai_ops(req, 'rollback', lambda S, b: S.rollback(str(b.get('by', ''))[:40]))
