"""Leakage, missing sensors, immutable scenario bundles and pilot integration."""
import json
import sys
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.model_selection import GroupKFold
from mlng_ai.scenario_runtime import predict_artifact,install
from mlng_ai.pilot import PilotEngine,write_json
from mlng_ai.routing import Router,digest
from milg_engine.milg_core import MilgNormalScorer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'milg_engine'))
import engine


def anomaly_config():
    return {'scenario':'AUDIT','process':'test','title':'test','question':'test','task':'anomaly',
            'label':{'source':'none'},'features':{'exclude':['t','setup','defect']},
            'split':{'method':'time','column':'t','datetime':False,'test_size':.5},
            'anomaly':{'context':['setup'],'candidates':['context','milg'],'label_eval':{'column':'defect','positive':[1]}},
            'output':{'carry_columns':['t','defect']}}


def test_anomaly_original_holdout_and_faulty_reference_exclusion(tmp_path):
    import time
    n=800;t=np.arange(n);bad=((t<200)|((t>=400)&(t<450))).astype(int)
    d=pd.DataFrame({'unit_id':t.astype(str),'t':t,'setup':['A']*n,'force':np.random.default_rng(7).normal(size=n)+bad*3,'defect':bad})
    r=engine.run_anomaly(anomaly_config(),d,str(tmp_path),time.time())
    p=pd.read_csv(tmp_path/'predictions.csv')
    assert set(p.loc[p.split=='test','t'])==set(range(400,800))
    assert p.loc[p.split.isin(['train','calibration']),'defect'].sum()==0
    assert p.loc[p.split=='train','t'].max()<p.loc[p.split=='calibration','t'].min()
    assert r['units']['train']==160 and r['units']['calibration']==40
    assert r['threshold_source']=='held_out_normal_calibration'
    a=joblib.load(tmp_path/'candidate.joblib')
    x=p[['t']].copy();x['force']=d.set_index('t').loc[p.t,'force'].to_numpy();x['setup']='A'
    served=predict_artifact(a,x)
    np.testing.assert_allclose([z['anomaly_score'] for z in served['rows']],p.score,atol=.00051)


def test_missing_sensors_are_unknown_in_batch_and_serving(tmp_path):
    import time
    n=80;t=np.arange(n);d=pd.DataFrame({'unit_id':t.astype(str),'t':t,'setup':['A']*n,'force':np.random.default_rng(8).normal(size=n),'defect':0})
    d.loc[79,'force']=np.nan
    engine.run_anomaly(anomaly_config(),d,str(tmp_path),time.time())
    p=pd.read_csv(tmp_path/'predictions.csv');assert p.iloc[-1].decision=='UNKNOWN' and pd.isna(p.iloc[-1].score)
    a=joblib.load(tmp_path/'candidate.joblib');r=predict_artifact(a,pd.DataFrame({'force':[None],'setup':['A']}))['rows'][0]
    assert r['decision']=='UNKNOWN' and r['alert'] is None and r['top']==[]
    r=predict_artifact(a,pd.DataFrame({'force':[1.]}))['rows'][0]
    assert r['decision']=='UNKNOWN' and r['input_valid'] is False and r['top']==[]
    model=MilgNormalScorer().fit(pd.DataFrame({'force':np.arange(30,dtype=float)}),['A']*30)
    assert np.isnan(model.score(pd.DataFrame({'force':[np.nan]}),['A'])[0][0])


def test_two_tools_stay_disjoint_and_one_group_fails():
    groups=pd.Series(['A']*20+['B']*20);y=pd.Series([0,1]*20)
    cv=engine.inner_cv({'task':'binary','split':{'method':'group'}},y,groups)
    assert isinstance(cv,GroupKFold)
    for a,b in cv.split(np.zeros((40,1)),y,groups):assert not(set(groups.iloc[a])&set(groups.iloc[b]))
    with pytest.raises(ValueError,match='two independent'):engine.inner_cv({'task':'binary','split':{'method':'group'}},y,pd.Series(['A']*40))


def test_unsorted_duplicate_timestamps_never_cross_boundaries():
    d=pd.DataFrame({'t':[4,0,3,1,5,2,9,8,7,6,1,5],'__y':[0,1]*6})
    cfg={'task':'binary','split':{'method':'time','column':'t','datetime':False,'test_size':.2}}
    te,times=engine.make_split(d,cfg)
    assert d.loc[~te,'t'].max()<d.loc[te,'t'].min()
    cv=engine.inner_cv(cfg,d.loc[~te,'__y'],times[~te],n_splits=2)
    train=d.loc[~te]
    for a,b in cv.split(train):assert train.iloc[a].t.max()<train.iloc[b].t.min()


def binary_candidate(tmp_path):
    import yaml
    rng=np.random.default_rng(6);force=rng.normal(size=360)
    d=pd.DataFrame({'cycle':np.arange(360),'day':np.repeat(list('ABCDEF'),60),'force':force,'defect':(force>0).astype(int)})
    d.to_csv(tmp_path/'units.csv',index=False)
    cfg={'scenario':'TEST','process':'test','title':'test','question':'test','data_root':str(tmp_path),
         'unit':{'type':'table','path':'units.csv','id':'cycle'},'task':'binary',
         'label':{'source':'column','column':'defect','positive':[1]},'features':{'exclude':['cycle','day']},
         'split':{'method':'group','group':'day','test_groups':['F']},'model':{'candidates':['milg']},
         'decision':{'type':'threshold_for_recall','target_recall':.8}}
    path=tmp_path/'s.yaml';path.write_text(yaml.safe_dump(cfg),encoding='utf-8')
    report=engine.run(str(path),str(tmp_path/'out'),300)
    assert report['deployment_gate']['passed']
    return tmp_path/'out'/'TEST'/'candidate.joblib'


def test_bundle_to_pilot_and_missing_cycle_without_quality_authority(tmp_path):
    candidate=binary_candidate(tmp_path)
    state=tmp_path/'state';release=tmp_path/'release';write_json(release/'routes.json',{})
    write_json(release/'release.json',{'id':'before','processes':{'pressfit':{}},'routes_sha256':digest(release/'routes.json')})
    write_json(state/'pilot-active.json',{'release':str(release),'sha256':digest(release/'release.json')})
    install(candidate,state,'test_scenario','MIT','test-owner',tmp_path/'install')
    p=PilotEngine(state);assert 'pressfit' in p.manifest['processes']
    sample=json.loads((p.release/'examples'/'test_scenario.json').read_text())
    r=p.submit(sample);assert r['adapter']=='scenario' and r['rows'][0]['quality_prediction'] is None
    assert r['rows'][0]['alert'] is None and r['input_stage']=='completed_unit_features'
    sample['cycle_id']='missing-sensor';sample['rows']=[{'force':None}]
    assert p.submit(sample)['rows'][0]['decision']=='UNKNOWN'
    route=p.router.routes()['test_scenario'];path=p.release/route['model'];path.write_bytes(path.read_bytes()+b'tamper')
    with pytest.raises(ValueError,match='checksum'):p.router.predict('test_scenario',pd.DataFrame({'force':[0.]}))


def test_gate_rejects_failed_or_unlicensed_bundle(tmp_path):
    candidate=binary_candidate(tmp_path);router=Router(tmp_path/'state')
    with pytest.raises(ValueError,match='license'):router.register('test_scenario',candidate,'scenario','owner')
    a=joblib.load(candidate);a['evaluation_gate']['passed']=False;a['source_license']='MIT';joblib.dump(a,candidate)
    with pytest.raises(ValueError,match='gate failed'):router.register('test_scenario',candidate,'scenario','owner')
    assert router.routes()=={}

@pytest.mark.parametrize('task',['regression','multiclass'])
def test_nonbinary_scenario_bundle_preserves_reference_only_decisions(tmp_path,task):
    import yaml
    rng=np.random.default_rng(11);signal=rng.normal(size=360)
    y=signal*2 if task=='regression' else np.where(signal<-.5,'A',np.where(signal>.5,'C','B'))
    df=pd.DataFrame({'cycle':np.arange(360),'group':np.repeat(list('ABCDEF'),60),'signal':signal,'target':y})
    df.to_csv(tmp_path/'d.csv',index=False)
    cfg={'scenario':'NONBINARY','process':'test','title':'test','question':'test','data_root':str(tmp_path),
         'unit':{'type':'table','path':'d.csv','id':'cycle'},'task':task,
         'label':{'source':'column','column':'target'},'features':{'exclude':['cycle','group']},
         'split':{'method':'group','group':'group','test_groups':['F']},
         'model':{'candidates':['ridge' if task=='regression' else 'hgb']}}
    if task=='regression':cfg['decision']={'type':'limit','limit':1.,'action':'stop'}
    path=tmp_path/'s.yaml';path.write_text(yaml.safe_dump(cfg),encoding='utf-8')
    r=engine.run(str(path),str(tmp_path/'out'),300)
    assert r['deployment_gate']['passed']
    a=joblib.load(tmp_path/'out'/'NONBINARY'/'candidate.joblib')
    row=predict_artifact(a,pd.DataFrame({'signal':[3.]}))['rows'][0]
    assert row['alert'] is None and row['quality_prediction'] is None and row['decision']!='STOP'
    if task=='regression':assert row['scenario_estimate']>1
    else:assert row['predicted_class']=='C' and row['benchmark_alert'] is None


def test_time_validation_refuses_unknown_order():
    with pytest.raises(ValueError,match='explicit timestamps'):
        engine.inner_cv({'task':'binary','split':{'method':'time'}},pd.Series([0,1]*20),None)
