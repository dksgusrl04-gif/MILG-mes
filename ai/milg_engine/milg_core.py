"""MILG 판정 엔진(mlng_ai · 누적 베이지안 모델)을 시나리오 엔진의 «모델 후보» 로 연결한다.

시나리오에서 `model.candidates` 에 `milg` 를 적으면 logistic · random_forest · hgb 와 같은 분할 · 같은 지표로 나란히 비교된다.
  입력   시나리오가 고른 특성 그대로 → 학습 구간 평균 · 표준편차로 표준화(±5 로 자름), two_sided 면 |z| · z²/5 도 더한다
         («너무 크거나 작으면 불량» — 적정 범위가 있는 공정)
  모델   mlng_ai.model.CumulativeModel (외부 모델 파일 없음 · 부스팅 없음) — mlng_ai.local_inputs 의 실행 설정 그대로
  출력   불량 확률 = 1 − exp(−기대 사건 수) · 특성별 영향(원인 후보 순위) · 한 건의 원인 후보(explain)
이진 판정(task: binary) 전용.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))    # ai/ — mlng_ai 가 있는 곳
from mlng_ai.local_inputs import LocalScaler, fit_engine  # noqa: E402

KEY = 'scenario'


class MilgEngineClassifier(ClassifierMixin, BaseEstimator):
    def __init__(self, two_sided: bool = True, clip: float = 5.0):
        self.two_sided, self.clip = two_sided, clip

    def _frame(self, X) -> pd.DataFrame:
        X = X if isinstance(X, pd.DataFrame) else pd.DataFrame(np.asarray(X), columns=getattr(self, 'columns_', None))
        X = X.apply(pd.to_numeric, errors='coerce').astype(float)
        X = X.mask(~np.isfinite(X))
        return X.fillna(self.median_) if hasattr(self, 'median_') else X

    def fit(self, X, y):
        X = self._frame(X)
        self.columns_ = list(X.columns)
        self.median_ = X.median().fillna(0.0)
        X = X.fillna(self.median_)
        self.scaler_ = LocalScaler(self.two_sided, self.clip).fit(X)
        self.model_ = fit_engine(KEY, self.scaler_.transform(X), np.asarray(y, float), self.scaler_.names)
        self.classes_ = np.array([0, 1])
        beta = {e['slot']: abs(e['beta']) for e in self.model_.process_effects(KEY)['effects']}
        imp = {c: sum(v for s, v in beta.items() if s.split('|')[0] == f'L:{c}') for c in self.columns_}
        self.feature_importances_ = np.array([imp[c] for c in self.columns_])      # 특성별 영향 크기 (원인 후보 순위)
        return self

    def _mu(self, X) -> np.ndarray:
        A = self.scaler_.transform(self._frame(X)[self.columns_])
        return self.model_.predict(KEY, A, np.ones(len(A)))[0]

    def predict_proba(self, X) -> np.ndarray:
        p = np.clip(1 - np.exp(-self._mu(X)), 0.0, 1.0)
        return np.column_stack([1 - p, p])

    def predict(self, X) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)

    def explain(self, X, top: int = 3) -> list[list[dict]]:
        """행마다 불량 쪽으로 민 특성 상위 top 개 — [{feature, z, contrib}]"""
        A = self.scaler_.transform(self._frame(X)[self.columns_])
        out = []
        for a in A:
            agg: dict = {}
            for r in self.model_.explain(KEY, a):
                c = r['slot'].split('|')[0][2:]
                agg[c] = agg.get(c, 0.0) + r['contrib']
            z = dict(zip(self.scaler_.cols, a[:len(self.scaler_.cols)]))
            out.append([{'feature': c, 'z': round(float(z.get(c, 0.0)), 2), 'contrib': round(float(v), 3)} for c, v in sorted(agg.items(), key=lambda kv: -kv[1])[:top] if v > 0])
        return out
