"""공정별 판정기 (부스팅) — 같은 센서 구성 안에서는 MILG 슬롯 선형 모델보다 정확하다 (reports/benchmark.md).

  역할 나눔
    MILG 엔진   칼럼 해석 · 공통 슬롯 · 새 설비 출발점(transfer_model) · 원인 후보 설명
    판정기      그 공정 · 그 공장 칼럼 그대로 + 공구 교체 후 처음 N회 대비 변화 → 그래디언트 부스팅
                (MCT 공구 단위 AUC 0.93~0.99, 다른 설비로 이전 0.97 — 엔진 0.88~0.90)

  저장: JSON 메타(칼럼 · 기준 · 임계값 · 성능) + joblib 모델 파일. 모델 파일은 우리가 학습해 저장한 것만 읽는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REL_N = 10


def rel_features(X: pd.DataFrame, entity: np.ndarray, n: int = REL_N) -> pd.DataFrame:
    """공구(entity)마다 처음 n회 평균 대비 log 비율 — 그 공구의 과거만 사용"""
    R = {}
    for c in X.columns:
        v = X[c].abs()
        ref = v.groupby(entity).transform(lambda s: s.iloc[:n].mean())
        R[f'rel_{c}'] = np.log((v + 1e-9) / (ref + 1e-9))
    return pd.DataFrame(R, index=X.index)


class ProcessBooster:
    def __init__(self, cols: list[str] | None = None, rel: bool = True, fa: float = 0.05):
        self.cols, self.rel, self.fa = cols or [], rel, fa
        self.model = None
        self.threshold = None
        self.medians: dict = {}

    def _X(self, df: pd.DataFrame, entity) -> pd.DataFrame:
        X = df.reindex(columns=self.cols).apply(pd.to_numeric, errors='coerce').astype(float)
        X = X.fillna(pd.Series(self.medians)).fillna(0.0)
        if self.rel:
            X = pd.concat([X, rel_features(X, np.asarray(entity))], axis=1)
        return X

    def fit(self, df: pd.DataFrame, y, entity, cols: list[str]):
        from sklearn.ensemble import HistGradientBoostingClassifier
        self.cols = list(cols)
        self.medians = df[self.cols].apply(pd.to_numeric, errors='coerce').median().fillna(0.0).to_dict()
        y = np.asarray(y, float)
        ok = ~np.isnan(y)
        X = self._X(df, entity)
        self.model = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, random_state=0).fit(X[ok], y[ok].astype(int))
        s = self.model.predict_proba(X[ok])[:, 1]
        neg = s[y[ok] == 0]
        self.threshold = float(np.quantile(neg, 1 - self.fa)) if len(neg) else 0.5     # 정상 행 오경보 fa
        return self

    def score(self, df: pd.DataFrame, entity) -> pd.DataFrame:
        p = self.model.predict_proba(self._X(df, entity))[:, 1]
        return pd.DataFrame({'p': p, 'alert': p > self.threshold}, index=df.index)

    def save(self, path: Path, info: dict | None = None):
        import joblib
        path = Path(path)
        joblib.dump(self.model, path.with_suffix('.joblib'))
        meta = {'kind': 'process_booster', 'cols': self.cols, 'rel': self.rel, 'rel_n': REL_N, 'fa': self.fa, 'threshold': self.threshold,
                'medians': self.medians, 'model_file': path.with_suffix('.joblib').name, **(info or {})}
        path.with_suffix('.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=float), encoding='utf-8')

    @classmethod
    def load(cls, path: Path) -> 'ProcessBooster':
        import joblib
        path = Path(path)
        m = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        b = cls(m['cols'], m['rel'], m['fa'])
        b.threshold, b.medians = m['threshold'], m['medians']
        b.model = joblib.load(path.parent / m['model_file'])
        return b
