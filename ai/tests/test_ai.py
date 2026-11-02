"""공정 통합 AI 시험 — cd ai && python -m pytest -q  (공개 데이터가 필요한 시험은 python -m mlng_ai fetch 뒤에만)"""
import tempfile
import warnings

import numpy as np
import pandas as pd
import pytest

warnings.filterwarnings('ignore')
from mlng_ai.datasets import PUB, mlng_tables, public_tables  # noqa: E402
from mlng_ai.engine import build, injection, prequential, synthetic_tables  # noqa: E402
from mlng_ai.features import SLOTS  # noqa: E402
from mlng_ai.holdout import evaluate as holdout  # noqa: E402
from mlng_ai.model import CumulativeModel  # noqa: E402
from mlng_ai.schema import sense, sense_table  # noqa: E402
from mlng_ai.service import Service  # noqa: E402

HAS_PUB = (PUB / 'ai4i2020.csv').exists()


def test_units_and_polarity():
    s = sense('Air temperature [K]', pd.Series(np.random.default_rng(0).normal(300, 2, 100)))
    assert (s.role, s.quantity) == ('param', 'temperature')
    o = sense('pass_ok', pd.Series([1] * 90 + [0] * 10), label='검사 판정 (합격 = 1)')
    assert o.role == 'outcome' and o.polarity == -1


def test_outcome_detail_not_used_as_input():
    df = pd.DataFrame({'Machine failure': [0, 1] * 50, 'TWF': [0, 1] * 50, 'Torque [Nm]': np.arange(100.0)})
    S = sense_table(df)
    assert S['Machine failure'].role == 'outcome' and S['TWF'].role == 'outcome_detail'


def test_holdout_names_accuracy():
    assert holdout()['accuracy'] >= 0.85          # 처음 보는 칼럼 이름 72개


def test_mlng_tables_encode():
    E = build(mlng_tables())
    assert set(E) == {'heat', 'press', 'weld', 'mach', 'insp'}
    assert all(list(e.Xs.columns) == SLOTS for e in E.values())
    assert E['insp'].X['up:has'].mean() == 1.0      # 검사 부품 → 앞 공정(MCT) 연결


def test_cumulative_update_and_roundtrip():
    r = np.random.default_rng(1)
    X = r.normal(size=(400, len(SLOTS)))
    y = r.poisson(np.exp(-3 + 0.8 * X[:, 0]))
    M = CumulativeModel(SLOTS)
    M.add_process('p', y.mean())
    for ix in np.array_split(np.arange(400), 8):
        M.update([('p', X[ix], y[ix], np.ones(len(ix)))])
    eff = M.process_effects('p')['effects'][0]
    assert eff['lo'] > 1.5                              # 심은 효과 exp(0.8)=2.2 를 찾음
    M2 = CumulativeModel.from_dict(M.to_dict())
    assert np.allclose(M2.predict('p', X[:5], np.ones(5))[0], M.predict('p', X[:5], np.ones(5))[0])


def test_injection_no_false_alarm_and_detects_strong():
    res = {r['effect']: r for r in injection(effects=(1.0, 3.0), seeds=range(2))}
    assert res[1.0]['detected'] == 0 and res[3.0]['detected'] >= 0.5


def test_service_new_process():
    d = tempfile.mkdtemp()
    S = Service(d)
    tA, _ = synthetic_tables(1.0, 3, n_up=80, per=1)
    a = S.update('line_a', tA.df.iloc[:40])
    b = S.update('line_a', tA.df.iloc[40:])
    assert a['scored'] is False and b['scored'] is True and b['version'] == a['version'] + 1
    with pytest.raises(ValueError):
        S.update('Bad Key!', tA.df)


@pytest.mark.skipif(not HAS_PUB, reason='공개 데이터 없음 (python -m mlng_ai fetch)')
def test_public_prequential_beats_baseline():
    E = build(mlng_tables() + public_tables())
    _, res, _, _ = prequential(E, keys=['ai4i', 'insp'])
    assert res['ai4i']['gated_skill'] > 0.1 and res['ai4i']['auc'] > 0.75
