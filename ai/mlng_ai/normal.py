"""정상 기준 모드 — 불량(정답) 칼럼이 없는 데이터에서 «평소 범위» 를 배우고, 벗어난 정도를 이상 점수로 낸다.

  NormalBaseline.fit(df, cols, group)   기준 기간 데이터로 칼럼별 중앙값 · 산포(MAD) 를 그룹마다 학습
  .score(df)                            이상 점수 · 가장 크게 벗어난 칼럼 · 기준 유무
  임계값                                 기준 기간 점수의 q 분위 (기본 99% → 기준 기간 알림 1%)

정답이 없으니 «불량을 맞혔다» 는 주장은 하지 않는다. 평가는
  · 오경보율: 학습에 쓰지 않은 이후 기간의 정상 데이터에서 알림 비율
  · (정답이 있는 데이터로) 방법 검증: 점수가 실제 마모 · 불량과 함께 오르는가
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import sense_table


def condition_columns(df: pd.DataFrame, meta: dict | None = None) -> list[str]:
    """칼럼 해석으로 «조건» 칼럼만 고른다 (식별자 · 시각 · 결과 · 값이 하나뿐인 칼럼 제외)"""
    S = sense_table(df, meta or {})
    return [c for c, s in S.items() if s.role == 'param' and pd.api.types.is_numeric_dtype(df[c]) and df[c].nunique(dropna=True) > 2]


class NormalBaseline:
    def __init__(self, q: float = 0.99, min_rows: int = 20, zcap: float = 10.0):
        self.q, self.min_rows, self.zcap = q, min_rows, zcap
        self.cols: list[str] = []
        self.ref: dict = {}          # 그룹 → (중앙값, 산포, 임계값, 행 수)

    @staticmethod
    def _key(df, group):
        if not group:
            return pd.Series(['*'] * len(df), index=df.index)
        g = group if isinstance(group, list) else [group]
        return df[g].astype(str).agg('|'.join, axis=1)

    def _z(self, X, med, sc):
        return np.clip((X - med) / sc, -self.zcap, self.zcap)

    def _score_z(self, Z):
        Z = np.nan_to_num(Z)
        return np.sqrt((Z * Z).mean(1))

    def fit(self, df: pd.DataFrame, cols: list[str], group=None):
        self.cols, self.group = list(cols), group
        X = df[self.cols].to_numpy(float)
        keys = self._key(df, group).to_numpy()
        for k in ['*'] + sorted(set(keys)):
            m = np.ones(len(df), bool) if k == '*' else keys == k
            if m.sum() < self.min_rows:
                continue
            Xk = X[m]
            med = np.nanmedian(Xk, 0)
            mad = 1.4826 * np.nanmedian(np.abs(Xk - med), 0)
            sd = np.nanstd(Xk, 0)
            sc = np.where(mad > 1e-9, mad, np.where(sd > 1e-9, sd, 1.0))
            s = self._score_z(self._z(Xk, med, sc))
            self.ref[k] = (med, sc, float(np.quantile(s, self.q)), int(m.sum()))
        return self

    def score(self, df: pd.DataFrame) -> pd.DataFrame:
        X = df[self.cols].to_numpy(float)
        keys = self._key(df, self.group).to_numpy()
        out = np.full(len(df), np.nan)
        thr = np.full(len(df), np.nan)
        top = np.array([''] * len(df), dtype=object)
        own = np.zeros(len(df), bool)
        for k in set(keys):
            m = keys == k
            ref = self.ref.get(k)
            own[m] = ref is not None
            ref = ref or self.ref.get('*')
            if ref is None:
                continue
            med, sc, th, _ = ref
            Z = self._z(X[m], med, sc)
            out[m] = self._score_z(Z)
            thr[m] = th
            top[m] = np.array(self.cols, dtype=object)[np.nanargmax(np.abs(np.nan_to_num(Z)), 1)]
        return pd.DataFrame({'score': out, 'threshold': thr, 'alert': out > thr, 'top': top, 'own_baseline': own}, index=df.index)

    def to_dict(self) -> dict:
        return {'q': self.q, 'cols': self.cols, 'group': self.group,
                'ref': {k: {'median': list(map(float, v[0])), 'scale': list(map(float, v[1])), 'threshold': v[2], 'rows': v[3]} for k, v in self.ref.items()}}
