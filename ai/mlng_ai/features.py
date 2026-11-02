"""공정 공통 표현 — 공정마다 다른 칼럼을 «의미 슬롯» 의 같은 모양 벡터로 바꾼다.

슬롯 (모든 공정이 같은 자리)
  q:<물리량>   그 물리량 칼럼들이 «이 공정의 평소 값» 에서 얼마나 벗어났나 (|z| 최댓값)
  spread       변동(표준편차 · 산포) 칼럼들이 평소보다 얼마나 커졌나
  spec         규격(설정값 ± 허용폭) 대비 벗어난 정도 (규격이 있는 칼럼만)
  event        교체 · 급변 같은 0/1 사건이 켜졌나
  all_max      모든 조건 칼럼 중 가장 크게 벗어난 정도 · n_out: 크게 벗어난 칼럼 비율
  cat_rare     작업자 · 자재 · 설비 같은 범주 값이 지금까지 드물었던 것인가
  load         LOT 수량(노출)이 평소보다 큰가 · missing: 빈 값 비율
  up:*         LOT 추적으로 이어진 앞 공정 LOT 의 같은 슬롯 (공정 간 원인 추적)

«누적형»: 평소 값(평균 · 표준편차 · 범주 빈도)은 그 행 «이전까지» 쌓인 데이터만으로 계산한다.
그래서 새 데이터가 들어올 때마다 기준이 자연스럽게 갱신되고, 미래 정보가 섞이지 않는다 (시간순 검증과 같은 조건).
마지막에 슬롯마다 다시 «이 공정에서 평소보다 몇 σ» 로 맞춰서, 공정이 달라도 같은 크기로 비교된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .datasets import Table
from .lexicon import QUANTITY
from .schema import ColumnSense, leakage_guard, sense_table

QS = list(QUANTITY) + ['unknown']
BASE_SLOTS = [f'q:{q}' for q in QS] + ['spread', 'spec', 'event', 'all_max', 'n_out', 'cat_rare', 'load', 'missing']
UP_SLOTS = ['up:all_max', 'up:n_out', 'up:spec', 'up:spread', 'up:event', 'up:q:temperature', 'up:q:pressure', 'up:q:current', 'up:q:wear', 'up:q:vibration', 'up:q:time']
SLOTS = BASE_SLOTS + UP_SLOTS + ['up:has']
SLOT_KO = {'spread': '변동(산포) 증가', 'spec': '규격 대비 이탈', 'event': '교체 · 급변 사건', 'all_max': '가장 크게 벗어난 조건', 'n_out': '벗어난 조건 수',
           'cat_rare': '드문 작업자 · 자재 · 설비', 'load': '수량(장입량) 과다', 'missing': '빈 값', 'up:has': '앞 공정 연결 있음'}
for q, (ko, _) in QUANTITY.items():
    SLOT_KO[f'q:{q}'] = f'{ko} 이탈'
SLOT_KO['q:unknown'] = '이름 없는 센서 이탈'
for s in UP_SLOTS:
    SLOT_KO[s] = '앞 공정 · ' + SLOT_KO[s[3:]]
MIN_HIST = 20         # 이보다 적게 쌓이면 «평소» 를 모른다 → 0 (표준편차 추정이 흔들리지 않을 만큼)


def expanding_z(x: np.ndarray) -> np.ndarray:
    """그 행 이전까지의 평균 · 표준편차로 본 z (빈 값은 0)"""
    v = np.where(np.isfinite(x), x, 0.0)
    m = np.isfinite(x).astype(float)
    n = np.concatenate([[0], np.cumsum(m)[:-1]])
    s1 = np.concatenate([[0], np.cumsum(v)[:-1]])
    s2 = np.concatenate([[0], np.cumsum(v * v)[:-1]])
    with np.errstate(invalid='ignore', divide='ignore'):
        mu = s1 / n
        sd = np.sqrt(np.maximum(s2 / n - mu * mu, 0) * n / np.maximum(n - 1, 1))
        z = (x - mu) / np.where(sd > 1e-12, sd, np.nan)
    z[(n < MIN_HIST) | ~np.isfinite(z)] = 0.0
    return np.clip(z, -6, 6)


def expanding_rarity(vals: pd.Series) -> np.ndarray:
    """그 행 이전까지 이 범주 값이 얼마나 드물었나: −log((이전 등장 + 1)/(이전 행 + 2)) 를 0~1 로"""
    cnt, out = {}, np.zeros(len(vals))
    for i, v in enumerate(vals.astype(str).to_numpy()):
        if i >= MIN_HIST:
            out[i] = -np.log((cnt.get(v, 0) + 1) / (i + 2)) / np.log(i + 2)
        cnt[v] = cnt.get(v, 0) + 1
    return out


@dataclass
class Encoded:
    table: Table
    senses: dict
    X: pd.DataFrame            # 슬롯 값 (공정 안 표준화 전)
    y: np.ndarray              # 사건 수 (불량 수 · 0/1)
    n: np.ndarray              # 노출 (수량 · 1)
    lot: np.ndarray            # LOT 키 (앞 공정 연결)
    detail: dict = field(default_factory=dict)   # 행마다 슬롯을 만든 칼럼 (설명용)
    leak: list = field(default_factory=list)


def outcome_vector(s: pd.Series, cs: ColumnSense) -> np.ndarray:
    v = pd.to_numeric(s, errors='coerce')
    if v.notna().mean() > 0.95:
        u = np.unique(v.dropna())
        if len(u) == 2:                                   # 이진: 큰 값이 사건 (부호가 반대면 작은 값)
            ev = (v == (u.min() if cs.polarity < 0 else u.max())).astype(float).to_numpy()
        else:
            ev = v.fillna(0).clip(lower=0).to_numpy(float)   # 개수
    else:                                                 # 글자: «좋은 쪽» 단어가 없는 값이 사건 (band / noband)
        t = s.astype(str).str.lower()
        good = t.str.contains('no ?band|noband|ok|pass|good|normal|합격|양품', regex=True)
        ev = (~good).astype(float).to_numpy()
    return ev


def encode(t: Table, overrides: dict | None = None, llm: bool = False) -> Encoded:
    df = t.df
    S = sense_table(df, t.meta, overrides=overrides, llm=llm, process=t.process)
    oc = [c for c, s in S.items() if s.role == 'outcome']
    if not oc:
        raise ValueError(f'{t.key}: 결과(불량) 칼럼을 찾지 못했습니다 — overrides 로 지정하세요')
    y = outcome_vector(df[oc[0]], S[oc[0]])
    ex = [c for c, s in S.items() if s.role == 'exposure']
    n = pd.to_numeric(df[ex[0]], errors='coerce').fillna(1).clip(lower=1).to_numpy(float) if ex else np.ones(len(df))
    if not ex and y.max() > 1:
        n = np.maximum(n, y)
    leak = leakage_guard(df, S, (y > 0).astype(float) if y.max() <= 1 else (y / n > np.median(y / n)).astype(float))

    params = [c for c, s in S.items() if s.role == 'param']
    Z = {c: expanding_z(pd.to_numeric(df[c], errors='coerce').to_numpy(float)) for c in params if S[c].stat != 'event'}
    X = pd.DataFrame(0.0, index=df.index, columns=BASE_SLOTS)
    who = {}
    for q in QS:
        cs = [c for c in Z if S[c].quantity == q and S[c].stat == 'level']
        if cs:
            A = np.abs(np.column_stack([Z[c] for c in cs]))
            X[f'q:{q}'] = A.max(1); who[f'q:{q}'] = (cs, A.argmax(1))
    sp = [c for c in Z if S[c].stat == 'spread']
    if sp:
        A = np.column_stack([Z[c] for c in sp])                 # 변동은 «커질 때» 만
        X['spread'] = np.clip(A, 0, None).max(1); who['spread'] = (sp, A.argmax(1))
    if t.spec:
        cols, D = [], []
        for c, (spv, tol) in t.spec.items():
            if c in df and S.get(c) and S[c].role == 'param':
                spa = pd.to_numeric(df[spv], errors='coerce').to_numpy(float) if isinstance(spv, str) else np.full(len(df), float(spv))
                D.append(np.clip(np.abs(pd.to_numeric(df[c], errors='coerce').to_numpy(float) - spa) / float(tol), 0, 6)); cols.append(c)
        if D:
            A = np.nan_to_num(np.column_stack(D)); X['spec'] = A.max(1); who['spec'] = (cols, A.argmax(1))
    evc = [c for c in params if S[c].stat == 'event']
    if evc:
        A = np.column_stack([pd.to_numeric(df[c], errors='coerce').fillna(0).to_numpy(float) for c in evc]); X['event'] = A.max(1); who['event'] = (evc, A.argmax(1))
    if Z:
        A = np.abs(np.column_stack(list(Z.values())))
        X['all_max'] = A.max(1); X['n_out'] = (A > 2.5).mean(1); who['all_max'] = (list(Z), A.argmax(1))
        X['missing'] = np.column_stack([df[c].isna().to_numpy() for c in Z]).mean(1)
    cats = [c for c, s in S.items() if s.role == 'category']
    if cats:
        A = np.column_stack([expanding_rarity(df[c]) for c in cats]); X['cat_rare'] = A.max(1); who['cat_rare'] = (cats, A.argmax(1))
    if ex:
        X['load'] = np.clip(expanding_z(np.log(n)), 0, None)
    lot = df[t.lot_col].astype(str).to_numpy() if t.lot_col else np.array([''] * len(df))
    return Encoded(t, S, X, y, n, lot, who, leak)


def add_upstream(encs: dict[str, Encoded]) -> None:
    """LOT 추적: 앞 공정 LOT 의 슬롯을 «up:» 로 붙인다 (앞 LOT 이 여럿이면 최댓값 — 가장 나빴던 앞 공정)"""
    by_lot = {}
    for e in encs.values():
        if not e.table.lot_col:
            continue
        g = e.X.assign(_lot=e.lot).groupby('_lot').mean(numeric_only=True)
        for lot, row in g.iterrows():
            by_lot[lot] = (e.table.key, row)
    for e in encs.values():
        U = pd.DataFrame(0.0, index=e.X.index, columns=UP_SLOTS + ['up:has'])
        src = {}
        if e.table.parents:
            for i, lot in enumerate(e.lot):
                ps = [by_lot[p] for p in e.table.parents.get(lot, []) if p in by_lot]
                if not ps:
                    continue
                U.iat[i, U.columns.get_loc('up:has')] = 1.0
                for s in UP_SLOTS:
                    vals = [r[s[3:]] for _, r in ps if s[3:] in r.index]
                    if vals:
                        U.iat[i, U.columns.get_loc(s)] = float(np.max(vals))
                src[i] = sorted({k for k, _ in ps})
        e.X = pd.concat([e.X[BASE_SLOTS], U], axis=1)
        e.detail['_up'] = src


def standardize(X: pd.DataFrame) -> pd.DataFrame:
    """슬롯을 공정 안에서 «평소보다 몇 σ» 로 (누적 · 이전 행 기준). 0/1 표시 슬롯은 그대로"""
    out = X.copy()
    for c in X.columns:
        if c == 'up:has':
            continue
        v = X[c].to_numpy(float)
        if np.nanstd(v) < 1e-12:
            out[c] = 0.0
        else:
            out[c] = expanding_z(v)
    return out
