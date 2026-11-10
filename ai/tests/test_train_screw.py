import numpy as np
import pytest
from mlng_ai.train_screw import curve_features, partition, STAGES

def test_features_ignore_reported_results_and_use_actual_angle_axis():
    raw={'result':'OK', 'tightening steps':[{'name':STAGES[1], 'result':'OK',
        'graph':{'angle values':[0,2,4], 'torque values':[1,5,9]}}]}
    a=curve_features(raw)
    raw['result']='NOK';raw['tightening steps'][0]['result']='NOK'
    b=curve_features(raw)
    assert all(a[k]==b[k] or np.isnan(a[k]) and np.isnan(b[k]) for k in a)
    assert a['stage_1_slope']==2
    assert a['stage_0_present']==0

def test_empty_curves_not_interpreted_as_normal():
    with pytest.raises(ValueError, match='No usable'):
        curve_features({'tightening steps':[]})

def test_workpiece_split_is_repeatable_and_independent_of_scenario():
    assert partition('40000000017401')==partition('40000000017401')
    assert set(partition(str(i)) for i in range(1000))=={'train','validation','test'}
