"""MLNG MES 자체 서버 — FastAPI

Supabase 가 해 주던 일 두 가지를 직접 한다.
  /rest/v1/{표}            표 읽기 · 쓰기 (PostgREST 문법, app/rest.py)
  /functions/v1/mlng-ask   질의응답 — Claude API 키를 서버에만 두고 중계 (app/ask.py)
  /health                  상태 확인 (Nginx · docker · 배포 스크립트가 본다)

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

from . import ask, settings as S
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
                   allow_headers=['apikey', 'authorization', 'content-type', 'prefer', 'x-client-info'])


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


# ── 공정 통합 AI (ai/mlng_ai) — 칼럼 해석 · 누적 학습. 무거워서 처음 부를 때 불러온다
_AI = None


def ai_service():
    global _AI
    if _AI is None:
        import os
        from mlng_ai.service import Service
        _AI = Service(os.environ.get('AI_STATE_DIR', '/data/ai'), os.environ.get('AI_SEED', '/srv/ai_seed/model.json'))
    return _AI


async def _ai_rows(req: Request, limit: int):
    import pandas as pd
    raw = await req.body()
    if len(raw) > limit:
        raise BadRequest('요청이 너무 큽니다', 'AI413', 413)
    try:
        body = json.loads(raw or b'{}')
    except json.JSONDecodeError:
        raise BadRequest('JSON 이 아닙니다', 'AI400')
    rows = body.get('rows')
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        raise BadRequest('rows 는 객체 배열이어야 합니다 (예: [{"temp": 850, "bad": 0}, …])', 'AI400')
    meta = body.get('meta') if isinstance(body.get('meta'), dict) else {}
    return body, pd.DataFrame(rows), meta


@app.get('/ai/v1/status')
async def ai_status():
    try:
        return ai_service().status()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({'error': f'AI 모듈을 불러오지 못했습니다: {e.__class__.__name__}'}, status_code=503)


@app.post('/ai/v1/map')
async def ai_map(req: Request):
    """처음 보는 표의 칼럼 해석 — {rows:[{…}], meta:{칼럼:{label,unit}}} → 칼럼마다 역할 · 물리량 · 근거"""
    if not key_ok(req):
        return err('apikey 가 없거나 다릅니다', 'PGRST301', 401)
    try:
        _, df, meta = await _ai_rows(req, 2_000_000)
        return {'columns': ai_service().map(df.head(5000), meta)}
    except BadRequest as e:
        return err(e.msg, e.code, e.status)


@app.post('/ai/v1/update')
async def ai_update(req: Request):
    """누적 학습 1회 — {process:'press_line2', rows:[…], meta:{…}} → 먼저 예측해 채점한 결과 + 새 모델 버전"""
    if not key_ok(req):
        return err('apikey 가 없거나 다릅니다', 'PGRST301', 401)
    if limited('ai:' + client_ip(req), 30, 10):
        return err('학습 요청이 너무 잦습니다', 'AI429', 429)
    try:
        body, df, meta = await _ai_rows(req, 4_000_000)
        import asyncio
        return await asyncio.to_thread(ai_service().update, str(body.get('process', '')), df, meta)
    except BadRequest as e:
        return err(e.msg, e.code, e.status)
    except ValueError as e:
        return err(str(e), 'AI400', 400)
