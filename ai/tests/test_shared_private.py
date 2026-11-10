import copy
import json
import numpy as np
import pandas as pd
import pytest
from mlng_ai.shared_private import screw_features_v2,private_inputs,networks,choose_policy,train,predict_frame,validate_bundle,evaluate
from mlng_ai.joint_engine import data_part

def test_curve_order_preserved_despite_matching_summary():
    def raw(values):return {'tightening steps':[{'name':'Thread forming','graph':{'angle values':[0,1,2,3,4],
                    'torque values':values,'time values':[0,.1,.2,.3,.4]}}]}
    a=screw_features_v2(raw([0,1,3,1,0]));b=screw_features_v2(raw([0,3,1,1,0]))
    assert a['stage_1_mean']==b['stage_1_mean']
    assert a['stage_1_torque_progress_6']!=b['stage_1_torque_progress_6']
    assert a['stage_1_duration']==.4

def test_shared_encoder_receives_gradients_and_private_can_be_frozen():
    torch=pytest.importorskip('torch')  # 신경망 의존성(requirements-neural.txt)이 없으면 건너뜀
    net=networks({'mct_test':4,'screw_test':6},['mct_test','screw_test'])
    for p in net.locals.parameters():p.requires_grad_(False)
    for head in net.heads.values():torch.nn.init.constant_(head.weight,.1)
    before={k:v.clone() for k,v in net.locals.state_dict().items()}
    opt=torch.optim.Adam(list(net.projectors.parameters())+list(net.encoder.parameters())+list(net.heads.parameters()),lr=.01)
    x=torch.randn(12,4);loss=(net.local('mct_test',x)+net.residual('mct_test',x)).square().mean()
    loss.backward();assert sum(float(p.grad.abs().sum()) for p in net.encoder.parameters())>0
    opt.step()
    assert all(torch.equal(before[k],v) for k,v in net.locals.state_dict().items())

def test_policy_rejects_harmful_shared_predictions():
    y=np.r_[np.zeros(100),np.ones(100)];local=np.r_[np.linspace(0,.4,100),np.linspace(.6,1,100)]
    p=choose_policy(y,local,1-local,.05)
    assert p['shared_weight']==0

def test_neural_training_freeze_serialization_and_engine(tmp_path):
    pytest.importorskip('torch')
    from mlng_ai.routing import Router
    rng=np.random.default_rng(4);data={}
    for key in ('mct_test','causalman_pressfit'):
        x=rng.normal(size=180);frame=pd.DataFrame({'force_mean':x})
        data[key]=data_part(frame,(x>0).astype(int),np.repeat(np.arange(18),10),
                     'mct_sensor_columns_v1','unit_test',synthetic=key=='causalman_pressfit')
    b=train(data,tmp_path,epochs=4,patience=2,threads=1)
    assert (tmp_path/'frozen.json').exists()
    assert not (tmp_path/'evaluation.json').exists()
    assert b['processes']['causalman_pressfit']['shared_weight']==0
    assert 'causalman_pressfit' not in b['real_processes']
    validate_bundle(b)
    raw=data['mct_test']['features'].iloc[:3].copy();a=predict_frame(b,'mct_test',raw)
    raw['label']=1
    np.testing.assert_allclose(predict_frame(b,'mct_test',raw),a)
    # Exercise the real engine adapter using the already-tested policy fixture.
    allowed=copy.deepcopy(b);allowed['regression_gate']={'passed':True}
    file=tmp_path/'allowed.json';file.write_text(json.dumps(allowed),encoding='utf-8')
    router=Router(tmp_path/'state');router.register('mct_test',file,'joint_v2','test')
    np.testing.assert_allclose([r['p'] for r in router.predict('mct_test',raw)['rows']],a)
    evaluate(tmp_path)
    with pytest.raises(ValueError,match='already evaluated'):evaluate(tmp_path)
