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


def test_pressfit_curve_and_snap(tmp_path):
    import io, zipfile
    import scipy.io as sio
    from mlng_ai.pressfit import curve_features, snap_table, summarize_snap
    f = curve_features(np.r_[np.linspace(0, 10, 50), np.linspace(10, 2, 50)], 'force')
    assert f['force_max'] == 10 and 0.4 < f['force_peakpos'] < 0.6 and 'force_end' in f
    rng = np.random.default_rng(3)
    sig = {f'signal_{i}': rng.normal(i % 2 * 5, 1, 200)[:, None] for i in range(1, 9)}
    buf, lab = io.BytesIO(), io.BytesIO()
    sio.savemat(buf, {'Annular_Snap_Training': sig}); sio.savemat(lab, {'Annular_Snap_Training_Labels': np.array([[i % 2] for i in range(1, 9)], np.uint8)})
    z = tmp_path / 'snap.zip'
    with zipfile.ZipFile(z, 'w') as zf:
        zf.writestr('annular snap/Annular_Snap_Training.mat', buf.getvalue()); zf.writestr('annular snap/Annular_Snap_Training_Labels.mat', lab.getvalue())
    d = summarize_snap(z)
    assert len(d) == 8 and set(d.group) == {'annular·human'} and d.ok.sum() == 4
    d.to_csv(tmp_path / 'pf_snap.csv', index=False)
    t, g = snap_table(tmp_path / 'pf_snap.csv')
    assert t.df.assembly_ng.sum() == 4 and len(g) == 8 and 'group' not in t.df


def test_joining_screw_and_safe_pickle(tmp_path):
    import io, json, pickle, zipfile
    import pytest
    from mlng_ai.joining import _SafeUnpickler, load_tables, summarize_screw
    js = {'tightening steps': [{'graph': {'torque values': [0, 1, 2, 3], 'angle values': [0, 10, 20, 30], 'gradient values': [0, 1, 1, 1]}},
                               {'graph': {'torque values': [3, 2.5], 'angle values': [35, 40], 'gradient values': [0, -0.5]}}]}
    lab = 'run_id,file_name,class_value,workpiece_id,workpiece_date,workpiece_usage,workpiece_result,workpiece_location,scenario_condition,scenario_exception\n'
    with zipfile.ZipFile(tmp_path / 's01_test.zip', 'w') as z:
        for i, (res, cond) in enumerate((('OK', 'normal'), ('NOK', 'faulty'), ('OK', 'faulty'))):
            z.writestr(f'json/g/Ch_{i}.json', json.dumps(js)); lab += f'{i},Ch_{i}.json,g,1,2022-01-01,0,{res},left,{cond},0\n'
        z.writestr('labels.csv', lab)
    out = summarize_screw(tmp_path, tmp_path)
    d = pd.read_csv(out)
    assert len(d) == 3 and d.torque_max.iloc[0] == 3 and d.angle_at_torque_peak.iloc[0] == 30 and d.faulty.sum() == 2
    T, G, R = load_tables(tmp_path)
    assert 'nok' not in T['j_screw'].df and T['j_screw'].df.join_ng.sum() == 2      # 체결기 판정은 입력에서 뺀다
    bad = pickle.dumps(print)                                   # builtins.print — 허용 목록 밖
    with pytest.raises(pickle.UnpicklingError):
        _SafeUnpickler(io.BytesIO(bad)).load()
    ok = pickle.dumps(pd.DataFrame({'a': [1, 2]}))
    assert _SafeUnpickler(io.BytesIO(ok)).load().a.sum() == 3


def test_booster_learns_and_roundtrips(tmp_path):
    from mlng_ai.booster import ProcessBooster
    rng = np.random.default_rng(4)
    rows = []
    for t in range(5):
        base = rng.uniform(80, 160)                                  # 공구마다 절대 수준이 달라도
        for i in range(50):
            w = i / 50
            rows.append({'tool_id': f'T{t}', 'force': base * (1 + 0.5 * w) + rng.normal(0, 3), 'cur': rng.normal(5, 0.2), 'ng': float(w > 0.8)})
    df = pd.DataFrame(rows)
    tr, te = df[df.tool_id != 'T4'], df[df.tool_id == 'T4']
    b = ProcessBooster().fit(tr, tr.ng, tr.tool_id.to_numpy(), ['force', 'cur'])
    s = b.score(te, te.tool_id.to_numpy())
    from mlng_ai.engine import auc
    assert auc(s.p.to_numpy(), te.ng.to_numpy()) > 0.9 and 'rel_force' in b._X(te, te.tool_id.to_numpy())
    b.save(tmp_path / 'b', {'dataset': 'syn'})
    b2 = ProcessBooster.load(tmp_path / 'b')
    assert np.allclose(b2.score(te, te.tool_id.to_numpy()).p, s.p) and b2.threshold == b.threshold


def test_outcome_polarity_and_curve_segments():
    from mlng_ai.schema import sense_table
    from mlng_ai.pressfit import curve_features
    df = pd.DataFrame({'assembly_ng': [0, 1] * 20, 'x': range(40)})
    assert sense_table(df, {'assembly_ng': {'label': '조립 불량 (OK 아님)'}})['assembly_ng'].polarity == 1     # «OK 아님» 은 불량 쪽
    assert sense_table(pd.DataFrame({'ok': [0, 1] * 20, 'x': range(40)}), {})['ok'].polarity == -1
    f = curve_features(np.r_[np.linspace(0, 10, 50), np.linspace(10, 2, 50)], 'force')
    assert f['force_segslope1'] > 0 and f['force_segslope5'] < 0 and 'force_area' in f


def test_engine_local_inputs_close_gap():
    from mlng_ai.local_inputs import engine_with_local
    from mlng_ai.engine import auc
    rng = np.random.default_rng(5)
    n = 400
    a, b = rng.normal(0, 1, n), rng.normal(0, 1, n)
    y = (a - 0.8 * b + rng.normal(0, 0.5, n) > 1).astype(float)
    from mlng_ai.features import SLOTS
    Xs = np.zeros((n, len(SLOTS)))                                   # 공통 슬롯엔 정보 없음 — 원본 칼럼에만 있음
    L = pd.DataFrame({'a': a, 'b': b})
    tr, te = np.arange(n) < 300, np.arange(n) >= 300
    _, s, M, sc = engine_with_local('p', Xs[tr], y[tr], Xs[te], L[tr], L[te])
    assert auc(s, y[te]) > 0.85 and sc.names == ['L:a', 'L:b']


def test_engine_local_saves_json_only(tmp_path):
    import json
    from mlng_ai.datasets import Table
    from mlng_ai.local_inputs import train_and_save
    rng = np.random.default_rng(6)
    rows = [{'tool_id': f'T{t}', 'force_rms': 100 + 40 * i / 40 + rng.normal(0, 5), 'spindle_current': 5 + rng.normal(0, 0.3), 'tool_ng': float(i > 30)} for t in range(3) for i in range(40)]
    df = pd.DataFrame(rows)
    t = Table('mct_syn', '합성', df, {'force_rms': {'label': '절삭력 RMS', 'unit': 'N'}, 'spindle_current': {'label': '주축 전류', 'unit': 'A'}, 'tool_ng': {'label': '공구 교체 필요'}}, {}, '', {}, 'row', 'syn', entity='tool_id')
    M, sc = train_and_save(t, tmp_path / 'e.json', {'dataset': 'syn'})
    d = json.loads((tmp_path / 'e.json').read_text(encoding='utf-8'))
    assert d['kind'] == 'milg_engine_local' and 'L:force_rms' in d['model']['slots'] and 'rel_force_rms' in d['local']['cols']
