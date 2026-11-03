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
from .features import GROUPS, SLOTS, add_upstream, encode, standardize
from .model import CumulativeModel

warnings.filterwarnings('ignore', category=RuntimeWarning)


def build(tables: list[Table] | None = None, overrides: dict | None = None, llm: bool = False):
    tables = tables if tables is not None else all_tables()
    E = {t.key: encode(t, (overrides or {}).get(t.key), llm) for t in tables}
    add_upstream(E)
    for e in E.values():
        e.Xs = standardize(e.X)[SLOTS]
    return E


def ablate(E, groups: list[str]):
    """제거 실험: 지정한 슬롯 묶음(적산 · 파형 · 텍스트)을 0 으로 — «이게 없으면 얼마나 나빠지나»"""
    drop = [s for g in groups for s in GROUPS[g]]
    drop += ['up:' + s for s in drop if 'up:' + s in SLOTS]
    for e in E.values():
        e.Xs = e.Xs.copy(); e.Xs[drop] = 0.0
    return E


def compare(make_E, groups: list[str], n_batches: int = 20, keys=None):
    """같은 데이터 · 같은 순서로 «전부» vs «이 묶음 빼고» 를 돌려 공정마다 비교"""
    _, full, _, _ = prequential(make_E(), n_batches=n_batches, keys=keys, drift_on=False)
    _, base, _, _ = prequential(ablate(make_E(), groups), n_batches=n_batches, keys=keys, drift_on=False)
    out = {}
    for k in full:
        f, b = full[k], base.get(k, {})
        rk = 'auc' if 'auc' in f else 'spearman'
        out[k] = {'with': f['dev_skill'], 'without': b.get('dev_skill'), 'rank_with': f.get(rk), 'rank_without': b.get(rk), 'rank': rk}
    return out


# ── 정답을 심은 가상 데이터: 적산 (서서히 쌓이는 손상) · 문장 (메모 속 징후)
def synthetic_accum(effect: float, seed: int, n=3000):
    """공구를 바꾼 뒤 가공 수가 쌓일수록 불량이 늘어난다 — 사용 시간 칼럼은 없고 «공구 번호» 만 있다 (모델이 적산을 스스로 만들어야 함)"""
    r = np.random.default_rng(seed)
    tool, life, rows = 0, 0, []
    t0 = pd.Timestamp('2026-02-01')
    for i in range(n):
        if life >= r.integers(60, 140):
            tool += 1; life = 0
        life += 1
        rows.append({'cut_time': str(t0 + pd.Timedelta(minutes=5 * i)), 'tool_no': f'T{tool:03d}', 'spindle_current [A]': 12 + r.normal(0, 0.4),
                     'coolant_temp [°C]': 24 + r.normal(0, 0.5), 'feed_speed [rpm]': 1500 + r.normal(0, 15), '_life': life})
    df = pd.DataFrame(rows)
    p = 0.05 * np.exp(np.log(effect) * (df['_life'] - 70) / 30)
    df['quality_ng'] = (r.random(n) < np.clip(p, 0, 0.9)).astype(int)
    return [Table('synAcc', '가상 · 공구 적산', df.drop(columns=['_life']), {}, {}, '', {}, 'row')]


def synthetic_dose(effect: float, seed: int, n=3000):
    """온도가 규격 안에서 조금씩 벗어나는 일이 «이어지면» 불량 — 한 번의 값은 작아서 순간값으로는 잘 안 보임"""
    r = np.random.default_rng(seed)
    drift, dose, rows = 0.0, 0.0, []
    t0 = pd.Timestamp('2026-02-01')
    for i in range(n):
        drift = 0.97 * drift + r.normal(0, 0.25)            # 천천히 움직이는 치우침
        x = drift + r.normal(0, 1.0)
        dose = max(0.0, dose + drift - 0.5)
        rows.append({'run_time': str(t0 + pd.Timedelta(minutes=5 * i)), 'oven_temp [°C]': 180 + 2 * x, 'line_speed [rpm]': 40 + r.normal(0, 1), '_dose': dose})
    df = pd.DataFrame(rows)
    p = 0.04 * np.exp(np.log(effect) * np.clip(df['_dose'] / 3, 0, 4))
    df['defect'] = (r.random(n) < np.clip(p, 0, 0.9)).astype(int)
    return [Table('synDose', '가상 · 누적 이탈', df.drop(columns=['_dose']), {}, {}, '', {}, 'row')]


MEMO_NORMAL = ['정상 가동', '특이사항 없음', '교대 인수 완료', '자재 보충', '청소 완료', '조건 확인함', '샘플 측정 양호', '이상 없음', '라인 순조로움']
MEMO_SIGN = ['주축 쪽 소리가 조금 큼', '가공 중 떨림 느낌', '칩 색이 평소와 다름', '표면 광택 약간 탁함', '쿨런트 냄새 남']


def synthetic_text(signal: float, seed: int, n=2500):
    """작업자 메모에만 남는 징후 — 숫자 칼럼에는 신호가 없다. signal=0 이면 메모와 불량이 무관 (오경보 시험)"""
    r = np.random.default_rng(seed)
    t0 = pd.Timestamp('2026-02-01')
    risk = (r.random(n) < 0.15)
    rows = []
    for i in range(n):
        sign = r.random() < (0.08 + signal * 0.6 if risk[i] else 0.08)
        memo = (r.choice(MEMO_SIGN) if sign else r.choice(MEMO_NORMAL)) + ' ' + r.choice(['', '— 확인 요망', '(야간)', '오전 작업'])
        rows.append({'work_time': str(t0 + pd.Timedelta(minutes=5 * i)), 'spindle_load [%]': 45 + r.normal(0, 3), 'feed_speed [rpm]': 1500 + r.normal(0, 15), 'operator_memo': memo})
    df = pd.DataFrame(rows)
    p = np.where(risk, 0.25, 0.03)
    df['defect'] = (r.random(n) < p).astype(int)
    return [Table('synText', '가상 · 작업 메모', df, {'operator_memo': {'label': '작업자 메모'}}, {}, '', {}, 'row')]


def channel_tests(seeds=range(3)):
    """적산 · 누적 이탈 · 문장 — 각 신호를 심었을 때 (1) 그 슬롯이 찾아지는가 (2) 그 묶음을 빼면 성능이 떨어지는가, 신호가 없을 때는 오경보가 없는가"""
    cases = [('적산', 'acc:count', synthetic_accum, (1.0, 2.0)), ('적산', 'acc:drift', synthetic_dose, (1.0, 2.0)), ('텍스트', 'text:risk', synthetic_text, (0.0, 1.0))]
    out = []
    for grp, slot, gen, levels in cases:
        for lv in levels:
            det, gain, auc_w, auc_wo = 0, [], [], []
            for sd in seeds:
                E = build(gen(lv, sd)); k = list(E)[0]
                M, rw, _, _ = prequential(E, n_batches=10, drift_on=False)
                pe = {x['slot']: x for x in M.process_effects(k)['effects']}
                det += int(pe[slot]['lo'] > 1.0)
                _, ro, _, _ = prequential(ablate(build(gen(lv, sd)), [grp]), n_batches=10, drift_on=False)
                gain.append(rw[k]['dev_skill'] - ro[k]['dev_skill']); auc_w.append(rw[k]['auc']); auc_wo.append(ro[k]['auc'])
            n_s = len(list(seeds))
            out.append({'group': grp, 'slot': slot, 'signal': lv, 'detected': det / n_s, 'skill_gain': float(np.mean(gain)),
                        'auc_with': float(np.mean(auc_w)), 'auc_without': float(np.mean(auc_wo)), 'seeds': n_s})
    return out


def is_binary(e) -> bool:
    return bool(e.n.max() <= 1 and np.all((e.y == 0) | (e.y == 1)))


def poisson_dev(y, mu, binary: bool = False):
    """예측 오차(이탈도): 개수는 포아송, 0/1 은 베르누이 (p = 1 − e^−μ)"""
    return CumulativeModel._dev(np.asarray(y, float), np.asarray(mu, float), binary)


def rate_to_mu(r, binary):
    return -np.log(1 - min(r, 0.999)) if binary else r


def spearman(a, b) -> float:
    """순위 상관 (scipy 없이 — 순위를 매긴 뒤 피어슨 상관)"""
    ra, rb = pd.Series(np.asarray(a, float)).rank().to_numpy(), pd.Series(np.asarray(b, float)).rank().to_numpy()
    if len(ra) < 3 or np.std(ra) == 0 or np.std(rb) == 0:
        return float('nan')
    return float(np.corrcoef(ra, rb)[0, 1])


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
                base[k][ix] = rate_to_mu(r0, is_binary(e)) * n
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
        bi = is_binary(e)
        d1, d0 = poisson_dev(y, mu, bi), poisson_dev(y, mb, bi)
        dg = poisson_dev(y, gpred[k][ok], bi)
        r = {'n_eval': int(ok.sum()), 'events': float(y.sum()), 'dev_skill': float(1 - d1 / d0) if d0 > 0 else float('nan'),
             'gated_skill': float(1 - dg / d0) if d0 > 0 else float('nan'), 'gate': M.gate_weight(k)}
        if e.n.max() <= 1:
            r['auc'] = auc(mu / e.n[ok], y)
        else:
            r['spearman'] = spearman(mu / e.n[ok], y / e.n[ok])
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
    out.append({'frac': 0.0, 'n_train': 0, 'events_train': 0.0, 'zero_shot_auc': auc(z0, e.y[te]) if e.n.max() <= 1 else spearman(z0, e.y[te] / e.n[te])})
    for f in fracs:
        k = max(5, int(N * f))
        if k > cut:
            break
        tr = np.arange(k)
        r0 = (e.y[tr].sum() + 0.5) / (e.n[tr].sum() + 1)
        bi = is_binary(e)
        d0 = poisson_dev(e.y[te], rate_to_mu(r0, bi) * e.n[te], bi)
        row = {'frac': f, 'n_train': k, 'events_train': float(e.y[tr].sum())}
        for name, M in (('transfer', CumulativeModel.from_dict(snap)), ('fresh', new_model(E))):
            M.add_process(target, r0)
            for ix in chunks(k, max(1, min(5, k // 5))):
                M.update([(target, e.Xs.to_numpy()[ix], e.y[ix], e.n[ix])])
            mu, _ = M.predict(target, e.Xs.to_numpy()[te], e.n[te])
            row[name] = float(1 - poisson_dev(e.y[te], mu, bi) / d0) if d0 > 0 else float('nan')
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
