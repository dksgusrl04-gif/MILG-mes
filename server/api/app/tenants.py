"""파트너(테넌트) — API 키 · 데이터 분리 · 사용량 기록 · 월 한도.

  키 파일   <AI_STATE_DIR>/keys.json   키 원문은 저장하지 않고 SHA-256 만 둔다 (발급 때 한 번만 보여 줌)
  사용량    <AI_STATE_DIR>/usage/YYYY-MM.jsonl   요청마다 한 줄 {ts, tenant, endpoint, rows}
  모델 상태 <AI_STATE_DIR>/tenants/<tenant>/     파트너마다 따로 — 공통 모델에서 출발해 각자 누적 학습

키 파일이 없거나 비어 있으면 «열린 모드» (로컬 개발 · 데모): 모든 요청을 tenant 'public' 으로 본다.
운영에서 열린 모드를 막으려면 AI_REQUIRE_KEY=1.

관리 (서버 컨테이너 안에서):
  python -m app.tenants issue  --tenant acme --partner "에이스MES" --plan partner [--quota 2000000] [--expires 2027-12-31]
  python -m app.tenants list
  python -m app.tenants revoke --tenant acme
  python -m app.tenants usage  [--month 2026-11]
  python -m app.tenants purge  --tenant acme --yes      계약 종료: 키 해지 + 모델 · 원본 데이터 삭제 + 파기 기록
"""
from __future__ import annotations

import argparse
import datetime as dt
try:
    import fcntl
except ImportError:
    fcntl=None
    import msvcrt
import hashlib
import json
import os
import re
import secrets
from pathlib import Path

TENANT = re.compile(r'^[a-z][a-z0-9_-]{1,31}$')
PLANS = {'trial': 200_000, 'partner': 2_000_000, 'enterprise': 20_000_000}   # 월 처리 행 수 기본 한도


def state_dir() -> Path:
    return Path(os.environ.get('AI_STATE_DIR', '/data/ai'))


def _keys_path() -> Path:
    return state_dir() / 'keys.json'


def _h(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def load_keys() -> dict:
    p = _keys_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8')).get('keys', {})
    except (json.JSONDecodeError, OSError):
        return {}


def _save_keys(keys: dict) -> None:
    p = _keys_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps({'keys': keys}, ensure_ascii=False, indent=1), encoding='utf-8')
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def open_mode() -> bool:
    return not load_keys() and os.environ.get('AI_REQUIRE_KEY', '0') != '1'


def resolve(headers) -> dict | None:
    """요청 헤더 → 테넌트. X-MLNG-Key 또는 Authorization: Bearer <키>. 없거나 틀리면 None"""
    keys = load_keys()
    if not keys:
        return None if os.environ.get('AI_REQUIRE_KEY', '0') == '1' else {'tenant': 'public', 'partner': '데모', 'plan': 'trial', 'quota': PLANS['trial'] * 50}
    raw = headers.get('x-mlng-key') or ''
    if not raw:
        a = headers.get('authorization') or ''
        raw = a[7:].strip() if a.lower().startswith('bearer ') else ''
    if not raw:
        return None
    t = keys.get(_h(raw))
    if not t or not t.get('active', True):
        return None
    if t.get('expires') and dt.date.today().isoformat() > t['expires']:      # 계약 기간 만료
        return None
    return t


def issue(tenant: str, partner: str, plan: str = 'partner', quota: int | None = None, expires: str | None = None) -> str:
    if not TENANT.match(tenant):
        raise ValueError('tenant 는 영문 소문자 · 숫자 · _ - (2~32자)')
    if plan not in PLANS:
        raise ValueError(f'plan 은 {", ".join(PLANS)} 중 하나')
    if expires:
        dt.date.fromisoformat(expires)
    key = 'mlng_' + secrets.token_urlsafe(24)
    keys = load_keys()
    keys[_h(key)] = {'tenant': tenant, 'partner': partner, 'plan': plan, 'quota': int(quota or PLANS[plan]), 'active': True,
                     'created': dt.datetime.now().isoformat(timespec='seconds'), 'hint': key[:9] + '…', 'expires': expires or ''}
    _save_keys(keys)
    return key


def revoke(tenant: str) -> int:
    keys = load_keys()
    n = 0
    for v in keys.values():
        if v['tenant'] == tenant and v.get('active', True):
            v['active'] = False
            n += 1
    _save_keys(keys)
    return n


# ── 사용량
def _usage_path(month: str | None = None) -> Path:
    return state_dir() / 'usage' / f"{month or dt.date.today().strftime('%Y-%m')}.jsonl"


def record(tenant: str, endpoint: str, rows: int, status: int = 200, ip: str = '', key_hint: str = '') -> None:
    """사용량 = 감사 로그: 언제 · 어느 파트너(키) · 어디서 · 무엇을 · 몇 행 — 정산과 보안 점검에 같이 쓴다 (데이터 내용은 남기지 않음)"""
    p = _usage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({'ts': dt.datetime.now().isoformat(timespec='seconds'), 'tenant': tenant, 'endpoint': endpoint, 'rows': int(rows), 'status': status,
                       'ip': ip, 'key': key_hint}, ensure_ascii=False)
    with open(p.with_suffix('.lock'),'a+b') as lock:
        if fcntl is not None:
            fcntl.flock(lock,fcntl.LOCK_EX)
        else:
            lock.seek(0,2)
            if not lock.tell():lock.write(b'0');lock.flush()
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_LOCK,1)
        try:
            with open(p,'a',encoding='utf-8') as f:
                f.write(line+'\n')
        finally:
            if fcntl is not None:fcntl.flock(lock,fcntl.LOCK_UN)
            else:
                lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


def usage(tenant: str | None = None, month: str | None = None) -> dict:
    """테넌트별 {requests, rows, by_endpoint}"""
    p = _usage_path(month)
    out: dict = {}
    if not p.exists():
        return out
    for ln in p.read_text(encoding='utf-8').splitlines():
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if tenant and r['tenant'] != tenant:
            continue
        u = out.setdefault(r['tenant'], {'requests': 0, 'rows': 0, 'by_endpoint': {}})
        u['requests'] += 1
        u['rows'] += r['rows']
        e = u['by_endpoint'].setdefault(r['endpoint'], {'requests': 0, 'rows': 0})
        e['requests'] += 1
        e['rows'] += r['rows']
    return out


def over_quota(t: dict, add_rows: int) -> bool:
    used = usage(t['tenant']).get(t['tenant'], {}).get('rows', 0)
    return used + add_rows > int(t.get('quota') or PLANS['trial'])


def purge(tenant: str) -> dict:
    """계약 종료 — 키 해지, 테넌트 모델 · 원본 행 삭제. 파기 기록은 <AI_STATE_DIR>/purge.log 에 남긴다 (파기 확인서 근거)"""
    import shutil
    if not TENANT.match(tenant):
        raise ValueError('tenant 이름이 올바르지 않습니다')
    n_keys = revoke(tenant)
    d = state_dir() / 'tenants' / tenant
    files = sum(1 for _ in d.rglob('*') if _.is_file()) if d.exists() else 0
    if d.exists():
        shutil.rmtree(d)
    rec = {'ts': dt.datetime.now().isoformat(timespec='seconds'), 'tenant': tenant, 'keys_revoked': n_keys, 'files_deleted': files}
    with open(state_dir() / 'purge.log', 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m app.tenants')
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('issue')
    a.add_argument('--tenant', required=True)
    a.add_argument('--partner', required=True)
    a.add_argument('--plan', default='partner')
    a.add_argument('--quota', type=int)
    a.add_argument('--expires', help='계약 만료일 YYYY-MM-DD (지나면 키 거부)')
    pg = sub.add_parser('purge')
    pg.add_argument('--tenant', required=True)
    pg.add_argument('--yes', action='store_true')
    sub.add_parser('list')
    r = sub.add_parser('revoke')
    r.add_argument('--tenant', required=True)
    u = sub.add_parser('usage')
    u.add_argument('--month')
    o = ap.parse_args(argv)
    if o.cmd == 'issue':
        k = issue(o.tenant, o.partner, o.plan, o.quota, o.expires)
        print('발급 완료 — 이 키는 다시 보여 줄 수 없습니다. 파트너에게 안전하게 전달하세요:\n' + k)
    elif o.cmd == 'list':
        for v in load_keys().values():
            print(f"{v['tenant']:16s} {v['partner'][:16]:16s} {v['plan']:10s} 한도 {v['quota']:>12,} 행/월  {'사용' if v.get('active', True) else '해지'}  만료 {v.get('expires') or '-':10s}  {v.get('hint', '')}  {v['created']}")
    elif o.cmd == 'purge':
        if not o.yes:
            print('되돌릴 수 없습니다 — 확인했으면 --yes 를 붙이세요')
            return
        print('파기 완료:', json.dumps(purge(o.tenant), ensure_ascii=False))
    elif o.cmd == 'revoke':
        print(f'{revoke(o.tenant)}개 키 해지')
    elif o.cmd == 'usage':
        for t, v in sorted(usage(month=o.month).items()):
            print(f"{t:16s} 요청 {v['requests']:>7,}  행 {v['rows']:>12,}  " + ' · '.join(f"{k} {e['requests']}" for k, e in v['by_endpoint'].items()))


if __name__ == '__main__':
    main()
