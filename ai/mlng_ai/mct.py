"""MCT 기본 모델 — 공개 MCT 데이터로 학습하고, 공구 단위로 평가하고, 정답 없는 현장 데이터는 정상 기준 모드로 본다.

정답 규칙 (사람이 정한다 · 결과를 보기 전에 고정)
  HMoTP 2024   회차별 평균 플랭크 마모 ≥ 200 µm  → 공구 교체 필요 = 1
  NASA 밀링     측정된 플랭크 마모 VB ≥ 0.2 mm     → 공구 교체 필요 = 1  (VB 를 잰 회차만)
  마모 칼럼(정답의 재료)은 입력에서 뺀다.

입력 두 가지
  sensor   센서 요약값만 (절삭력 · 토크 · 진동 · 주축 전류 · AE) — 누적 사용 횟수 · 시간 제외 (공구 교체 뒤 센서의 누적 이탈은 포함)
  usage    sensor + 공구 누적 사용량(적산) — 현장에서 실제로 아는 값이지만, 마모가 회차마다 늘기만 해서 «회차만 보고 맞히는» 효과가 섞인다

평가 — 공구 단위 교차검증 (한 공구를 통째로 빼고 나머지로 학습 → 뺀 공구로 평가, 공구마다 반복)
  규칙(가장 크게 벗어난 조건) · 자체(그 데이터만) · 합침(HMoTP + NASA 함께, 공정별 보정)
  지표: 위험도 상위 10% 알림 안의 불량 비율 · AUC

정상 기준 모드
  HMoTP 로 방법 검증: 공구마다 처음 20회(새 공구)를 정상 기준으로 → 이후 회차 점수가 마모와 함께 오르는가
  현장 데이터(정답 없음): 기준 기간으로 학습 → 이후 기간 알림 비율(오경보율)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .datasets import Table
from .engine import auc, chunks, spearman
from .evaluate import alert_metrics
from .features import REL_SLOTS, SLOTS, add_upstream, encode, standardize
from .model import CumulativeModel
from .normal import NormalBaseline, condition_columns

HERE = Path(__file__).resolve().parent.parent
WEB = HERE.parent / 'web' / 'data'
_EVALUATION_LABELS = json.loads((HERE / 'profiles' / 'evaluation_labels.json').read_text(encoding='utf-8'))
WEAR_UM = _EVALUATION_LABELS['hmotp']['threshold']
VB_MM = _EVALUATION_LABELS['nasa']['threshold']
STIEHL_UM = _EVALUATION_LABELS['stiehl']['threshold']     # 저자 정의 수명 끝(VB ≈ 150 µm)의 80% — 교체 임박. 모든 공구에 사건이 생기는 가장 높은 기준
RULES = {}
SHORT = {'mct_hmotp': 'HMoTP', 'mct_nasa': 'NASA', 'mct_stiehl': 'Stiehl'}
CH = {'F_x': ('절삭력 X', 'N'), 'F_y': ('절삭력 Y', 'N'), 'F_z': ('절삭력 Z', 'N'), 'M_z': ('토크 모멘트 Z', 'N·m'),
      'A_x': ('진동 가속도 X', 'm/s²'), 'A_y': ('진동 가속도 Y', 'm/s²'), 'A_z': ('진동 가속도 Z', 'm/s²')}
ST = {'mean': '평균', 'rms': 'RMS', 'std': '표준편차', 'p2p': '최대 진폭', 'kurt': '첨도'}


def label_rule(df: pd.DataFrame, src: str, thr: float, name: str) -> pd.DataFrame:
    """정답 규칙: src ≥ thr → name = 1. 값이 없는 행은 정답 없음(NaN)"""
    v = pd.to_numeric(df[src], errors='coerce')
    df = df.copy()
    df[name] = np.where(v.isna(), np.nan, (v >= thr).astype(float))
    return df


# ── 데이터
def hmotp_table(path, thr_um: float = WEAR_UM) -> tuple[Table, pd.DataFrame]:
    raw = pd.read_csv(path).sort_values(['tool_id', 'cut_no']).reset_index(drop=True)
    raw = label_rule(raw, 'wear_um', thr_um, 'tool_ng')
    meta = {'tool_ng': {'label': f'공구 교체 필요 (마모 ≥ {thr_um:g} µm)'}, 'tool_id': {'label': '공구 번호'}}
    for c in raw.columns:
        k, _, s = c.rpartition('_')
        if k in CH and s in ST:
            meta[c] = {'label': f'{CH[k][0]} {ST[s]}', 'unit': '' if s == 'kurt' else CH[k][1]}
    RULES['mct_hmotp'] = f'평균 마모 ≥ {thr_um:g} µm'
    df = raw.drop(columns=['cut_id', 'cut_no', 'wear_um', 'wear_flute1_um', 'wear_flute2_um'])
    df['use_count'] = raw.groupby('tool_id').cumcount() + 1
    meta['use_count'] = {'label': '공구 누적 사용 횟수'}
    return Table('mct_hmotp', 'MCT 엔드밀 가공 (HMoTP 2024)', df, meta, {}, '', {}, 'row', 'HMoTP 2024 (공구 3개 × 100회)', entity='tool_id'), raw


def nasa_table(thr_mm: float = VB_MM, demo: Path | None = None) -> tuple[Table, pd.DataFrame]:
    M = json.load(open(demo or WEB / 'demo.json', encoding='utf-8'))['mach']
    tools = {t['id']: t for t in M['tools']}
    sig = {s['key']: s['label'] for s in M['signals']}
    rows = []
    for r in M['runs']:
        if r.get('vb') is None:
            continue
        t = tools.get(r['tool'], {})
        x = {'tool_id': r['tool'], 'cut_minutes': r.get('minute'), 'vb_mm': r['vb'], 'depth_of_cut': t.get('DOC'), 'feed': t.get('feed'), 'work_material': t.get('material')}
        x.update({k: v for k, v in (r.get('s') or {}).items()})
        rows.append(x)
    raw = pd.DataFrame(rows).sort_values(['tool_id', 'cut_minutes']).reset_index(drop=True)
    raw = label_rule(raw, 'vb_mm', thr_mm, 'tool_ng')
    meta = {'tool_ng': {'label': f'공구 교체 필요 (VB ≥ {thr_mm:g} mm)'}, 'tool_id': {'label': '공구 번호'}, 'cut_minutes': {'label': '공구 누적 가공 시간', 'unit': 'min'},
            'depth_of_cut': {'label': '절삭 깊이', 'unit': 'mm'}, 'feed': {'label': '이송량', 'unit': 'mm/rev'}, 'work_material': {'label': '피삭재 재질'}}
    meta.update({k: {'label': v} for k, v in sig.items()})
    RULES['mct_nasa'] = f'VB ≥ {thr_mm:g} mm'
    df = raw.drop(columns=['vb_mm'])
    return Table('mct_nasa', 'MCT 정면 밀링 (NASA)', df, meta, {}, '', {}, 'row', 'NASA Ames Milling (공구 16개)', entity='tool_id'), raw


def stiehl_table(path, thr_um: float = STIEHL_UM) -> tuple[Table, pd.DataFrame]:
    from .stiehl import LABEL
    raw = pd.read_csv(path).sort_values(['machine', 'tool', 'run']).reset_index(drop=True)
    raw = label_rule(raw, 'wear_um', thr_um, 'tool_ng')
    raw['tool_id'] = 'M' + raw.machine.astype(str) + 'T' + raw.tool.astype(str)
    raw['machine_id'] = 'M' + raw.machine.astype(str)
    meta = {'tool_ng': {'label': f'공구 교체 임박 (VB ≥ {thr_um:g} µm)'}, 'tool_id': {'label': '공구 번호'}, 'contact_min': {'label': '공구 누적 접촉 시간', 'unit': 'min'},
            'cut_seconds': {'label': '가공 시간', 'unit': 's'}}
    for c in raw.columns:
        k, _, st = c.rpartition('_')
        if k in LABEL and st in ST:
            meta[c] = {'label': f'{LABEL[k][0]} {ST[st]}', 'unit': '' if st == 'kurt' else LABEL[k][1]}
    df = raw.drop(columns=['file', 'machine', 'tool', 'run', 'wear_um', 'machine_id'])
    RULES['mct_stiehl'] = f'VB ≥ {thr_um:g} µm'
    return Table('mct_stiehl', 'MCT 숄더 밀링 · 설비 3대 (Stiehl 2023)', df, meta, {}, '', {}, 'row', 'Mendeley zpxs87bjt8 v3 (설비 3대 × 공구 9개, CC BY 4.0)', entity='tool_id'), raw


USAGE_COLS = {'use_count', 'cut_minutes', 'contact_min'}
VARIANTS = ('sensor', 'rel', 'usage')
VNAME = {'sensor': '센서만 (모든 슬롯)', 'rel': '센서만 · 교체 후 변화', 'usage': '센서 + 공구 누적 사용량'}
USAGE_SLOTS = ['acc:count', 'acc:exposure', 'acc:native']     # 사용 횟수 · 시간 자체 — 센서에서 나온 누적 이탈(acc:dose · drift)은 센서 정보라 남긴다


def _encode(t: Table, variant: str):
    if variant in ('sensor', 'rel'):
        t = Table(t.key, t.process, t.df.drop(columns=[c for c in USAGE_COLS if c in t.df]), t.meta, t.spec, t.lot_col, t.parents, t.unit_kind, t.source, t.series, t.entity)
    e = encode(t)
    add_upstream({t.key: e})
    Xs = standardize(e.X)[SLOTS]
    if variant in ('sensor', 'rel'):
        Xs[USAGE_SLOTS] = 0.0
    if variant == 'rel':                       # 교체 후 변화만 — 설비가 바뀌어도 같은 크기로 읽히는 입력
        Xs[[c for c in SLOTS if c not in REL_SLOTS]] = 0.0
    return e, Xs.to_numpy()


def _fit(M: CumulativeModel, parts: list[tuple[str, np.ndarray, np.ndarray]], n_batches: int = 8):
    for k, X, y in parts:
        if k not in M.procs:
            M.add_process(k, (y.sum() + 0.5) / (len(y) + 1))
    sp = {k: chunks(len(y), n_batches) for k, X, y in parts}
    for b in range(n_batches):
        M.update([(k, X[sp[k][b]], y[sp[k][b]], np.ones(len(sp[k][b]))) for k, X, y in parts if len(sp[k][b])])
    return M


def _fold_data(t: Table, test_tool: str, variant: str, groups: np.ndarray | None = None):
    """학습 묶음 → 평가 묶음 순서로 이어 붙여 인코딩 (평가 묶음의 표준화는 자기 과거와 학습 묶음만 사용)"""
    g = t.df.tool_id.to_numpy() if groups is None else groups
    tr = t.df[g != test_tool]
    te = t.df[g == test_tool]
    f = Table(t.key, t.process, pd.concat([tr, te]).reset_index(drop=True), t.meta, {}, '', {}, 'row', t.source, entity=t.entity)
    e, X = _encode(f, variant)
    k = len(tr)
    return X[:k], e.y[:k], X[k:], e.y[k:], e.X['all_max'].to_numpy()[k:]


def tool_cv(tables: dict[str, Table], variant: str, groups: dict | None = None, only: list[str] | None = None) -> dict:
    """공구(또는 groups 로 준 설비) 단위 교차검증 — 데이터셋마다 규칙 · 자체 · 합침 점수를 모아서 계산"""
    full = {k: _encode(t, variant) for k, t in tables.items()}
    res = {}
    for k, t in tables.items():
        if only and k not in only:
            continue
        G = (groups or {}).get(k)
        pool = {'rule': [], 'local': [], 'joint': [], 'y': []}
        folds = []
        for g in sorted(pd.unique(t.df.tool_id if G is None else G)):
            Xtr, ytr, Xte, yte, rule = _fold_data(t, g, variant, G)
            if not len(yte) or not len(ytr) or not ytr.sum():
                continue
            loc = _fit(CumulativeModel(SLOTS), [(k, Xtr, ytr)])
            others = [(o, full[o][1], full[o][0].y) for o in tables if o != k]
            jnt = _fit(CumulativeModel(SLOTS), [(k, Xtr, ytr)] + others)
            n1 = np.ones(len(yte))
            s_loc = loc.predict(k, Xte, n1)[0]
            s_jnt = jnt.predict(k, Xte, n1)[0]
            pool['rule'] += list(rule); pool['local'] += list(s_loc); pool['joint'] += list(s_jnt); pool['y'] += list(yte)
            folds.append({'tool': g, 'rows': int(len(yte)), 'events': int(yte.sum()),
                          **{m: (auc(s, yte) if 0 < yte.sum() < len(yte) else None) for m, s in (('rule', rule), ('local', s_loc), ('joint', s_jnt))}})
        y = np.array(pool['y'])
        res[k] = {'name': t.process, 'rows': int(len(y)), 'events': int(y.sum()), 'tools': len(folds), 'folds': folds,
                  **{m: {'auc': auc(np.array(pool[m]), y), **alert_metrics(np.array(pool[m]), y)} for m in ('rule', 'local', 'joint')}}
    return res


def normal_check_hmotp(raw: pd.DataFrame, t: Table, ref_cuts: int = 20) -> dict:
    """정답 있는 HMoTP 로 정상 기준 모드 검증: 새 공구 처음 ref_cuts 회 = 기준"""
    cols = [c for c in condition_columns(t.df.drop(columns=['use_count', 'tool_ng']), t.meta)]
    d = raw.copy()
    ref = d[d.cut_no <= ref_cuts]
    nb = NormalBaseline(q=0.99, min_rows=10).fit(ref, cols, group='tool_id')
    test = d[d.cut_no > ref_cuts]
    sc = nb.score(test)
    y = test.tool_ng.to_numpy()
    early = test.wear_um < 100
    per_tool = {g: spearman(sc.score[test.tool_id == g].to_numpy(), test.wear_um[test.tool_id == g].to_numpy()) for g in sorted(test.tool_id.unique())}
    return {'ref_cuts': ref_cuts, 'cols': len(cols), 'test_rows': int(len(test)), 'events': int(y.sum()),
            'auc': auc(sc.score.to_numpy(), y), 'spearman_wear': per_tool,
            'alert_rate_early': float(sc.alert[early].mean()), 'alert_rate_ng': float(sc.alert[y == 1].mean()) if y.sum() else None,
            **alert_metrics(sc.score.to_numpy(), y)}


def normal_check_site(files: list[Path], ref: int = 1, test: list[int] = (2, 0)) -> dict:
    """정답 없는 현장 데이터: files[ref] 기간으로 정상 기준 → 나머지 기간 알림 비율 (프로그램 · 공구별 기준)"""
    D = [pd.read_csv(f) for f in files]
    meta = {}
    cols = condition_columns(D[ref].drop(columns=['samples', 'tool_main', 'tools_used'], errors='ignore'), meta)
    nb = NormalBaseline(q=0.99, min_rows=20).fit(D[ref], cols, group=['program', 'tool_main'])
    out = {'cols': cols, 'ref_rows': len(D[ref]), 'groups': len(nb.ref) - 1, 'periods': []}
    for i in test:
        s = nb.score(D[i])
        own = s.own_baseline
        out['periods'].append({'file': Path(files[i]).name, 'rows': int(len(s)), 'covered': float(own.mean()),
                               'alert_rate_covered': float(s.alert[own].mean()) if own.any() else None,
                               'alert_rate_all': float(s.alert.mean()),
                               'top_cols': s.top[s.alert & own].value_counts().head(5).to_dict()})
    return out


def train_base(tables: dict[str, Table], variant: str) -> CumulativeModel:
    parts = []
    for k, t in tables.items():
        e, X = _encode(t, variant)
        parts.append((k, X, e.y))
    return _fit(CumulativeModel(SLOTS), parts)


def run(data_dir: str | Path, out_dir: Path, state_dir: Path, site_dir: str | Path | None = None, stiehl_um: float = STIEHL_UM) -> dict:
    data_dir = Path(data_dir)
    th, hraw = hmotp_table(data_dir / 'hmotp_cuts.csv')
    tn, _ = nasa_table()
    tables = {'mct_hmotp': th, 'mct_nasa': tn}
    RULES.update({'mct_hmotp': f'평균 마모 ≥ {WEAR_UM:g} µm', 'mct_nasa': f'VB ≥ {VB_MM:g} mm'})
    st = data_dir / 'stiehl_runs.csv'
    sraw = None
    if st.exists():
        tables['mct_stiehl'], sraw = stiehl_table(st, stiehl_um)
    cv = {v: tool_cv(tables, v) for v in VARIANTS}
    best = max(('sensor', 'rel'), key=lambda v: np.mean([cv[v][k]['joint']['auc'] for k in tables]))     # 기본 모델은 센서 입력 중 AUC 평균이 높은 쪽
    M = train_base(tables, best)
    state_dir.mkdir(parents=True, exist_ok=True)
    base = {'process': 'MCT', 'variant': best, 'label_rules': {k: RULES[k] for k in tables},
            'datasets': {k: t.source for k, t in tables.items()}, 'cv': {v: {k: {m: cv[v][k][m]['capture'] for m in ('rule', 'local', 'joint')} for k in tables} for v in cv},
            'model': M.to_dict(),
            'transfer_model': train_base(tables, 'rel').to_dict(),          # 다른 설비로 옮길 때 쓰는 입력(교체 후 변화만)
            'transfer_note': '새 설비 · 새 공장에는 transfer_model 로 시작 — 설비별 AUC 0.91~0.93 (Stiehl 설비 단위 교차검증)'}
    (state_dir / 'mct.json').write_text(json.dumps(base, ensure_ascii=False), encoding='utf-8')
    res = {'cv': cv, 'best': best, 'rules': {k: RULES[k] for k in tables}, 'normal_hmotp': normal_check_hmotp(hraw, th)}
    if sraw is not None:      # 다른 설비로 옮겼을 때 — 설비 하나를 통째로 빼고 학습 → 그 설비로 평가
        res['machine_cv'] = {v: tool_cv(tables, v, groups={'mct_stiehl': sraw.machine_id.to_numpy()}, only=['mct_stiehl'])['mct_stiehl'] for v in VARIANTS}
    cnc = data_dir / 'cnc_experiment_01.csv'
    if cnc.exists():
        S = __import__('mlng_ai.schema', fromlist=['sense_table']).sense_table(pd.read_csv(cnc))
        q = pd.Series([s.quantity for s in S.values() if s.role == 'param']).value_counts().to_dict()
        res['cnc_schema'] = {'cols': len(S), 'params': sum(s.role == 'param' for s in S.values()), 'known_quantity': sum(v for k, v in q.items() if k != 'unknown'), 'by_quantity': q}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'mct.json').write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    (out_dir / 'mct.md').write_text(report_md(res), encoding='utf-8')
    if site_dir:
        fs = sorted(Path(site_dir).glob('jeil_parts_*.csv'))
        if len(fs) >= 2:
            site = normal_check_site(fs, ref=1, test=[i for i in range(len(fs)) if i != 1])
            (Path(site_dir) / 'report_site.json').write_text(json.dumps(site, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
            (Path(site_dir) / 'report_site.md').write_text(site_md(site), encoding='utf-8')
            res['site'] = site
    return res


# ── 보고서
def _p(v):
    return '—' if v is None or v != v else f'{v * 100:.0f}%'


def _a(v):
    return '—' if v is None or v != v else f'{v:.2f}'


def report_md(r: dict) -> str:
    L = ['# MCT 기본 모델 — 공구 단위 교차검증', '',
         '정답 규칙: ' + ' · '.join(f"{SHORT.get(k, k)} {v}" for k, v in r.get('rules', {}).items()) + ' → 공구 교체 필요. 한 공구를 통째로 빼고 학습 → 뺀 공구로 평가, 공구마다 반복.',
         '값: 위험도 상위 10% 알림 안에 들어온 불량 비율 (AUC). 최대 가능 = 알림 수 ÷ 불량 수 (불량이 많으면 10% 알림으로 다 담을 수 없음). 규칙 = 가장 크게 벗어난 조건 하나 · 자체 = 그 데이터만 · 합침 = HMoTP + NASA 함께 학습(데이터별 보정).', '']
    for v, name in VNAME.items():
        L += [f'## 입력: {name}', '', '| 데이터 | 공구 · 행 · 불량 | 최대 가능 | 규칙 | 자체 | 합침 |', '|---|---|---|---|---|---|']
        for k, x in r['cv'][v].items():
            mx = min(1.0, x['rule']['alerts'] / x['events']) if x['events'] else None
            L.append(f"| {x['name']} | {x['tools']} · {x['rows']} · {x['events']} | {_p(mx)} | " + ' | '.join(f"{_p(x[m]['capture'])} ({_a(x[m]['auc'])})" for m in ('rule', 'local', 'joint')) + ' |')
        L.append('')
    if r.get('machine_cv'):
        L += ['## 다른 설비로 옮겼을 때 (Stiehl · 설비 3대)', '', '설비 하나를 통째로 빼고 나머지 설비로 학습 → 뺀 설비로 평가. 합침 = 다른 공개 MCT 데이터까지 함께 학습.', '',
              '| 입력 | 행 · 불량 | 규칙 | 자체 | 합침 | 설비별 AUC (자체) |', '|---|---|---|---|---|---|']
        for v, name in VNAME.items():
            x = r['machine_cv'][v]
            L.append(f"| {name} | {x['rows']} · {x['events']} | " + ' | '.join(f"{_p(x[m]['capture'])} ({_a(x[m]['auc'])})" for m in ('rule', 'local', 'joint'))
                     + ' | ' + ' · '.join(f"{f['tool']} {_a(f['local'])}" for f in x['folds']) + ' |')
        L.append('')
    L += [f"기본 모델로 저장한 입력: {VNAME[r['best']]} (센서 입력 중 합침 AUC 평균이 높은 쪽)",
          '«교체 후 변화» = 공구 교체 뒤 처음 10회 대비 지금 값의 변화(log 비율, 방향 유지, 물리량별). 설비마다 단위 · 크기가 달라도 같은 척도로 읽힌다.', '']
    n = r['normal_hmotp']
    L += ['## 정상 기준 모드 — 정답 있는 HMoTP 로 방법 검증', '',
          f"공구마다 처음 {n['ref_cuts']}회(새 공구)를 정상 기준으로 학습 · 조건 칼럼 {n['cols']}개 · 평가 {n['test_rows']}회 (교체 필요 {n['events']}회)", '',
          '| 항목 | 값 |', '|---|---|',
          f"| 점수 ↔ 마모 순위상관 (공구별) | {' · '.join(f'{k} {_a(v)}' for k, v in n['spearman_wear'].items())} |",
          f"| 교체 필요 판별 AUC | {_a(n['auc'])} |",
          f"| 상위 10% 알림 안의 교체 필요 비율 | {_p(n['capture'])} |",
          f"| 초기 마모(< 100 µm) 회차 알림 비율 | {_p(n['alert_rate_early'])} |",
          f"| 교체 필요 회차 알림 비율 | {_p(n['alert_rate_ng'])} |", '']
    if r.get('cnc_schema'):
        c = r['cnc_schema']
        L += ['## CNC Virtual Data set v2 — 정답 없음 · 칼럼 해석 시험', '',
              f"칼럼 {c['cols']}개 중 조건 {c['params']}개, 물리량을 알아낸 칼럼 {c['known_quantity']}개 — " + ' · '.join(f'{k} {v}' for k, v in c['by_quantity'].items()), '']
    return '\n'.join(L)


def site_md(s: dict) -> str:
    L = ['# 현장 MCT 데이터 — 정상 기준 모드 (정답 없음 · 외부 비공개)', '',
         f"기준 기간 {s['ref_rows']}개 공구 구간 · 프로그램 × 공구 기준 {s['groups']}개 · 조건 칼럼 {len(s['cols'])}개 · 임계값 = 기준 기간 점수 99% 분위", '',
         '| 평가 기간 | 구간 수 | 기준 있음 | 알림 비율 (기준 있는 구간) | 알림 상위 칼럼 |', '|---|---|---|---|---|']
    for p in s['periods']:
        L.append(f"| {p['file']} | {p['rows']} | {_p(p['covered'])} | {_p(p['alert_rate_covered'])} | {' · '.join(f'{k} {v}' for k, v in p['top_cols'].items())} |")
    return '\n'.join(L) + '\n'
