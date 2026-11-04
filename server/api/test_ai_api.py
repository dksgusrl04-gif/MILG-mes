"""AI 엔진 API — 파트너 키 · 테넌트 분리 · 사용량 · 한도 · 진단 (DB 없이 돈다)

  cd server/api && PYTHONPATH=.:../../ai python -m pytest -q test_ai_api.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ai'), str(Path(__file__).resolve().parent)]


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv('AI_STATE_DIR', d)
    monkeypatch.setenv('AI_SEED', str(ROOT / 'ai' / 'state' / 'model.json'))
    monkeypatch.setenv('DATABASE_URL', 'postgresql://x:x@127.0.0.1:1/x')
    from fastapi.testclient import TestClient
    from app import main
    main._AI.clear()
    with TestClient(main.app) as c:
        yield c, d


def rows(n=300, seed=0):
    import numpy as np
    r = np.random.default_rng(seed)
    t = r.normal(850, 10, n)
    p = r.normal(5, 0.3, n)
    bad = (r.random(n) < 0.03 + 0.25 * (t > 862)).astype(int)
    return [{'작업일시': f'2026-10-{1 + i // 20:02d} {i % 20:02d}:00', 'LOT번호': f'L{i:05d}', '로 온도(℃)': round(float(t[i]), 1),
             '가압력 bar': round(float(p[i]), 2), '작업자': 'AB'[i % 2], '불량여부': int(bad[i])} for i in range(n)]


def test_open_mode_and_diagnose(client):
    c, _ = client
    s = c.get('/ai/v1/status').json()
    assert s['open_mode'] is True and s['tenant'] == 'public'
    r = c.post('/ai/v1/diagnose', json={'rows': rows(), 'unit_cost': 12000, 'name': '고객사.csv'})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j['outcome'] == '불량여부' and j['readiness']['score'] >= 70
    assert j['factors'] and j['factors'][0]['column'] == '로 온도(℃)'
    h = c.post('/ai/v1/diagnose', json={'rows': rows(), 'format': 'html'})
    assert h.headers['content-type'].startswith('text/html') and '불량 원인 후보' in h.text


def test_csv_body_and_predict(client):
    c, _ = client
    import pandas as pd
    csv = pd.DataFrame(rows(200)).to_csv(index=False)
    assert c.post('/ai/v1/map', json={'csv': csv}).status_code == 200
    u = c.post('/ai/v1/update', json={'process': 'line_a', 'csv': csv})
    assert u.status_code == 200, u.text
    p = c.post('/ai/v1/predict', json={'process': 'line_a', 'rows': rows(30, seed=1)}).json()
    assert p['mode'] == 'learned' and len(p['rows']) == 30


def test_keys_isolation_usage_quota(client):
    c, d = client
    from app import tenants
    ka = tenants.issue('acme', '에이스MES', 'trial', quota=600)
    kb = tenants.issue('beta', '베타SI', 'partner')
    assert 'mlng_' in ka and ka not in Path(d, 'keys.json').read_text()   # 원문은 저장 안 함
    assert c.get('/ai/v1/status').status_code == 401                          # 키 파일이 생기면 열린 모드 끝
    assert c.get('/ai/v1/status', headers={'X-MLNG-Key': 'mlng_wrong'}).status_code == 401
    A, B = {'X-MLNG-Key': ka}, {'Authorization': f'Bearer {kb}'}
    assert c.post('/ai/v1/update', headers=A, json={'process': 'line_a', 'rows': rows(250)}).status_code == 200
    sa, sb = c.get('/ai/v1/status', headers=A).json(), c.get('/ai/v1/status', headers=B).json()
    assert 'line_a' in [p['key'] for p in sa['processes']] and 'line_a' not in [p['key'] for p in sb['processes']]   # 파트너끼리 안 섞임
    ua = c.get('/ai/v1/usage', headers=A).json()
    assert ua['rows'] == 250 and ua['by_endpoint']['update']['requests'] == 1
    r = c.post('/ai/v1/map', headers=A, json={'rows': rows(400)})            # 250 + 400 > 600
    assert r.status_code == 402
    tenants.revoke('beta')
    assert c.get('/ai/v1/status', headers=B).status_code == 401


def test_bad_input(client):
    c, _ = client
    assert c.post('/ai/v1/diagnose', content=b'not json').status_code == 400
    assert c.post('/ai/v1/update', json={'process': 'Bad Key!', 'rows': rows(30)}).status_code == 400
    assert c.post('/ai/v1/diagnose', json={'rows': rows(50), 'unit_cost': 'abc'}).status_code == 400


def test_korean_files_expiry_forget(client):
    """국내 현장 파일(CP949 CSV · 엑셀 · «불합격» 표기) · 계약 만료 · 데이터 파기"""
    import base64, io, datetime as dt
    import pandas as pd
    c, d = client
    df = pd.DataFrame(rows(300))
    df['판정'] = df.pop('불량여부').map({0: '합격', 1: '불합격'})
    b64 = base64.b64encode(df.to_csv(index=False).encode('cp949')).decode()
    r = c.post('/ai/v1/diagnose', json={'file_b64': b64, 'name': '라인2.csv'}).json()
    assert r['outcome'] == '판정' and abs(r['defect_rate'] - (df['판정'] == '불합격').mean()) < 1e-9
    bio = io.BytesIO(); df.to_excel(bio, index=False)
    r2 = c.post('/ai/v1/diagnose', json={'file_b64': base64.b64encode(bio.getvalue()).decode(), 'name': '라인2.xlsx'}).json()
    assert r2['quality']['rows'] == 300 and r2['factors']
    from app import tenants
    old = tenants.issue('old', '만료사', 'trial', expires=(dt.date.today() - dt.timedelta(days=1)).isoformat())
    assert c.get('/ai/v1/status', headers={'X-MLNG-Key': old}).status_code == 401
    k = tenants.issue('acme', '에이스MES', 'partner', expires='2099-12-31')
    H = {'X-MLNG-Key': k}
    assert c.post('/ai/v1/update', headers=H, json={'process': 'line_a', 'rows': rows(200)}).status_code == 200
    f = c.delete('/ai/v1/process/line_a', headers=H).json()
    assert f['rows_deleted'] == 200 and f['model_reset']
    assert 'line_a' not in [p['key'] for p in c.get('/ai/v1/status', headers=H).json()['processes']]
    log = (Path(d) / 'usage').glob('*.jsonl')
    assert any('"forget"' in p.read_text() and '"key": "mlng_' in p.read_text() for p in log)   # 감사 로그
    rec = tenants.purge('acme')
    assert rec['keys_revoked'] == 1 and not (Path(d) / 'tenants' / 'acme').exists()


def test_model_ops_isolated(client):
    """후보 학습 → 승인 → 복구, 그리고 A공장 갱신이 B공장 모델을 바꾸지 않는다"""
    import hashlib
    c, d = client
    from app import tenants
    A, B = {'X-MLNG-Key': tenants.issue('fac_a', 'A공장', 'partner')}, {'X-MLNG-Key': tenants.issue('fac_b', 'B공장', 'partner')}
    rws = rows(400, seed=3)
    assert c.post('/ai/v1/ingest', headers=A, json={'process': 'line_a', 'rows': rws}).json()['rows'] == 400
    assert c.post('/ai/v1/ingest', headers=B, json={'process': 'line_a', 'rows': rows(400, seed=4)}).status_code == 200
    c.post('/ai/v1/models/candidate', headers=B, json={'process': 'line_a'})
    vb = c.post('/ai/v1/models/candidate', headers=B, json={'process': 'line_a'}).json()['version']
    c.post('/ai/v1/models/promote', headers=B, json={'version': vb, 'by': 'qa_b'})
    fb = Path(d, 'tenants', 'fac_b', 'model.json')
    hb = hashlib.sha256(fb.read_bytes()).hexdigest()
    cand = c.post('/ai/v1/models/candidate', headers=A, json={'process': 'line_a'}).json()
    assert cand['eval']['candidate']['capture'] >= 0 and cand['recommend'] in ('promote', 'keep')
    assert c.get('/ai/v1/models', headers=A).json()['active'] == 'base'          # 후보만으로는 운영 모델이 안 바뀐다
    assert c.post('/ai/v1/models/promote', headers=A, json={'version': cand['version'], 'by': 'qa_a'}).json()['active'] == cand['version']
    assert hashlib.sha256(fb.read_bytes()).hexdigest() == hb                      # B공장 모델 불변
    assert c.post('/ai/v1/models/rollback', headers=A, json={'by': 'qa_a'}).json()['active'] == 'base'
    reg = c.get('/ai/v1/models', headers=A).json()
    assert [h['action'] for h in reg['history']] == ['promote', 'rollback']
    assert c.get('/ai/v1/models', headers=B).json()['active'] == vb
    assert c.post('/ai/v1/models/promote', headers=A, json={'version': 'v_none'}).status_code == 400
