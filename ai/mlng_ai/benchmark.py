"""MCT 기준선 비교 · 현장 지표 — MILG 엔진이 일반 머신러닝보다 나은지, 현장 언어(선행 · 오경보 · 놓침)로 얼마인지.

  같은 검증 방식(공구 하나 · 설비 하나를 통째로 빼고 학습 → 그것으로 평가)으로
    규칙 · MILG 엔진(센서 · 교체 후 변화) · 로지스틱 회귀 · 랜덤포레스트 · 그래디언트 부스팅 · 랜덤포레스트 + 교체 후 변화
  를 비교한다. 공구 누적 사용량(횟수 · 시간)은 모든 모델에서 뺀다 («사용량만 보고 맞히는» 효과 차단).

  현장 지표 (임계값은 학습 묶음에서 정상 행 오경보 5% 가 되도록 미리 고정 → 평가 묶음에 그대로 적용)
    선행 회차   첫 «교체 임박» 회차 − 첫 알림 회차 (양수 = 미리 알림)
    놓침        첫 «교체 임박» 회차까지 알림이 한 번도 없던 공구 비율
    오경보율    정상(교체 임박 아님) 회차 중 알림 비율
    조기 알림   정상 회차 중 수명의 절반 이전에 난 알림 비율

  python -m mlng_ai benchmark <MCT 요약 폴더> <보고서 폴더>
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .engine import auc
from .evaluate import alert_metrics
from .features import SLOTS
from .mct import USAGE_COLS, _fit, _fold_data, hmotp_table, nasa_table, stiehl_table
from .model import CumulativeModel

FA = 0.05            # 학습 묶음 정상 행 오경보 목표 → 임계값
REL_N = 10           # 교체 후 처음 N회 = 기준

NAMES = {'rule': '규칙 (최대 이탈)', 'eng_sensor': 'MILG 엔진 · 센서', 'eng_rel': 'MILG 엔진 · 교체 후 변화', 'eng_local': 'MILG 엔진 + 공정 전용 입력', 'lr': '로지스틱 회귀',
         'rf': '랜덤포레스트', 'gb': '그래디언트 부스팅', 'rf_rel': '랜덤포레스트 + 교체 후 변화', 'gb_rel': '그래디언트 부스팅 + 교체 후 변화',
         'gb_slot': 'MILG 슬롯 + 부스팅', 'gb_slot_rel': 'MILG 교체 후 변화 슬롯 + 부스팅'}


def _ml_frame(t, rel: bool = False) -> pd.DataFrame:
    """일반 ML 입력 — 센서 칼럼 그대로 (식별자 · 정답 · 사용량 제외). rel=True 면 공구별 처음 N회 대비 log 비율을 덧붙인다"""
    df = t.df
    X = df.drop(columns=[c for c in ['tool_id', 'tool_ng', *USAGE_COLS] if c in df])
    for c in X.columns:
        if not pd.api.types.is_numeric_dtype(X[c]):
            X[c] = X[c].astype('category').cat.codes
    X = X.astype(float)
    if rel:
        R = {}
        g = df.tool_id.to_numpy()
        for c in X.columns:
            v = X[c].abs()
            ref = v.groupby(g).transform(lambda s: s.iloc[:REL_N].mean())
            R[f'rel_{c}'] = np.log((v + 1e-9) / (ref + 1e-9))
        X = pd.concat([X, pd.DataFrame(R, index=X.index)], axis=1)
    return X.fillna(X.median()).fillna(0.0)


def _ml_models():
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {'lr': lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=2000, class_weight='balanced')),
            'rf': lambda: RandomForestClassifier(n_estimators=300, min_samples_leaf=3, class_weight='balanced_subsample', n_jobs=-1, random_state=0),
            'gb': lambda: HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, random_state=0),
            'rf_rel': lambda: RandomForestClassifier(n_estimators=300, min_samples_leaf=3, class_weight='balanced_subsample', n_jobs=-1, random_state=0),
            'gb_rel': lambda: HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, random_state=0)}


def _thr(score_tr, y_tr) -> float:
    neg = np.asarray(score_tr)[np.asarray(y_tr) == 0]
    return float(np.quantile(neg, 1 - FA)) if len(neg) else float('inf')


def _ops(score, y, tools, thr, minutes=None) -> list[dict]:
    """평가 묶음의 공구마다 선행 · 놓침 · 오경보"""
    out = []
    for g in pd.unique(tools):
        m = tools == g
        s, yy = np.asarray(score)[m], np.asarray(y)[m]
        a = s > thr
        n = len(yy)
        ev = np.flatnonzero(yy == 1)
        r = {'tool': str(g), 'runs': int(n), 'neg': int((yy == 0).sum()), 'fa': int((a & (yy == 0)).sum()),
             'early': int((a & (yy == 0) & (np.arange(n) < n / 2)).sum())}
        if len(ev):
            e0 = int(ev[0])
            al = np.flatnonzero(a[:e0 + 1])
            r.update(event_run=e0, detected=bool(len(al)), lead=(e0 - int(al[0])) if len(al) else None)
            later = np.flatnonzero(a[e0:])
            r['delay'] = int(later[0]) if not len(al) and len(later) else None
            if minutes is not None and len(al):
                mm = np.asarray(minutes)[m]
                r['lead_min'] = float(mm[e0] - mm[al[0]])
        out.append(r)
    return out


def _summ(ops: list[dict]) -> dict:
    ev = [o for o in ops if 'event_run' in o]
    leads = [o['lead'] for o in ev if o.get('lead') is not None]
    lm = [o['lead_min'] for o in ev if o.get('lead_min') is not None]
    neg = sum(o['neg'] for o in ops)
    return {'tools': len(ev), 'missed': sum(not o['detected'] for o in ev), 'miss_rate': (sum(not o['detected'] for o in ev) / len(ev)) if ev else None,
            'lead_median': float(np.median(leads)) if leads else None, 'lead_min_median': float(np.median(lm)) if lm else None,
            'fa_rate': (sum(o['fa'] for o in ops) / neg) if neg else None, 'early_rate': (sum(o['early'] for o in ops) / neg) if neg else None}


def compare(t, groups: np.ndarray | None = None, minutes_col: str | None = None) -> dict:
    """공구(또는 groups) 단위로 하나씩 빼며 모든 모델 학습 → 평가 묶음 점수를 모아 AUC · 상위 10% · 현장 지표"""
    G = t.df.tool_id.to_numpy() if groups is None else groups
    Xml, Xrel = _ml_frame(t), _ml_frame(t, rel=True)
    from .local_inputs import local_frame
    Lf = local_frame(t.df)                     # 엔진 공정 전용 입력: 절반 넘게 빈 칼럼(설비마다 다른 채널)은 뺀다
    y_all = t.df.tool_ng.to_numpy()
    lab = ~np.isnan(y_all)
    tools_all = t.df.tool_id.to_numpy()
    mins = t.df[minutes_col].to_numpy(float) if minutes_col and minutes_col in t.df else None
    mk = _ml_models()
    pool = {k: [] for k in NAMES}
    ys, ops = [], {k: [] for k in NAMES}
    for g in sorted(pd.unique(G)):
        te = (G == g)
        tr = ~te
        if not (y_all[tr & lab] == 1).any() or not (te & lab).any():
            continue
        S = {}
        # MILG 엔진 (인코딩은 학습 → 평가 순서, 평가 표준화는 과거만)
        for v in ('sensor', 'rel'):
            Xtr, ytr, Xte, yte, rule = _fold_data(t, g, v, groups)
            Mdl = _fit(CumulativeModel(SLOTS), [(t.key, Xtr, ytr)])
            S[f'eng_{v}'] = (Mdl.predict(t.key, Xtr, np.ones(len(ytr)))[0], Mdl.predict(t.key, Xte, np.ones(len(yte)))[0], ytr)
            ok = ~np.isnan(ytr)        # 같은 슬롯 입력 위에 판정기만 부스팅으로 — 표현은 MILG, 판정은 비선형
            gbm = mk['gb']().fit(Xtr[ok], ytr[ok].astype(int))
            S['gb_slot' if v == 'sensor' else 'gb_slot_rel'] = (gbm.predict_proba(Xtr[ok])[:, 1], gbm.predict_proba(Xte)[:, 1], ytr[ok])
            if v == 'sensor':
                S['rule'] = (None, rule, ytr)
                from .local_inputs import engine_with_local          # 엔진 그대로 + 그 공정 원본 칼럼 · 교체 후 변화 (부스팅 없음)
                s_a, s_b, _, _ = engine_with_local(t.key, Xtr, ytr, Xte, Lf[tr], Lf[te])
                S['eng_local'] = (s_a, s_b, ytr[~np.isnan(ytr)])
        yte = y_all[te]
        for k, f in mk.items():
            X = Xrel if k.endswith('_rel') else Xml
            mtr = tr & lab
            clf = f().fit(X[mtr], y_all[mtr].astype(int))
            S[k] = (clf.predict_proba(X[mtr])[:, 1], clf.predict_proba(X[te])[:, 1], y_all[mtr])
        keep = ~np.isnan(yte)
        for k, (s_tr, s_te, y_tr) in S.items():
            s_te = np.asarray(s_te, float)
            pool[k] += list(s_te[keep])
            if s_tr is not None:
                ok = ~np.isnan(np.asarray(y_tr, float))
                thr = _thr(np.asarray(s_tr)[ok], np.asarray(y_tr)[ok])
                ops[k] += _ops(s_te[keep], yte[keep], tools_all[te][keep], thr, None if mins is None else mins[te][keep])
        ys += list(yte[keep])
    y = np.array(ys)
    res = {'rows': int(len(y)), 'events': int(y.sum()), 'models': {}}
    for k in NAMES:
        s = np.array(pool[k], float)
        res['models'][k] = {'auc': auc(s, y), 'capture10': alert_metrics(s, y)['capture'], **({'ops': _summ(ops[k])} if ops[k] else {})}
    return res


def save_boosters(jobs, state_dir: Path, res: dict) -> list[str]:
    """데이터셋마다 판정기(부스팅 + 교체 후 변화)를 전체 데이터로 학습해 저장 — 같은 센서 구성 공장의 출발점"""
    from .booster import ProcessBooster
    from .mct import SHORT
    state_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for name, t, grp, mc in jobs:
        if grp is not None:
            continue
        cols = [c for c in _ml_frame(t).columns if pd.api.types.is_numeric_dtype(t.df[c])]
        b = ProcessBooster().fit(t.df, t.df.tool_ng, t.df.tool_id.to_numpy(), cols)
        m = res[name]['models'].get('gb_rel', {})
        p = state_dir / f'mct_booster_{SHORT[t.key].lower()}'
        b.save(p, {'dataset': t.source, 'cv_auc': m.get('auc'), 'cv_ops': m.get('ops')})
        out.append(p.with_suffix('.json').name)
    return out


def run(data_dir, out_dir, state_dir: Path | None = None) -> dict:
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    th, hraw = hmotp_table(data_dir / 'hmotp_cuts.csv')
    tn, nraw = nasa_table()
    jobs = [('HMoTP · 공구 단위', th, None, None), ('NASA · 공구 단위', tn, None, 'cut_minutes')]
    if (data_dir / 'stiehl_runs.csv').exists():
        ts, sraw = stiehl_table(data_dir / 'stiehl_runs.csv')
        jobs += [('Stiehl · 공구 단위', ts, None, 'contact_min'), ('Stiehl · 설비 단위 (다른 설비로 이전)', ts, sraw.machine_id.to_numpy(), 'contact_min')]
    res = {}
    for name, t, grp, mc in jobs:
        t0 = time.time()
        res[name] = compare(t, grp, mc)
        print(f'  {name} {time.time() - t0:.0f}초', flush=True)
    if state_dir is not None:
        print('  판정기 저장:', ', '.join(save_boosters(jobs, Path(state_dir), res)))
        from .local_inputs import train_and_save
        from .mct import SHORT
        for name, t, grp, mc in jobs:          # 엔진 보강 모델 (자체 엔진만) — 같은 센서 구성 공장의 출발점
            if grp is None:
                p = Path(state_dir) / f'mct_engine_{SHORT[t.key].lower()}.json'
                train_and_save(t, p, {'dataset': t.source, 'cv_auc': res[name]['models']['eng_local']['auc'], 'cv_ops': res[name]['models']['eng_local'].get('ops')})
                print('  엔진 보강 모델 저장:', p.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'benchmark.json').write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    (out_dir / 'benchmark.md').write_text(report_md(res), encoding='utf-8')
    return res


def _f(v, p=2, pct=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return '—'
    return f'{v:.0%}' if pct else f'{v:.{p}f}'


def report_md(res: dict) -> str:
    L = ['# MCT 기준선 비교 · 현장 지표', '',
         '같은 검증 방식(공구 · 설비를 통째로 빼고 학습 → 그것으로 평가). 공구 누적 사용량은 모든 모델에서 제외.',
         f'현장 지표 임계값 = 학습 묶음 정상 행 오경보 {FA:.0%} 로 미리 고정.', '']
    for name, r in res.items():
        L += [f'## {name} — {r["rows"]}행 · 교체 임박 {r["events"]}행', '',
              '| 모델 | AUC | 상위 10% 알림 안 비율 | 놓친 공구 | 선행 회차 (중앙값) | 선행 시간 (분) | 오경보율 | 수명 전반부 알림 |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
        for k, m in r['models'].items():
            o = m.get('ops', {})
            miss = f'{o["missed"]}/{o["tools"]}' if o else '—'
            L.append(f'| {NAMES[k]} | {_f(m["auc"])} | {_f(m["capture10"], pct=True)} | {miss} | {_f(o.get("lead_median"), 0)} | {_f(o.get("lead_min_median"), 1)} | '
                     f'{_f(o.get("fa_rate"), pct=True)} | {_f(o.get("early_rate"), pct=True)} |')
        L.append('')
    return '\n'.join(L)
