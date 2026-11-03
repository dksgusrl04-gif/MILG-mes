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
    assert eff['lo'] > 1.2                              # 심은 효과 exp(0.8)=2.2 를 찾음 (90% 하한)
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


def test_accum_and_text_detection():
    from mlng_ai.engine import synthetic_accum, synthetic_text
    E = build(synthetic_accum(2.0, 0))
    e = E['synAcc']
    assert e.info['entity'] == 'tool_no' and e.info['resets'] > 10 and (e.X['acc:count'] > 0).all()
    E = build(synthetic_text(1.0, 0))
    e = E['synText']
    assert e.senses['operator_memo'].role == 'text' and e.info['texts'] == ['operator_memo']
    assert (e.X['text:alarm'] > 0).any() and e.X['text:risk'].abs().sum() > 0


def test_accum_column_shape():
    x = pd.Series(np.concatenate([np.arange(0, 100, 2.0), np.arange(0, 80, 2.0), np.arange(0, 120, 2.0)]))
    assert sense('spindle_hours', x).stat == 'accum'          # 톱니 모양 → 적산


def test_text_channel_finds_memo_signal_without_false_alarm():
    from mlng_ai.engine import prequential, synthetic_text
    for sig, want in ((1.0, True), (0.0, False)):
        E = build(synthetic_text(sig, 1))
        M, _, _, _ = prequential(E, n_batches=10, drift_on=False)
        eff = {r['slot']: r for r in M.process_effects('synText')['effects']}['text:risk']
        assert (eff['lo'] > 1.0) == want


def test_diagnose_finds_planted_cause():
    """진단: 심어 둔 원인(온도 높은 구간)을 1순위로 찾고, 상관없는 칼럼은 올리지 않는다"""
    import numpy as np
    import pandas as pd
    from mlng_ai.diagnose import diagnose, render_html
    r = np.random.default_rng(3)
    n = 1500
    t, noise = r.normal(850, 10, n), r.normal(0, 1, n)
    bad = (r.random(n) < 0.02 + 0.2 * (t > 860)).astype(int)
    df = pd.DataFrame({'측정시각': pd.date_range('2026-01-01', periods=n, freq='h').astype(str), 'lot_no': [f'L{i}' for i in range(n)],
                       '소입 온도 ℃': t, '잡음 센서': noise, 'NG': bad})
    res = diagnose(df, unit_cost=10000, name='t.csv')
    assert res['outcome'] == 'NG'
    assert res['factors'][0]['column'] == '소입 온도 ℃' and res['factors'][0]['lift'] > 2
    assert '잡음 센서' not in [f['column'] for f in res['factors'][:2]]
    assert res['copq']['total'] == bad.sum() * 10000
    assert '불량 원인 후보' in render_html(res)


def test_diagnose_without_outcome():
    import pandas as pd
    from mlng_ai.diagnose import diagnose
    res = diagnose(pd.DataFrame({'온도': range(50), '압력': range(50)}))
    assert res['outcome'] == '' and res['readiness']['score'] < 50 and res['next']
