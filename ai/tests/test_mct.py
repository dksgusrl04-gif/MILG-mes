import numpy as np
import pandas as pd

from mlng_ai.datasets import Table
from mlng_ai.mct import label_rule, tool_cv
from mlng_ai.normal import NormalBaseline


def test_label_rule_keeps_missing():
    df = pd.DataFrame({'wear': [10, 250, None, 200]})
    out = label_rule(df, 'wear', 200, 'ng')
    assert out.ng.tolist()[:2] == [0.0, 1.0] and np.isnan(out.ng.iloc[2]) and out.ng.iloc[3] == 1.0


def test_normal_baseline_flags_shift_per_group():
    rng = np.random.default_rng(0)
    ref = pd.DataFrame({'g': ['a'] * 200 + ['b'] * 200, 'load': np.r_[rng.normal(10, 1, 200), rng.normal(50, 1, 200)], 'cur': rng.normal(5, 0.5, 400)})
    nb = NormalBaseline(q=0.99).fit(ref, ['load', 'cur'], group='g')
    new = pd.DataFrame({'g': ['a', 'b', 'a', 'c'], 'load': [10.2, 50.1, 18.0, 10.0], 'cur': [5.0, 5.1, 5.0, 5.0]})
    s = nb.score(new)
    assert not s.alert.iloc[0] and not s.alert.iloc[1]        # 그룹마다 평소 값이면 알림 없음
    assert s.alert.iloc[2] and s.top.iloc[2] == 'load'         # 크게 벗어나면 알림 · 원인 칼럼
    assert not s.own_baseline.iloc[3]                          # 처음 보는 그룹은 전체 기준으로 표시


def test_tool_cv_runs_and_learns():
    rng = np.random.default_rng(1)
    rows = []
    for t in range(4):
        for i in range(60):
            w = i / 60
            rows.append({'tool_id': f'T{t}', 'force_rms': 100 + 40 * w + rng.normal(0, 12), 'spindle_current': 5 + rng.normal(0, 0.3), 'tool_ng': float((w > 0.7) != (rng.random() < 0.08))})
    df = pd.DataFrame(rows)
    meta = {'force_rms': {'label': '절삭력 RMS', 'unit': 'N'}, 'spindle_current': {'label': '주축 전류', 'unit': 'A'}, 'tool_ng': {'label': '공구 교체 필요'}}
    t = Table('mct_syn', '합성 MCT', df, meta, {}, '', {}, 'row', 'synthetic', entity='tool_id')
    r = tool_cv({'mct_syn': t}, 'sensor')['mct_syn']
    assert r['tools'] == 4 and r['events'] == df.tool_ng.sum()
    assert all(0 <= r[m]['auc'] <= 1 for m in ('rule', 'local', 'joint')) and len(r['folds']) == 4


def test_stiehl_summarize(tmp_path):
    import h5py
    from mlng_ai.stiehl import summarize
    src = tmp_path / 'h5'
    src.mkdir()
    rng = np.random.default_rng(2)
    for r, vb in ((1, 10), (2, 130)):
        with h5py.File(src / f'M1T1R{r}C{r * 4}VB{vb}.h5', 'w') as f:
            for k, v in (('machine', 1), ('tool', 1), ('run', r), ('cumulated_tool_contact_time', r * 4.0), ('wear', vb)):
                f[f'labels/{k}'] = np.array([[v]])
            f['signals_sensor/force_sensor_x'] = rng.normal(0, 100 + vb, (1, 5000)).astype(np.float32)
            f['signals_sensor/time_sensor'] = np.linspace(0, 0.2, 5000, dtype=np.float32)[None]
            f['signals_machine/torque_spindle'] = rng.normal(1, 0.1, (1, 100)).astype(np.float32)
            if r == 2:                                          # 일부 회차에만 있는 채널
                f['signals_machine/torque_axis_z'] = rng.normal(0, 1, (1, 100)).astype(np.float32)
    out = summarize(src, tmp_path / 'sum', workers=1)
    d = pd.read_csv(out).sort_values('run')
    assert len(d) == 2 and d.wear_um.tolist() == [10, 130]
    assert d.force_x_rms.iloc[1] > d.force_x_rms.iloc[0] and 'torque_spindle_rms' in d
    assert np.isnan(d.torque_axis_z_rms.iloc[0]) and np.isfinite(d.torque_axis_z_rms.iloc[1])
    summarize(src, tmp_path / 'sum', workers=1)                 # 다시 실행해도 중복 없음
    assert len(pd.read_csv(out)) == 2


def test_old_model_loads_after_new_slots():
    from mlng_ai.features import SLOTS
    from mlng_ai.model import CumulativeModel
    old = [s for s in SLOTS if not s.startswith('rel:')]
    M = CumulativeModel(old)
    M.add_process('p', 0.05)
    M.m[0] = 0.7
    M2 = CumulativeModel.from_dict(M.to_dict())
    assert M2.slots == list(SLOTS) and M2.K == len(SLOTS)
    assert M2.m[SLOTS.index(old[0])] == 0.7 and M2.m[SLOTS.index('rel:up')] == 0.0
    mu = M2.predict('p', np.zeros((3, len(SLOTS))), np.ones(3))[0]
    assert np.allclose(mu, mu[0]) and 0 < mu[0] < 1


def test_relative_change_keeps_direction():
    from mlng_ai.features import relative_change
    df = pd.DataFrame({'f': np.r_[np.full(15, 100.0), np.full(5, 130.0), np.full(15, 100.0)]})
    reset = np.zeros(35, bool); reset[0] = reset[20] = True
    R = relative_change(df, ['f'], reset, n_ref=10)[:, 0]
    assert R[0] == 0 and R[16] > 0 and abs(R[22]) < 1e-9     # 커진 뒤 +, 새 공구로 바뀌면 다시 0 (떨어짐을 이상으로 보지 않음)
