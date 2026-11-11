"""MES 화면용 MCT 공구 위험도 — «MILG 엔진 + 공정 전용 입력» 을 MES 데모의 MCT 가공 기록에 적용한다.

  python -m mlng_ai export-mct [--vb 0.35]

  입력  web/data/demo.json 의 mach (NASA Ames Milling · 공구 16개)
  방식  공구 하나를 통째로 빼고 나머지 공구로 학습 → 뺀 공구의 가공마다 위험도 (그 공구는 학습에 쓰지 않음)
        공구 누적 사용량 제외 · 정답 = VB ≥ --vb (기본 0.35 mm = MES 주의 기준)
        알림 기준 = 학습 공구의 정상 가공 95% 분위 · 알림 = 2회 연속 기준 초과
  출력  web/data/ai_model.json 의 «mct» 키 (가공별 위험도 · 원인 후보 · 공구별 알림 시점 · 검증 요약 · 기준선 비교)
        부스팅은 비교 수치로만 싣는다 — 위험도 계산에는 쓰지 않는다
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd

from .datasets import ROOT, Table
from .engine import auc
from .features import SLOT_KO, SLOTS
from .local_inputs import FIT, LocalScaler, fit_engine, local_frame
from .mct import _fold_data, label_rule

WEB = ROOT / 'web' / 'data'
REPORTS = ROOT / 'ai' / 'reports'
KEY = 'mct_nasa'
COND = {'depth_of_cut': '절삭 깊이', 'feed': '이송량'}
BENCH = [('HMoTP · 공구 단위', 'MCT 엔드밀', '공구 3 · 300회'), ('NASA · 공구 단위', 'MCT 정면 밀링', '공구 16 · 146회'),
         ('Stiehl · 공구 단위', 'MCT 숄더 밀링', '공구 9 · 6,418회'), ('Stiehl · 설비 단위 (다른 설비로 이전)', 'MCT 숄더 밀링 · 다른 설비', '설비 3대')]
PF = [('pf_delta', '압입 · 바퀴 림 조립'), ('pf_snap', '압입 · 스냅핏 삽입')]


def _r(x, d=3):
    return None if x is None or not np.isfinite(x) else round(float(x), d)


def _table(mach: dict, thr: float) -> tuple[Table, pd.DataFrame]:
    tools = {t['id']: t for t in mach['tools']}
    rows = []
    for r in mach['runs']:
        if r.get('bad'):
            continue
        t = tools[r['tool']]
        rows.append({'tool_id': r['tool'], 'run': r['run'], 'cut_minutes': r['minute'], 'vb_mm': r.get('vb'), 'vb_now': r.get('vb_now'),
                     'depth_of_cut': t.get('DOC'), 'feed': t.get('feed'), 'work_material': t.get('material'), **(r.get('s') or {})})
    raw = pd.DataFrame(rows).sort_values(['tool_id', 'cut_minutes']).reset_index(drop=True)
    raw = label_rule(raw, 'vb_mm', thr, 'tool_ng')
    meta = {'tool_ng': {'label': f'공구 교체 필요 (VB ≥ {thr:g} mm)'}, 'tool_id': {'label': '공구 번호'}, 'cut_minutes': {'label': '공구 누적 가공 시간', 'unit': 'min'},
            'depth_of_cut': {'label': '절삭 깊이', 'unit': 'mm'}, 'feed': {'label': '이송량', 'unit': 'mm/rev'}, 'work_material': {'label': '피삭재 재질'}}
    meta.update({s['key']: {'label': s['label']} for s in mach['signals']})
    df = raw.drop(columns=['vb_mm', 'vb_now', 'run'])
    return Table(KEY, 'MCT 정면 밀링 (NASA)', df, meta, {}, '', {}, 'row', 'NASA Ames Milling', entity='tool_id'), raw


def _name(slot: str, sig: dict) -> dict:
    """엔진 슬롯 → 화면 표시 (신호 이름 · 교체 후 변화 여부)"""
    if slot.startswith('L:'):
        c = slot[2:]
        rel = c.startswith('rel_')
        c = c[4:] if rel else c
        return {'label': sig.get(c) or COND.get(c) or c, 'rel': rel, 'key': c}
    return {'label': SLOT_KO.get(slot, slot), 'rel': slot.startswith('rel:'), 'key': slot}


def _first_run(flags: np.ndarray, runs: np.ndarray, need: int = 1):
    for i in range(len(flags) - need + 1):
        if flags[i:i + need].all():
            return int(runs[i])
    return None


def _bench() -> list[dict]:
    out = []
    p = REPORTS / 'benchmark.json'
    if p.exists():
        B = json.loads(p.read_text(encoding='utf-8'))
        for k, name, size in BENCH:
            m = (B.get(k) or {}).get('models') or {}
            if not m:
                continue
            move = '설비 단위' in k
            eng = m['eng_rel' if move else 'eng_local']
            out.append({'name': name, 'size': size, 'unit': '설비를 통째로 뺌' if move else '공구를 통째로 뺌', 'rule': _r(m['rule']['auc'], 2), 'engine': _r(eng['auc'], 2), 'boost': _r(m['gb_rel']['auc'], 2),
                        'input': '교체 후 변화만' if move else '공정 전용 입력'})
    p = REPORTS / 'mct_pressfit.json'
    if p.exists():
        P = json.loads(p.read_text(encoding='utf-8'))
        for k, name in PF:
            r = P.get(k) or {}
            if 'plus' in r:
                out.append({'name': name, 'size': f"곡선 {r['rows']}개", 'unit': f"{r['unit']}을 통째로 뺌", 'rule': _r(r['rule']['auc'], 2), 'engine': _r(r['plus']['auc'], 2), 'boost': None, 'input': '곡선 5구간 · 양쪽 벗어남'})
    p = REPORTS / 'packs.json'                                  # 사출 · 프레스 · 용접 학습 팩 중 적용된 것
    if p.exists():
        from .packs import web_rows
        out += web_rows(json.loads(p.read_text(encoding='utf-8')))
    return out


def run(vb: float = 0.35, limit: float = 0.5, write: bool = True) -> dict:
    mach = json.loads((WEB / 'demo.json').read_text(encoding='utf-8'))['mach']
    sig = {s['key']: s['label'] for s in mach['signals']}
    t, raw = _table(mach, vb)
    G = raw.tool_id.to_numpy()
    lab = raw.tool_ng.notna().to_numpy()
    S = np.full(len(raw), np.nan)
    TH, TOP, FOLDS = {}, {}, {}
    from .train_widget_engine import calibration_partition
    for g in sorted(pd.unique(G)):
        train,validation,test=calibration_partition(G,np.nan_to_num(raw.tool_ng.to_numpy()),g)
        train &= lab
        FOLDS[g]={"training_tools":sorted(set(G[train])),"calibration_tools":sorted(set(G[validation]))}
        evaluation=validation|test
        tr=t.df[train]
        te=t.df[evaluation].copy()
        # Prediction input never includes validation/test outcomes in schema fitting.
        te['tool_ng']=0.
        df=pd.concat([tr,te]).reset_index(drop=True)
        split=np.array(['train']*len(tr)+['evaluation']*len(te))
        Xa,ya,Xb,_,_=_fold_data(Table(KEY,t.process,df,t.meta,{},'',{},'row',t.source,entity='tool_id'),'evaluation','sensor',split)
        L=local_frame(df,'tool_id');m=split=='evaluation'
        sc=LocalScaler().fit(L[~m])
        A,B=np.column_stack([Xa,sc.transform(L[~m])]),np.column_stack([Xb,sc.transform(L[m])])
        M=fit_engine(KEY,A,np.asarray(ya,float),list(SLOTS)+sc.names)
        scores=M.predict(KEY,B,np.ones(len(B)))[0]
        val_in_eval=validation[evaluation]&lab[evaluation]
        normals=val_in_eval&(raw.tool_ng.to_numpy()[evaluation]==0)
        if not normals.any():
            raise ValueError('No independent normal calibration cycles for '+g)
        TH[g]=float(np.quantile(scores[normals],.95,method='higher'))
        selected=test[evaluation]
        S[test]=scores[selected]
        TOP[g]=[[{**_name(r['slot'],sig),'x':_r(r['x'],1),'c':_r(r['contrib'],2)} for r in M.explain(KEY,B[i]) if r['contrib']>.05][:3] for i in np.flatnonzero(selected)]
    p = lambda s: 1 - np.exp(-np.asarray(s, float))               # 기대 사건 수 → 확률
    y = raw.tool_ng.to_numpy()
    ok = ~np.isnan(y)
    raw['p'], raw['thr'] = p(S), p(raw.tool_id.map(TH).to_numpy())
    raw['al'] = raw.p > raw.thr

    tools, over, caught, leads, early = {}, 0, 0, [], 0
    for g, d in raw.groupby('tool_id', sort=True):
        runs, al, vbn = d.run.to_numpy(), d.al.to_numpy(), d.vb_now.to_numpy(float)
        a1 = _first_run(al, runs, 2)                                # 2회 연속 기준 초과
        warn, lim = _first_run(vbn >= vb, runs), _first_run(vbn >= limit, runs)
        lead = None
        if lim is not None:
            over += 1
            if a1 is not None and a1 < lim:
                caught += 1
                lead = int((runs < lim).sum() - (runs < a1).sum())  # 알림부터 교체 기준 도달까지 가공 횟수
                leads.append(lead)
        if a1 is not None and (warn is None or a1 < warn) and float(np.nanmax(vbn)) < vb:
            early += 1                                              # 주의 기준에 끝내 닿지 않았는데 알림
        tools[g] = {**FOLDS[g], 'thr': _r(d.thr.iloc[0]), 'alert_run': a1, 'warn_run': warn, 'limit_run': lim, 'lead': lead,
                    'runs': [{'run': int(r.run), 'p': _r(r.p), 'al': bool(r.al), 'top': TOP[g][i]} for i, r in enumerate(d.itertuples())]}
    norm = ok & (y == 0)
    out = {'built': dt.datetime.now().strftime('%Y-%m-%d %H:%M'), 'engine': 'MILG 엔진 + 공정 전용 입력', 'source': mach.get('source') or 'NASA Ames Milling Data Set',
           'vb': vb, 'limit': limit, 'label': f'VB ≥ {vb:g} mm', 'method': '공구를 통째로 빼고 학습·검증·시험 분리 · 알림 기준은 검증 공구에서 결정', 'usage_excluded': True, 'alert_rule': '2회 연속 검증 기준 초과', 'fit': FIT, 'threshold_source':'held-out calibration tools', 'causal_features':True,
           'n_tools': int(len(tools)), 'n_runs': int(len(raw)), 'n_labelled': int(ok.sum()), 'n_events': int(np.nansum(y)), 'auc': _r(auc(S[ok], y[ok])),
           'ops': {'over': over, 'caught': caught, 'lead_median': _r(np.median(leads), 1) if leads else None, 'lead_min': min(leads) if leads else None, 'lead_max': max(leads) if leads else None,
                   'false_alarm': _r(raw.al.to_numpy()[norm].mean()), 'early_tools': early},
           'tools': tools, 'bench': _bench()}
    out['evaluation_version']='causal-grouped-v2'
    for row in out['bench']:
        row['verification_status']='legacy_needs_strict_revalidation'
    if write:
        f = WEB / 'ai_model.json'
        art = json.loads(f.read_text(encoding='utf-8'))
        art['mct'] = out
        f.write_text(json.dumps(art, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    return out
