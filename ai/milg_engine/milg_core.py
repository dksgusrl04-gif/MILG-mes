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
from mlng_ai.normal import NormalBaseline  # noqa: E402

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
        for attr in ('median_','columns_'):
            if hasattr(self,attr):delattr(self,attr)
        X = self._frame(X)
        self.columns_ = list(X.columns)
        self.median_ = X.median().fillna(0.0).to_dict()
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


class MilgNormalScorer:
    """정답이 없을 때 — MILG 엔진의 «정상 기준 모드»(mlng_ai.normal.NormalBaseline) 를 이상 탐지 시나리오의 점수 후보로 연결한다.

    맥락(같은 공구 · 같은 용접 위치 …)마다 기준 구간의 중앙값 · 산포(MAD) 를 배우고, 특성 전체가 벗어난 정도의 RMS 를 점수로 낸다.
    시나리오 엔진 기본 방식(희소도 상위 k개 평균)이 «몇 개 특성이 크게» 벗어난 것에 민감하다면, 이쪽은 «여러 특성이 조금씩 함께» 벗어난 것에 민감하다.
    기준이 부족한 맥락은 전체 기준으로 비교한다. 임계값(WARN · NG 분위수)은 시나리오 엔진이 같은 방식으로 정한다.
    """

    def __init__(self, min_rows: int = 10, zcap: float = 10.0):
        self.min_rows, self.zcap = min_rows, zcap

    @staticmethod
    def _frame(X, ctx) -> pd.DataFrame:
        d = pd.DataFrame(np.asarray(X, float), columns=[str(c) for c in X.columns]) if isinstance(X, pd.DataFrame) else pd.DataFrame(np.asarray(X, float))
        d.columns = [str(c) for c in d.columns]
        d['__ctx'] = np.asarray(ctx, dtype=object).astype(str)
        return d

    def fit(self, X, ctx):
        d = self._frame(X, ctx)
        self.cols_ = [c for c in d.columns if c != '__ctx' and d[c].notna().any() and d[c].nunique(dropna=True) > 1]
        if not self.cols_:raise ValueError('No usable normal-reference sensors')
        self.base_ = NormalBaseline(min_rows=max(2, self.min_rows), zcap=self.zcap).fit(d, self.cols_, '__ctx')
        if '*' not in self.base_.ref:                      # 기준 구간 전체가 min_rows 보다 적어도 전체 기준은 둔다
            self.base_.min_rows = 2
            self.base_.fit(d, self.cols_, '__ctx')
        return self

    def score(self, X, ctx):
        """→ (점수, 가장 크게 벗어난 특성)"""
        r = self.base_.score(self._frame(X, ctx))
        valid=np.isfinite(self._frame(X,ctx)[self.cols_].to_numpy(float)).all(axis=1)
        scores=r['score'].to_numpy(float,copy=True);top=r['top'].to_numpy(dtype=object,copy=True)
        scores[~valid]=np.nan;top[~valid]='sensor_missing'
        return scores,top
