import copy
import numpy as np
import pandas as pd
import pytest
from mlng_ai.profiles import ProfileRuntime, load_profile
from mlng_ai.datasets import Table
from mlng_ai.features import accumulate, BASE_SLOTS
from mlng_ai.schema import sense_table

def profile():
    p = load_profile('mct_endmill')
    p['baseline']['min_samples'] = 2
    return p

def row(tool='A', installation='life1', value=10, confirmed=False):
    return dict(machine_id='M1', tool_id=tool, installation_id=installation,
                cut_load=[value]*4, time_seconds=[0, 1, 2, 3],
                cut_seconds=3, confirmed_normal=confirmed,
                material='steel', operation='finish', rpm=1000, feed=100, depth=1)

def test_return_to_tool_and_new_life(tmp_path):
    p = profile(); r = ProfileRuntime(p, 'factory-A')
    r.observe(row(confirmed=True)); r.observe(row('B'))
    assert r.observe(row(confirmed=True))['usage_count'] == 2
    assert r.observe(row(value=15))['features']['profile:relative'] == .5
    path = tmp_path/'state.json'; r.save(path)
    r = ProfileRuntime.load(path, 'factory-A', p)
    assert r.observe(row())['usage_count'] == 4
    assert r.observe(row(installation='life2'))['usage_count'] == 1
    with pytest.raises(ValueError): ProfileRuntime.load(path, 'factory-B', p)
    q = copy.deepcopy(p); q['version'] = '2'
    with pytest.raises(ValueError): ProfileRuntime.load(path, 'factory-A', q)

def test_confirmed_past_only_and_freeze():
    r = ProfileRuntime(profile(), 'A')
    for _ in range(20): assert r.observe(row())['features']['profile:baseline_ready'] == 0
    r.observe(row(confirmed=True))
    assert r.observe(row(confirmed=True))['features']['profile:baseline_ready'] == 0
    assert r.observe(row(value=30, confirmed=True))['features']['profile:relative'] == 2
    assert r.observe(row(value=10))['features']['profile:relative'] == 0
    changed = row(); changed['material'] = 'aluminium'
    assert r.observe(changed)['baseline_status'] != 'ready'

def test_missing_invalid_curve_and_limits():
    p = profile(); p['engineering_limits'] = {'profile:peak': [0, 12]}
    r = ProfileRuntime(p, 'A')
    x = row(); x['time_seconds'] = [0, 0, 2, 3]
    assert not r.observe(x)['available']
    assert r.observe(row())['usage_count'] == 1
    assert r.observe(row(value=15))['engineering_checks']['profile:peak'] == 'outside_limits'
    assert r.observe(row())['causal_effect_prediction'] is None

def test_screw_preserves_stage_not_global_curve():
    r = ProfileRuntime(load_profile('screw_tightening'), 'A')
    x = row(); x.update(part_number='P1', screw_type='S1', program='fourstep'); x['tightening steps'] = [
        {'name':'Finding','graph':{'torque values':[100,100,100], 'angle values':[0,1,2]}},
        {'name':'Thread forming','graph':{'torque values':[1,2,3], 'angle values':[0,1,2]}}]
    assert r.observe(x)['features']['profile:peak'] == 3
    x['tightening steps'] = []
    assert not r.observe(x)['available']

def test_existing_accumulation_interleaved_tools():
    df = pd.DataFrame({'tool_id':['A','B','A','B','A'], 'force':[10,50,11,52,12]})
    t = Table('test','test',df,entity='tool_id')
    s = sense_table(df, {'force':{'label':'cutting force','unit':'N'}})
    x = pd.DataFrame(0., index=df.index, columns=BASE_SLOTS)
    accumulate(df,s,t,np.ones(len(df)),x)
    assert x['acc:count'].tolist() == [1,1,2,2,3]

def test_model_old_slots_expand_without_changing_predictions():
    from mlng_ai.model import CumulativeModel
    m = CumulativeModel(['q:force']); m.add_process('test', .1)
    before = m.predict('test',np.ones((2,1)),np.ones(2))[0]
    n = CumulativeModel.from_dict(m.to_dict())
    xx = np.zeros((2,n.K)); xx[:,n.slots.index('q:force')] = 1
    assert np.allclose(before, n.predict('test',xx,np.ones(2))[0])

def test_profile_encode_connects_real_recipe_slots():
    from mlng_ai.features import encode, add_upstream, SLOTS
    rows = [dict(row(value=10+i/10, confirmed=True), defect=i%2) for i in range(24)]
    df = pd.DataFrame(rows)
    t = Table('p','mct',df,{'defect':{'label':'defect outcome'}},profile=profile(),entity='tool_id')
    e = encode(t, overrides={'defect':{'role':'outcome'}})
    add_upstream({'p':e})
    assert e.X['profile:rms'].iloc[0] == 10
    assert e.X['profile:baseline_ready'].iloc[2] == 1
    assert set(SLOTS) <= set(e.X.columns)
