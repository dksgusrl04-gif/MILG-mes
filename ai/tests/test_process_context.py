import pandas as pd
from mlng_ai.process_context import recognize, analyze, choose_registered_process
from mlng_ai.service import Service

def screw():
    return {'machine_id':'M1','tool_id':'T1','installation_id':'I1',
        'part_number':'P1','screw_type':'S1','program':'P',
        'tightening steps':[{'name':'Thread forming','graph':
          {'torque values':[1,2,3], 'angle values':[0,1,2]}}]}

def mct():
    return {'machine_id':'M1','tool_id':'T1','installation_id':'I1',
        'material':'steel','operation':'finish','rpm':1000,'feed':100,'depth':1,
        'cut_load':[1,2,3],'time_seconds':[0,1,2]}

def test_signature_and_ambiguity():
    assert recognize(pd.DataFrame([screw()]))['family'] == 'screw'
    assert recognize(pd.DataFrame([mct()]))['family'] == 'mct'
    assert recognize(pd.DataFrame({'force':[1],'torque':[2]}))['status'] == 'unknown'
    assert recognize(pd.DataFrame([screw()]),'mct')['status'] == 'conflict'
    assert recognize(pd.DataFrame([{**screw(),**mct()}]))['status'] == 'needs_confirmation'

def test_recipe_matches_process_and_is_read_only(tmp_path):
    service=Service(tmp_path)
    df=pd.DataFrame([screw()])
    r=service.analyze_process(df)
    assert r['template']['id']=='screw_tightening'
    assert r['rows'][0]['features']['profile:peak']==3
    assert r['quality_prediction'] is None and r['state_persisted'] is False
    assert service.analyze_process(df)['rows'][0]['usage_count']==1
    assert service.analyze_process(df,'','mct_endmill')['mode']=='needs_confirmation'
    assert not list(tmp_path.rglob('*.json'))

def test_missing_signals_are_not_normal():
    r=analyze(pd.DataFrame({'tool_id':['T']}),'mct')
    assert r['rows'][0]['available'] is False

def test_synthetic_and_multi_model_never_auto_selected():
    context=recognize(pd.DataFrame([mct()]))
    assert choose_registered_process(context,{'mct_hmotp':{'synthetic':False}})=='mct_hmotp'
    assert choose_registered_process(context,{'mct_hmotp':{'synthetic':False},'mct_nasa':{'synthetic':False}}) is None
    pf={'status':'recognized','family':'pressfit'}
    assert choose_registered_process(pf,{'causalman_pressfit':{'synthetic':True}}) is None

def test_auto_predict_returns_explicit_analysis_not_fake_probability(tmp_path):
    service=Service(tmp_path)
    r=service.predict('auto',pd.DataFrame([screw()]))
    assert r['mode']=='template_analysis_only' and r['quality_prediction'] is None
    assert service.predict('auto',pd.DataFrame({'force':[1]}))['mode']=='needs_confirmation'
