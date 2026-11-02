"""학습 · 검증 엔진

  build()        모든 공정 표 → 칼럼 해석 → 의미 슬롯 → 앞 공정 연결 → 공정 안 누적 표준화
  prequential()  시간순 누적 학습: 배치마다 «먼저 예측 → 채점 → 그다음 학습» (실제 운영과 같은 조건)
  cold_start()   처음 보는 공정: 다른 공정으로 미리 배운 모델 vs 그 공정만으로 처음 배우는 모델 — 적은 데이터에서 누가 나은가
  injection()    공정 간 원인 추적 검증: 앞 공정 온도가 뒤 공정 불량을 키우도록 «정답을 심은» 가상 데이터로, 찾아내는지 · 없는 것을 만들어내지 않는지
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .datasets import Table, all_tables
from .features import SLOTS, add_upstream, encode, standardize
from .model import CumulativeModel

warnings.filterwarnings('ignore', category=RuntimeWarning)


def build(tables: list[Table] | None = None, overrides: dict | None = None, llm: bool = False):
    tables = tables if tables is not None else all_tables()
    E = {t.key: encode(t, (overrides or {}).get(t.key), llm) for t in tables}
    add_upstream(E)
    for e in E.values():
        e.Xs = standardize(e.X)[SLOTS]
    return E


def poisson_dev(y, mu):
    mu = np.maximum(mu, 1e-12)
    with np.errstate(divide='ignore', invalid='ignore'):
        t = np.where(y > 0, y * np.log(y / mu), 0.0)
    return 2 * np.sum(t - (y - mu))


def auc(score, y):
    y = np.asarray(y) > 0
    if y.sum() == 0 or (~y).sum() == 0:
        return float('nan')
    r = pd.Series(score).rank().to_numpy()
    return float((r[y].sum() - y.sum() * (y.sum() + 1) / 2) / (y.sum() * (~y).sum()))


def psi(a, b, bins=8):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 20 or len(b) < 10 or np.std(a) < 1e-9:
        return 0.0
    cuts = np.unique(np.quantile(a, np.linspace(0, 1, bins + 1)[1:-1]))
    pa = np.bincount(np.searchsorted(cuts, a), minlength=len(cuts) + 1) / len(a)
    pb = np.bincount(np.searchsorted(cuts, b), minlength=len(cuts) + 1) / len(b)
    pa, pb = np.clip(pa, 1e-4, None), np.clip(pb, 1e-4, None)
    return float(np.sum((pb - pa) * np.log(pb / pa)))


def chunks(n, k):
    return np.array_split(np.arange(n), k)


def new_model(E):
    m = CumulativeModel(SLOTS)
    return m


# ── 시간순 누적 학습 (공정들이 동시에 쌓인다)
def prequential(E, n_batches: int = 20, keys: list[str] | None = None, drift_on: bool = True):
    keys = keys or list(E)
    M = new_model(E)
    parts = {k: chunks(len(E[k].y), n_batches) for k in keys}
    pred = {k: np.full(len(E[k].y), np.nan) for k in keys}
    base = {k: np.full(len(E[k].y), np.nan) for k in keys}
    gpred = {k: np.full(len(E[k].y), np.nan) for k in keys}
    events = []
    for b in range(n_batches):
        batch, lam = [], 0.995
        for k in keys:
            ix = parts[k][b]
            if not len(ix):
                continue
            e = E[k]
            X, y, n = e.Xs.to_numpy()[ix], e.y[ix], e.n[ix]
            seen = np.concatenate(parts[k][:b]) if b else np.array([], int)
            if k in M.procs and len(seen):
                mu, _ = M.predict(k, X, n)
                pred[k][ix] = mu
                gpred[k][ix] = M.predict_gated(k, X, n)[0]
                r0 = (e.y[seen].sum() + 0.5) / (e.n[seen].sum() + 1)
                base[k][ix] = r0 * n
                if drift_on and len(seen) >= 60 and len(ix) >= 20:
                    pv = max(psi(e.X.iloc[seen][c], e.X.iloc[ix][c]) for c in ('all_max', 'n_out', 'spread'))
                    if pv > 0.25:
                        lam = min(lam, 0.9); events.append({'batch': b, 'proc': k, 'psi': round(pv, 3)})
            else:
                M.add_process(k, (y.sum() + 0.5) / (n.sum() + 1))
            batch.append((k, X, y, n))
        M.update(batch, lam=lam)
        M.history.append({'batch': b, 'version': M.version, 'lam': lam})
    res = {}
    for k in keys:
        ok = np.isfinite(pred[k])
        e = E[k]
        if ok.sum() < 5:
            continue
        y, mu, mb = e.y[ok], pred[k][ok], base[k][ok]
        d1, d0 = poisson_dev(y, mu), poisson_dev(y, mb)
        dg = poisson_dev(y, gpred[k][ok])
        r = {'n_eval': int(ok.sum()), 'events': float(y.sum()), 'dev_skill': float(1 - d1 / d0) if d0 > 0 else float('nan'),
             'gated_skill': float(1 - dg / d0) if d0 > 0 else float('nan'), 'gate': M.gate_weight(k)}
        if e.n.max() <= 1:
            r['auc'] = auc(mu / e.n[ok], y)
        else:
            r['spearman'] = float(pd.Series(mu / e.n[ok]).corr(pd.Series(y / e.n[ok]), method='spearman'))
        res[k] = r
    return M, res, events, pred


# ── 처음 보는 공정 (콜드 스타트)
def _fit(M, E, keys, upto=None, n_batches=10):
    parts = {k: chunks(len(E[k].y) if upto is None or k not in upto else upto[k], n_batches) for k in keys}
    for b in range(n_batches):
        batch = []
        for k in keys:
            ix = parts[k][b]
            if not len(ix):
                continue
            e = E[k]
            if k not in M.procs:
                M.add_process(k, (e.y[ix].sum() + 0.5) / (e.n[ix].sum() + 1))
            batch.append((k, e.Xs.to_numpy()[ix], e.y[ix], e.n[ix]))
        M.update(batch)
    return M


def cold_start(E, target: str, fracs=(0.02, 0.05, 0.1, 0.2, 0.4, 0.6), test_frac=0.4):
    e = E[target]
    N = len(e.y)
    cut = int(N * (1 - test_frac))
    te = np.arange(cut, N)
    others = [k for k in E if k != target]
    pre = _fit(new_model(E), E, others)
    snap = pre.to_dict()
    out = []
    # 학습 0개: 다른 공정에서 배운 공통 효과만으로 이 공정 LOT 의 «위험 순위» 를 매긴다
    beta = pre.m[:pre.K]
    z0 = e.Xs.to_numpy()[te] @ beta
    out.append({'frac': 0.0, 'n_train': 0, 'events_train': 0.0, 'zero_shot_auc': auc(z0, e.y[te]) if e.n.max() <= 1 else float(pd.Series(z0).corr(pd.Series(e.y[te] / e.n[te]), method='spearman'))})
    for f in fracs:
        k = max(5, int(N * f))
        if k > cut:
            break
        tr = np.arange(k)
        r0 = (e.y[tr].sum() + 0.5) / (e.n[tr].sum() + 1)
        d0 = poisson_dev(e.y[te], r0 * e.n[te])
        row = {'frac': f, 'n_train': k, 'events_train': float(e.y[tr].sum())}
        for name, M in (('transfer', CumulativeModel.from_dict(snap)), ('fresh', new_model(E))):
            M.add_process(target, r0)
            for ix in chunks(k, max(1, min(5, k // 5))):
                M.update([(target, e.Xs.to_numpy()[ix], e.y[ix], e.n[ix])])
            mu, _ = M.predict(target, e.Xs.to_numpy()[te], e.n[te])
            row[name] = float(1 - poisson_dev(e.y[te], mu) / d0) if d0 > 0 else float('nan')
            if e.n.max() <= 1:
                row[name + '_auc'] = auc(mu, e.y[te])
        out.append(row)
    return out


# ── 공정 간 원인 추적 검증 (정답을 심은 가상 데이터)
def synthetic_tables(effect: float, seed: int, n_up=120, per=8):
    """앞 공정 A(로 온도 · 압력 · 시간) → 뒤 공정 B(전류 · 속도) 부품. B 불량 확률이 A 의 로 온도 이탈에 따라 effect 배수로 커진다"""
    r = np.random.default_rng(seed)
    t0 = pd.Timestamp('2026-01-01')
    A = pd.DataFrame({'lot_no': [f'A{i:04d}' for i in range(n_up)], 'start_time': [str(t0 + pd.Timedelta(hours=6 * i)) for i in range(n_up)],
                      'furnace_temp [°C]': 850 + r.normal(0, 4, n_up), 'press_force [kN]': 120 + r.normal(0, 3, n_up), 'cycle_time [s]': 42 + r.normal(0, 1, n_up),
                      'qty': r.integers(800, 1200, n_up)})
    A['bad_count'] = r.poisson(A['qty'] * 2e-4)
    zt = (A['furnace_temp [°C]'] - 850) / 4
    rows = []
    for i in range(n_up):
        for j in range(per):
            rows.append({'serial': f'B{i:04d}{j}', 'inspect_time': str(t0 + pd.Timedelta(hours=6 * i + 3, minutes=j)), 'lot': f'B{i:04d}', 'parent': A.lot_no[i],
                         'spindle_current [A]': 12 + r.normal(0, 0.5), 'feed_speed [rpm]': 1500 + r.normal(0, 20)})
    B = pd.DataFrame(rows)
    zi = np.repeat(np.abs(zt.to_numpy()), per)
    p = 0.04 * np.exp(np.log(effect) * np.clip(zi - 1, 0, None))
    B['defect'] = (r.random(len(B)) < np.clip(p, 0, 0.9)).astype(int)
    par = {f'B{i:04d}': [A.lot_no[i]] for i in range(n_up)}
    ta = Table('synA', '가상 앞 공정', A, {}, {}, 'lot_no', {}, 'lot')
    tb = Table('synB', '가상 뒤 공정', B.drop(columns=['parent']), {}, {}, 'lot', par, 'row')
    return [ta, tb]


def injection(effects=(1.0, 1.5, 2.0, 3.0), seeds=range(5), n_up=300, per=10):
    out = []
    for ef in effects:
        hits, ranks = 0, []
        for s in seeds:
            E = build(synthetic_tables(ef, s, n_up, per))
            M, _, _, _ = prequential(E, n_batches=10, drift_on=False)
            pe = {r['slot']: r for r in M.process_effects('synB')['effects']}
            up = pe['up:q:temperature']
            cand = sorted([r for r in pe.values() if r['slot'] != 'up:has'], key=lambda r: -r['beta'])
            ranks.append([r['slot'] for r in cand].index('up:q:temperature') + 1)
            hits += int(up['lo'] > 1.0)
        out.append({'effect': ef, 'detected': hits / len(list(seeds)), 'median_rank': float(np.median(ranks)), 'seeds': len(list(seeds))})
    return out
