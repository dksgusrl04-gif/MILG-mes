import json
import numpy as np
import pandas as pd
import pytest
from mlng_ai.joint_engine import data_part,fit_common,semantic_transform,regression_gate,install_joint
from mlng_ai.routing import Router
from mlng_ai.service import Service
from mlng_ai.adapters import input_features
from mlng_ai.strict_evaluation import TrainTransform

def sample_parts():
    rng=np.random.default_rng(42);parts={}
    for key in ('mct_alpha','screw_beta'):
        X=pd.DataFrame({'torque_mean':rng.normal(size=120)})
        y=(np.abs(X.torque_mean.to_numpy())>.8).astype(int)
        parts[key]=data_part(X,y,np.repeat(np.arange(12),10),'mct_sensor_columns_v1','test_target')
    return parts

def test_shared_coefficients_learn_and_inference_recipe_matches(tmp_path):
    from threadpoolctl import threadpool_limits
    parts=sample_parts()
    with threadpool_limits(limits=2):model=fit_common(parts,list(parts),batches=2)
    assert np.linalg.norm(model.m[:model.K])>0
    assert set(model.procs)==set(parts)
    bundle={'format':'milg_joint_v1','model':model.to_dict(),
            'adapters':{k:d['adapter'] for k,d in parts.items()},'thresholds':dict.fromkeys(parts,.5),
            'regression_gate':{'passed':True}}
    path=tmp_path/'joint.json';path.write_text(json.dumps(bundle),encoding='utf-8')
    router=Router(tmp_path/'state');router.register('mct_alpha',path,'joint','test')
    raw=parts['mct_alpha']['features'].iloc[:5].copy();raw['target']=1
    result=router.predict('mct_alpha',raw)
    raw.target=0
    assert router.predict('mct_alpha',raw)['rows']==result['rows']
    adapter=parts['mct_alpha']['adapter'];x=semantic_transform(raw[adapter['columns']],adapter)
    expected=model.prob('mct_alpha',model.predict('mct_alpha',x,np.ones(len(x)))[0])
    np.testing.assert_allclose([r['p'] for r in result['rows']],expected)

def test_specialist_raw_curve_auto_route_and_checksum(tmp_path):
    import joblib
    from sklearn.linear_model import LogisticRegression
    columns=['stage_1_mean','stage_1_slope']
    X=pd.DataFrame({columns[0]:[-2,-1,1,2],columns[1]:[-2,-1,1,2]})
    prep=TrainTransform().fit(X);model=LogisticRegression().fit(prep.transform(X),[0,0,1,1])
    src=tmp_path/'trusted.joblib';joblib.dump({'model':model,'preprocessing':prep.export(),
          'threshold':.5,'target':'experimental_condition','feature_recipe':'screw_stage_torque_angle_v1'},src)
    router=Router(tmp_path/'state');route=router.register('screw_tightening',src,'specialist','test')
    df=pd.DataFrame([{'tightening steps':[{'name':'Thread forming','graph':{'angle values':[0,1,2],'torque values':[1,2,3]}}]}])
    r=Service(tmp_path/'state').predict('auto',df)
    assert r['process']=='screw_tightening' and r['adapter']=='specialist'
    assert 0<=r['rows'][0]['p']<=1
    with (router.directory/route['model']).open('ab') as f:f.write(b'changed')
    with pytest.raises(ValueError,match='checksum'):router.predict('screw_tightening',df)

def test_aursad_and_clamping_contracts_reject_bad_arrays():
    channels=['output_double_register_25']+[f'actual_TCP_force_{i}' for i in range(6)]
    df=pd.DataFrame([{c:[0,1,2] for c in channels}]);df.at[0,channels[-1]]=[0,1]
    with pytest.raises(ValueError,match='equal-length'):input_features('aursad_post_operation_progress_shapes_v1',df,['output_double_register_25_mean'])

def test_gate_blocks_installation_without_route_changes(tmp_path):
    def m(auc,recall,fpr):return {'auc':auc,'recall':recall,'false_positive_rate':fpr}
    before={'mct':{'validation':m(.9,.8,.05),'test':m(.9,.8,.05)}}
    after={'mct':{'validation':m(.7,.4,.15),'test':m(.7,.4,.15)}}
    gate=regression_gate(before,after);assert gate['passed'] is False
    file=tmp_path/'blocked.json';file.write_text(json.dumps({'regression_gate':gate}))
    state=tmp_path/'state'
    with pytest.raises(ValueError,match='gate failed'):install_joint(file,state,'test')
    assert not (state/'routes.json').exists()
