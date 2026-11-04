"""공장별 모델 운영 · 평가 기준"""
import tempfile

import numpy as np
import pandas as pd

from mlng_ai.evaluate import alert_metrics
from mlng_ai.service import Service


def test_alert_metrics():
    y = np.array([1, 0, 0, 0, 0, 0, 0, 0, 0, 1])
    s = np.array([9, 1, 1, 1, 1, 1, 1, 1, 1, 0])
    m = alert_metrics(s, y, budget=0.1)
    assert m['alerts'] == 1 and m['capture'] == 0.5 and m['precision'] == 1.0 and m['lift'] == 5.0


def _rows(n, seed):
    r = np.random.default_rng(seed)
    t = r.normal(850, 10, n)
    return pd.DataFrame({'측정시각': pd.date_range('2026-01-01', periods=n, freq='h').astype(str), '소입 온도': t,
                         '판정': np.where(r.random(n) < 0.02 + 0.3 * (t > 860), 'NG', 'OK')})


def test_candidate_promote_rollback():
    S = Service(tempfile.mkdtemp())
    S.ingest('line_a', _rows(600, 1))
    before = S.M.to_dict()
    c = S.train_candidate('line_a')
    assert c['eval']['active'] is None and c['recommend'] == 'promote'
    assert S.M.to_dict() == before                    # 후보 학습은 운영 모델을 바꾸지 않는다
    assert S.promote(c['version'], 'qa')['active'] == c['version']
    assert 'line_a' in S.M.procs
    assert S.rollback('qa')['active'] == 'base' and 'line_a' not in S.M.procs
    assert [h['action'] for h in S.registry()['history']] == ['promote', 'rollback']
