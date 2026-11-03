"""데이터 어댑터 — 서로 다른 공정 데이터를 «원래 칼럼 그대로 + 설명 · 단위» 의 표 하나로 바꾼다.

여기서는 칼럼의 뜻을 정하지 않는다 (그건 schema.py 가 이름 · 단위 · 값으로 스스로 판단).
어댑터가 주는 것은 원본 데이터에 원래 있던 정보뿐: 칼럼 설명(label) · 단위 · 규격(설정값 ± 허용폭) · LOT 연결.

MILG 데모 공장 (web/data/demo.json — 공개 데이터셋을 한 공장 흐름으로 이은 것)
  열처리 LOT · 프레스 LOT · 점용접 LOT · MCT 가공 회차 · 정밀가공 검사 부품
공개 데이터 (ai/data/public — python -m mlng_ai fetch 로 받기, UCI 저장소 CC BY 4.0)
  AI4I 2020 예지보전(가공 설비) · Cylinder Bands(그라비어 인쇄 밴딩 불량) · SECOM(반도체 공정, 칼럼 이름 없음)
"""
from __future__ import annotations

import json
import os
import urllib.request
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / 'web' / 'data' / 'demo.json'
PUB = ROOT / 'ai' / 'data' / 'public'

PUBLIC_SOURCES = {
    'ai4i': ('https://archive.ics.uci.edu/static/public/601/ai4i+2020+predictive+maintenance+dataset.zip', ['ai4i2020.csv']),
    'bands': ('https://archive.ics.uci.edu/static/public/32/cylinder+bands.zip', ['bands.data', 'bands.names']),
    'secom': ('https://archive.ics.uci.edu/static/public/179/secom.zip', ['secom.data', 'secom_labels.data']),
}


@dataclass
class Table:
    key: str                     # 'heat' …
    process: str                 # 사람이 읽는 공정 이름
    df: pd.DataFrame             # 원래 칼럼 (+ 시간 순서로 정렬)
    meta: dict = field(default_factory=dict)        # {칼럼: {'label':…, 'unit':…}}
    spec: dict = field(default_factory=dict)        # {칼럼: (설정값 또는 설정값 칼럼, 허용폭)}
    lot_col: str = ''            # 이 행이 속한 LOT (공정 간 연결 키)
    parents: dict = field(default_factory=dict)     # {LOT: [앞 공정 LOT …]}
    unit_kind: str = 'lot'       # lot(수량 있는 LOT) · row(한 행 = 한 개)
    source: str = ''
    series: dict = field(default_factory=dict)      # 비정형 파형: {이름: {'label', 'unit', 'by_lot': {LOT: [값…]}, 'sp', 'tol'}}  (sp 가 dict 면 LOT 별 설정값)
    entity: str = ''             # 적산을 묶는 단위 (공구 · 금형 · 설비 칼럼) — 바뀌면 적산을 0 부터


# ── 공개 데이터 받기
def fetch_public(force: bool = False) -> list[str]:
    PUB.mkdir(parents=True, exist_ok=True)
    got = []
    for k, (url, files) in PUBLIC_SOURCES.items():
        if not force and all((PUB / f).exists() for f in files):
            continue
        with urllib.request.urlopen(url, timeout=120) as r:
            z = zipfile.ZipFile(BytesIO(r.read()))
        for n in z.namelist():
            base = os.path.basename(n)
            if base in files:                        # 필요한 파일만 · 경로 무시 (압축 경로 공격 방지)
                (PUB / base).write_bytes(z.read(n))
        got.append(k)
    return got


# ── MILG 데모 공장
def _demo() -> dict:
    return json.loads(DEMO.read_text(encoding='utf-8'))


def mlng_tables(d: dict | None = None) -> list[Table]:
    d = d or _demo()
    trace = d.get('trace', [])
    par = {}
    for t in trace:
        par.setdefault(t['child_lot'], []).append(t['parent_lot'])
    out = []

    # 열처리 LOT
    H = d['heat']
    rec = {r['key']: r for r in H['recipe']}
    hl = {h['key']: h['label'] for h in H.get('heaters', [])} if H.get('heaters') else {}
    rows, meta, spec = [], {}, {}
    for l in H['lots']:
        r = {'lot_id': l['id'], 'start_time': l['start'], 'qty': l['qty'], 'bad_count': l['bad'], 'worker': l.get('worker'), 'mat_lot': l.get('mat_lot')}
        for k, s in (l.get('stats') or {}).items():
            r[f'{k}_mean'], r[f'{k}_std'], r[f'{k}_out'] = s.get('mean'), s.get('std'), s.get('out')
        for k, v in (l.get('heater') or {}).items():
            r[f'heater_{k}'] = v
        rows.append(r)
    for k, rc in rec.items():
        lab, u = rc['label'], rc.get('unit', '')
        meta[f'{k}_mean'] = {'label': f'{lab} 평균', 'unit': u}
        meta[f'{k}_std'] = {'label': f'{lab} 변동 (표준편차)', 'unit': u}
        meta[f'{k}_out'] = {'label': f'{lab} 규격 이탈 비율', 'unit': '%'}
        if rc.get('sp') is not None and rc.get('tol'):
            spec[f'{k}_mean'] = (rc['sp'], rc['tol'])
        elif rc.get('lo') is not None and rc.get('hi') is not None:
            spec[f'{k}_mean'] = ((rc['lo'] + rc['hi']) / 2, (rc['hi'] - rc['lo']) / 2)
    for k in ['op1', 'op2', 'op3', 'op4', 'opd1', 'opd2']:
        meta[f'heater_{k}'] = {'label': f'히터 출력 {hl.get(k, k)}', 'unit': '%'}
    meta.update({'qty': {'label': '투입 수량'}, 'bad_count': {'label': '불량 수량'}, 'worker': {'label': '작업자'}, 'mat_lot': {'label': '원자재 LOT'},
                 'lot_id': {'label': '열처리 LOT 번호'}, 'start_time': {'label': '작업 시작 시각'}})
    df = pd.DataFrame(rows).sort_values('start_time').reset_index(drop=True)
    prof = H.get('profiles') or {}
    ser = {}
    for k, rc in rec.items():
        by = {lot: p['s'][k] for lot, p in prof.items() if k in (p.get('s') or {})}
        if by:
            sp, tol = (rc.get('sp'), rc.get('tol')) if rc.get('tol') else (((rc['lo'] + rc['hi']) / 2, (rc['hi'] - rc['lo']) / 2) if rc.get('lo') is not None else (None, None))
            ser[k] = {'label': rc['label'] + ' 온도 기록' if rc.get('unit') == '°C' else rc['label'] + ' 기록', 'unit': rc.get('unit', ''), 'by_lot': by, 'sp': sp, 'tol': tol, 'step_min': 2}
    out.append(Table('heat', '염욕 열처리', df, meta, spec, 'lot_id', {k: v for k, v in par.items() if k.startswith('HT')}, 'lot', H.get('source', ''), ser))

    # 프레스 LOT (LOT 마다 설정값이 다를 수 있어 설정값을 칼럼으로)
    P = d['press']
    prec = {r['key']: r for r in P['recipe']}
    rows, meta, spec = [], {}, {}
    for l in P['lots']:
        r = {'lot_id': l['id'], 'work_date': l['date'], 'qty': l['qty'], 'bad_count': l['bad'], 'strokes': l.get('strokes'), 'changeover': int(bool(l.get('changeover')))}
        for k, s in (l.get('stats') or {}).items():
            r[f'{k}_mean'], r[f'{k}_std'], r[f'{k}_sp'] = s.get('mean'), s.get('std'), s.get('sp', prec.get(k, {}).get('sp'))
        rows.append(r)
    for k, rc in prec.items():
        meta[f'{k}_mean'] = {'label': f'{rc["label"]} 평균', 'unit': rc.get('unit', '')}
        meta[f'{k}_std'] = {'label': f'{rc["label"]} 변동 (표준편차)', 'unit': rc.get('unit', '')}
        meta[f'{k}_sp'] = {'label': f'{rc["label"]} 설정값', 'unit': rc.get('unit', '')}
        spec[f'{k}_mean'] = (f'{k}_sp', rc['tol'])
    meta.update({'qty': {'label': '생산 수량'}, 'bad_count': {'label': '불량 수량'}, 'strokes': {'label': '타발 횟수'}, 'changeover': {'label': '금형 교체 여부'},
                 'lot_id': {'label': '프레스 LOT 번호'}, 'work_date': {'label': '작업 날짜'}})
    df = pd.DataFrame(rows).sort_values('work_date').reset_index(drop=True)
    ser = {}
    for k, rc in prec.items():
        by = {l['id']: (P.get('series') or {}).get(l['id'], {}).get(k) for l in P['lots']}
        by = {a: b for a, b in by.items() if b}
        if by:
            ser[k] = {'label': rc['label'] + ' 타발 추이', 'unit': rc.get('unit', ''), 'by_lot': by,
                      'sp': {l['id']: (l.get('stats') or {}).get(k, {}).get('sp', rc.get('sp')) for l in P['lots']}, 'tol': rc['tol']}
    out.append(Table('press', '프레스 성형', df, meta, spec, 'lot_id', {k: v for k, v in par.items() if k.startswith('PR')}, 'lot', P.get('source', ''), ser))

    # 점용접 LOT
    W = d['weld']
    df = pd.DataFrame([{'lot_id': l['id'], 'work_date': l['date'], 'welds': l['welds'], 'bad_count': l['bad'], 'out_rate': l.get('out_rate'),
                        'excursion': int(bool(l.get('excursion')))} for l in W['lots']]).sort_values('work_date').reset_index(drop=True)
    meta = {'welds': {'label': '타점 수'}, 'bad_count': {'label': '불량 타점 수'}, 'out_rate': {'label': '공정 조건 이탈률', 'unit': '%'},
            'excursion': {'label': '조건 급변 구간 발생'}, 'lot_id': {'label': '용접 LOT 번호'}, 'work_date': {'label': '작업 날짜'}}
    out.append(Table('weld', '점용접', df, meta, {}, 'lot_id', {k: v for k, v in par.items() if k.startswith('WD')}, 'lot', W.get('source', '')))

    # MCT 가공 회차 (공구 하나 = 가공 LOT 하나)
    M = d['mach']
    tools = {t['id']: t for t in M['tools']}
    sig = {s['key']: s for s in M.get('signals', [])}
    rows = []
    for r in M['runs']:
        t = tools.get(r['tool'], {})
        x = {'run_time': r['t'], 'tool_id': r['tool'], 'lot': t.get('lot'), 'cut_minutes': r.get('minute'), 'vb_measured': r.get('vb'),
             'vb_sensor_est': r.get('vb_est'), 'depth_of_cut': t.get('DOC'), 'feed': t.get('feed'), 'work_material': t.get('material'), 'quality_ng': int(bool(r.get('bad')))}
        x.update({k: v for k, v in (r.get('s') or {}).items()})
        rows.append(x)
    meta = {'cut_minutes': {'label': '공구 누적 가공 시간', 'unit': 'min'}, 'vb_measured': {'label': '공구 마모 측정값 VB', 'unit': 'mm'},
            'vb_sensor_est': {'label': '센서로 추정한 공구 마모', 'unit': 'mm'}, 'depth_of_cut': {'label': '절삭 깊이', 'unit': 'mm'},
            'feed': {'label': '이송량', 'unit': 'mm/rev'}, 'work_material': {'label': '피삭재 재질'}, 'quality_ng': {'label': '가공 품질 불량'},
            'tool_id': {'label': '공구 번호'}, 'lot': {'label': '가공 LOT'}, 'run_time': {'label': '가공 시각'}}
    for k, s in sig.items():
        meta[k] = {'label': s.get('label', k), 'unit': s.get('unit', '')}
    df = pd.DataFrame(rows).sort_values('run_time').reset_index(drop=True)
    out.append(Table('mach', 'MCT 가공', df, meta, {}, 'lot', {k: v for k, v in par.items() if k.startswith('MC')}, 'row', M.get('source', ''), entity='tool_id'))

    # 정밀가공 검사 (부품 하나 = 한 행)
    I = d['insp']
    sig = {s['key']: s for s in I['signals']}
    ipar = {l['id']: l.get('parents', []) for l in I['lots']}
    df = pd.DataFrame([dict({'serial_no': r['sn'], 'inspect_time': r['t'], 'lot': r['lot'], 'pass_ok': int(bool(r['ok']))}, **r['s']) for r in I['records']])
    df = df.sort_values('inspect_time').reset_index(drop=True)
    meta = {k: {'label': s.get('label', k), 'unit': s.get('unit', '')} for k, s in sig.items()}
    meta.update({'pass_ok': {'label': '검사 판정 (합격 = 1)'}, 'serial_no': {'label': '부품 번호'}, 'inspect_time': {'label': '검사 시각'}, 'lot': {'label': '검사 LOT'}})
    out.append(Table('insp', '정밀가공 검사', df, meta, {}, 'lot', ipar, 'row', I.get('source', '')))
    return out


# ── 공개 데이터
BAND_COLS = ['timestamp', 'cylinder number', 'customer', 'job number', 'grain screened', 'ink color', 'proof on ctd ink', 'blade mfg', 'cylinder division',
             'paper type', 'ink type', 'direct steam', 'solvent type', 'type on cylinder', 'press type', 'press', 'unit number', 'cylinder size',
             'paper mill location', 'plating tank', 'proof cut', 'viscosity', 'caliper', 'ink temperature', 'humifity', 'roughness', 'blade pressure',
             'varnish pct', 'press speed', 'ink pct', 'solvent pct', 'ESA Voltage', 'ESA Amperage', 'wax', 'hardener', 'roller durometer',
             'current density', 'anode space ratio', 'chrome content', 'band type']


def public_tables() -> list[Table]:
    out = []
    if (PUB / 'ai4i2020.csv').exists():
        df = pd.read_csv(PUB / 'ai4i2020.csv', encoding='utf-8-sig')
        out.append(Table('ai4i', '가공 설비 (AI4I 2020)', df, {}, {}, '', {}, 'row', 'UCI AI4I 2020 Predictive Maintenance (CC BY 4.0)'))
    if (PUB / 'bands.data').exists():
        rows = []
        for line in (PUB / 'bands.data').read_text(encoding='latin-1').splitlines():
            p = [x.strip() for x in line.split(',')]
            if len(p) == 40:
                rows.append(p)
        df = pd.DataFrame(rows, columns=BAND_COLS).replace({'?': np.nan, '': np.nan})
        for c in df.columns:                                   # 숫자로 읽히는 칼럼은 숫자로
            v = pd.to_numeric(df[c], errors='coerce')
            if v.notna().sum() >= 0.9 * df[c].notna().sum() and df[c].notna().sum():
                df[c] = v
        df['band type'] = df['band type'].str.lower()
        df = df.dropna(subset=['band type']).sort_values('timestamp', kind='stable').reset_index(drop=True)
        out.append(Table('bands', '그라비어 인쇄 (Cylinder Bands)', df, {}, {}, '', {}, 'row', 'UCI Cylinder Bands (CC BY 4.0)'))
    if (PUB / 'secom.data').exists():
        X = pd.read_csv(PUB / 'secom.data', sep=r'\s+', header=None)
        X.columns = [f'f{i:03d}' for i in range(X.shape[1])]
        lab = [ln.split(' ', 1) for ln in (PUB / 'secom_labels.data').read_text().splitlines() if ln.strip()]
        X = pd.concat([X, pd.DataFrame({'label': [int(a) for a, _ in lab],
                                        'timestamp': pd.to_datetime([t.strip().strip('"') for _, t in lab], format='%d/%m/%Y %H:%M:%S').astype(str)})], axis=1)
        X = X.sort_values('timestamp').reset_index(drop=True)
        out.append(Table('secom', '반도체 공정 (SECOM, 칼럼 이름 없음)', X, {'label': {'label': '1 = fail'}}, {}, '', {}, 'row', 'UCI SECOM (CC BY 4.0)'))
    return out


def all_tables(public: bool = True) -> list[Table]:
    return mlng_tables() + (public_tables() if public else [])
