"""공정 전용 입력 — MILG 엔진(CumulativeModel)에 그 공정 원본 칼럼을 슬롯으로 더한다 (부스팅 · 외부 모델 없음).

  왜: 공통 슬롯은 여러 칼럼을 «물리량별 대표값» 으로 묶어 정보가 준다. 같은 센서 구성 안에서는
      원본 칼럼(+ 공구 교체 후 변화)을 그대로 넣으면 범용 모델과 비슷해진다 (reports/benchmark.md · engine_plus).
  어떻게: 학습 묶음 평균 · 표준편차로 표준화(±5 로 자름) → 슬롯 이름 «L:칼럼» 으로 엔진 설계행렬 뒤에 붙인다.
          two_sided=True 면 |z| · z²/5 도 더한다 — «너무 크거나 작으면 불량» (압입 곡선처럼 적정 범위가 있는 공정).
  실행 설정: 사전 폭 δ 1.0 (공정 전용 효과를 덜 누름) · 한 번에 학습(배치 1) · 망각 없음 · 반복 50.
  주의: 다른 설비로 옮길 때는 원본 칼럼의 절대 수준이 달라 오히려 나빠진다 → 그때는 «교체 후 변화» 슬롯만 쓴다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .features import SLOTS
from .model import CumulativeModel

FIT = dict(tau=(0.3, 1.0), n_batches=1, lam=1.0, weight_cap=1e9, iters=50)


class LocalScaler:
    def __init__(self, two_sided: bool = False, clip: float = 5.0):
        self.two_sided, self.clip = two_sided, clip
        self.cols: list[str] = []

    def fit(self, X: pd.DataFrame):
        A = X.to_numpy(float)
        sd = np.nanstd(A, 0)
        keep = sd > 1e-9
        self.cols = [c for c, k in zip(X.columns, keep) if k]
        self.mu, self.sd = np.nanmean(A, 0)[keep], sd[keep]
        return self

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        Z = np.clip((X[self.cols].to_numpy(float) - self.mu) / self.sd, -self.clip, self.clip)
        Z = np.nan_to_num(Z)
        return np.column_stack([Z, np.abs(Z), Z * Z / self.clip]) if self.two_sided else Z

    @property
    def names(self) -> list[str]:
        n = [f'L:{c}' for c in self.cols]
        return n + [f'{x}|abs' for x in n] + [f'{x}|sq' for x in n] if self.two_sided else n


def fit_engine(key: str, X: np.ndarray, y: np.ndarray, slots: list[str], cfg: dict | None = None) -> CumulativeModel:
    c = {**FIT, **(cfg or {})}
    ok = ~np.isnan(y)
    X, y = X[ok], y[ok]
    M = CumulativeModel(slots, tau_beta=c['tau'][0], tau_delta=c['tau'][1])
    M.add_process(key, (y.sum() + 0.5) / (len(y) + 1))
    for idx in np.array_split(np.arange(len(y)), c['n_batches']):
        if len(idx):
            M.update([(key, X[idx], y[idx], np.ones(len(idx)))], lam=c['lam'], weight_cap=c['weight_cap'], iters=c['iters'])
    return M


def engine_with_local(key: str, X_slots_tr, y_tr, X_slots_te, local_tr: pd.DataFrame, local_te: pd.DataFrame, two_sided: bool = False):
    """공통 슬롯 + 공정 전용 입력으로 엔진 학습 → (학습 점수, 평가 점수, 모델, 변환기)"""
    sc = LocalScaler(two_sided).fit(local_tr)
    A = np.column_stack([X_slots_tr, sc.transform(local_tr)])
    B = np.column_stack([X_slots_te, sc.transform(local_te)])
    M = fit_engine(key, A, np.asarray(y_tr, float), list(SLOTS) + sc.names)
    ok = ~np.isnan(np.asarray(y_tr, float))
    return M.predict(key, A[ok], np.ones(ok.sum()))[0], M.predict(key, B, np.ones(len(B)))[0], M, sc


DROP = {'tool_id', 'curve_id', 'tool_ng', 'assembly_ng', 'join_ng', 'use_count', 'cut_minutes', 'contact_min',
        'part_ng', 'weld_ng', 'cycle_id', 'stroke_id', 'sample_id', 'window_id'}


def local_frame(df: pd.DataFrame, entity: str | None = 'tool_id', rel_n: int = 10) -> pd.DataFrame:
    """공정 전용 입력 후보 — 숫자 칼럼 그대로 (식별자 · 정답 · 사용량 제외) + 공구가 있으면 처음 rel_n 회 대비 log 변화"""
    X = df.drop(columns=[c for c in df.columns if c in DROP]).apply(pd.to_numeric, errors='coerce')
    X = X.loc[:, X.notna().mean() > 0.5].astype(float)
    X = X.fillna(X.median()).fillna(0.0)
    if entity and entity in df:
        g = df[entity].to_numpy()
        R = {f'rel_{c}': np.log((X[c].abs() + 1e-9) / (X[c].abs().groupby(g).transform(lambda s: s.iloc[:rel_n].mean()) + 1e-9)) for c in X.columns}
        X = pd.concat([X, pd.DataFrame(R, index=X.index)], axis=1)
    return X


def local_cv(t, groups=None, two_sided: bool = False) -> dict:
    """묶음(공구 · 작업일 등)을 통째로 빼며 «엔진 + 공정 전용 입력» 평가 — 정답 · 표준화는 엔진 인코딩 그대로"""
    from .engine import auc
    from .evaluate import alert_metrics
    from .mct import _fold_data
    G = t.df.tool_id.to_numpy() if groups is None else np.asarray(groups)
    L = local_frame(t.df, 'tool_id' if 'tool_id' in t.df else None)
    s_all, y_all, folds = [], [], []
    for g in sorted(pd.unique(G)):
        te = G == g
        Xa, ya, Xb, yb, _ = _fold_data(t, g, 'sensor', groups)
        if not np.nansum(ya) or not len(yb):
            continue
        _, s, _, _ = engine_with_local(t.key, Xa, ya, Xb, L[~te], L[te], two_sided)
        ok = ~np.isnan(yb)
        s_all += list(s[ok]); y_all += list(yb[ok])
        folds.append({'tool': g, 'auc': auc(s[ok], yb[ok]) if 0 < yb[ok].sum() < ok.sum() else None})
    y = np.array(y_all)
    return {'auc': auc(np.array(s_all), y), **alert_metrics(np.array(s_all), y), 'folds': folds}


def train_and_save(t, path, info: dict | None = None, two_sided: bool = False):
    """전체 데이터로 «엔진 + 공정 전용 입력» 학습 → JSON 하나 (엔진 상태 + 공정 전용 입력 표준화 값). 외부 모델 파일 없음"""
    import json
    from pathlib import Path
    from .mct import _encode
    e, X = _encode(t, 'sensor')
    L = local_frame(t.df, 'tool_id' if 'tool_id' in t.df else None)
    sc = LocalScaler(two_sided).fit(L)
    M = fit_engine(t.key, np.column_stack([X, sc.transform(L)]), np.asarray(e.y, float), list(SLOTS) + sc.names)
    d = {'kind': 'milg_engine_local', 'process': t.key, 'model': M.to_dict(), 'fit': FIT,
         'local': {'cols': sc.cols, 'mu': sc.mu.tolist(), 'sd': sc.sd.tolist(), 'two_sided': two_sided, 'rel_n': 10}, **(info or {})}
    Path(path).write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    return M, sc
