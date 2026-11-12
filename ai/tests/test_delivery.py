"""Portable recovery, privacy boundaries and no-write CSV validation."""
import copy
import csv
import json
from pathlib import Path

import joblib
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from mlng_ai.connector import make_template,prepare,run
from mlng_ai.delivery import acceptance,backup,restore,snapshot,install_snapshot,inspect_release
from mlng_ai.pilot import PilotEngine,write_json
from mlng_ai.routing import Router,digest


@pytest.fixture
def numeric_state(tmp_path):
    state=tmp_path/'source';release=state/'pilot-releases'/'one';release.mkdir(parents=True)
    x=pd.DataFrame({'force':[-2.,-1.,1.,2.]})
    model=LogisticRegression().fit(x,[0,0,1,1])
    artifact={'format':'milg_scenario_v1','task':'binary','model':model,'columns':['force'],
        'context_columns':[],'threshold':.5,'target':'scenario_quality_score','scenario':'TEST',
        'evaluation_gate':{'passed':True,'research_only':False},'source_license':'MIT',
        'template':{'process':'test'},'engine_version':'test','input_stage':'completed_unit_features'}
    path=tmp_path/'model.joblib';joblib.dump(artifact,path)
    Router(release).register('test_numeric',path,'scenario','test-owner')
    write_json(release/'release.json',{'id':'test-release','processes':{'test_numeric':{
        'target':'scenario_quality_score','input_units':{'force':'N'},'alert_enabled':False}},
        'routes_sha256':digest(release/'routes.json')})
    write_json(release/'examples'/'test_numeric.json',{'process':'test_numeric','cycle_id':'EXAMPLE',
        'lot_id':'LOT','demo':True,'context':{'machine_id':'M','part_number':'P','program':'v1'},'rows':[{'force':2.}]})
    write_json(state/'pilot-active.json',{'release':str(release),'sha256':digest(release/'release.json')})
    (state/'pilot-token.txt').write_text('PRIVATE-TOKEN')
    return state


def test_portable_snapshot_has_no_tenant_events_tokens_or_private_models(numeric_state,tmp_path):
    engine=PilotEngine(numeric_state);engine.submit(engine.example('test_numeric'))
    private=numeric_state/'field_models'/'private.joblib';private.parent.mkdir();private.write_text('PRIVATE')
    snap=snapshot(numeric_state,tmp_path/'snap')
    assert not list(snap.rglob('*.sqlite3')) and not list(snap.rglob('*token*'))
    assert not list(snap.rglob('private.joblib'))
    installed=install_snapshot(snap,tmp_path/'other');other=PilotEngine(installed)
    assert other.status()['counts']['events']==0
    other.submit(other.example('test_numeric'))
    with pytest.raises(ValueError,match='must not exist'):install_snapshot(snap,installed)


def test_backup_restores_events_reviews_inspection_and_rotates_token(numeric_state,tmp_path):
    engine=PilotEngine(numeric_state);sample=engine.example('test_numeric');engine.submit(sample)
    engine.review(sample['cycle_id'],'investigate','operator')
    engine.feedback(sample['cycle_id'],'pass','inspector','inspection-001')
    write_json(numeric_state/'inbox'/'pending.ready.json',{'format':'milg_inbox_v1','kind':'cycles','items':[]})
    # A tenant-local immutable model must be recovered as well.
    field=numeric_state/'field'/'model.joblib';field.parent.mkdir();field.write_bytes(b'trusted-test-model')
    with engine.db() as db:db.execute('INSERT INTO field_models VALUES(?,?,?,?,?,?)',
        ('one','today','scope','test_numeric','field/model.joblib',digest(field)))
    source=backup(numeric_state,tmp_path/'backup');dest=restore(source,tmp_path/'restored')
    recovered=PilotEngine(dest)
    assert recovered.status()['counts']==engine.status()['counts']
    assert recovered.submit(sample)==engine.submit(sample)
    assert (dest/'field'/'model.joblib').read_bytes()==field.read_bytes()
    assert not (dest/'pilot-token.txt').exists()
    assert (dest/'inbox'/'pending.ready.json').read_bytes()==(numeric_state/'inbox'/'pending.ready.json').read_bytes()
    with pytest.raises(ValueError,match='must not exist'):restore(source,dest)


def test_corrupt_model_and_backup_fail_before_install(numeric_state,tmp_path):
    snap=snapshot(numeric_state,tmp_path/'snap')
    example=snap/'examples'/'test_numeric.json';example.write_text('{}')
    with pytest.raises(ValueError,match='checksum'):install_snapshot(snap,tmp_path/'new')
    assert not (tmp_path/'new').exists()
    PilotEngine(numeric_state)
    saved=backup(numeric_state,tmp_path/'backup');(saved/'pilot.sqlite3').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):restore(saved,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


def test_full_acceptance_isolated_from_source(numeric_state,tmp_path):
    before=PilotEngine(numeric_state).status()['counts']
    result=acceptance(numeric_state,tmp_path/'report',repeats=2)
    assert result['passed'] and result['checks']['backup_restore']
    assert PilotEngine(numeric_state).status()['counts']==before
    assert (tmp_path/'report'/'acceptance.html').exists()


def test_csv_validation_conversion_and_resumable_submission(numeric_state,tmp_path):
    folder=make_template(numeric_state,'test_numeric',tmp_path/'csv')
    mapping=folder/'mapping.json';cfg=json.loads(mapping.read_text())
    cfg['features']['force'].update(source_unit='kN',engine_unit='N',scale=1000)
    write_json(mapping,cfg)
    prepared=prepare(numeric_state,folder/'cycles.csv',mapping)
    assert prepared[0]['rows'][0]['force']==2000
    report=run(numeric_state,folder/'cycles.csv',mapping,tmp_path/'valid.json')
    assert report['submitted']==0 and PilotEngine(numeric_state).status()['counts']['events']==0
    run(numeric_state,folder/'cycles.csv',mapping,tmp_path/'sent.json',True)
    run(numeric_state,folder/'cycles.csv',mapping,tmp_path/'retry.json',True)
    assert PilotEngine(numeric_state).status()['counts']['events']==1


@pytest.mark.parametrize('bad',['units','timestamp','duplicate','missing','release'])
def test_csv_errors_never_partially_submit(numeric_state,tmp_path,bad):
    folder=make_template(numeric_state,'test_numeric',tmp_path/'csv')
    mapping=folder/'mapping.json';cfg=json.loads(mapping.read_text())
    with (folder/'cycles.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    second=copy.deepcopy(rows[0]);second['cycle_id']='SECOND';second['observed_at']='2026-11-12T00:01:00+00:00';rows.append(second)
    if bad=='units':cfg['features']['force']['engine_unit']='kN'
    elif bad=='timestamp':second['observed_at']='2026-11-12T00:01:00'
    elif bad=='duplicate':second['cycle_id']=rows[0]['cycle_id']
    elif bad=='missing':second['force']=''
    else:cfg['template_version']='old-release'
    write_json(mapping,cfg)
    with (folder/'cycles.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    with pytest.raises(ValueError):run(numeric_state,folder/'cycles.csv',mapping,tmp_path/'result.json',True)
    assert PilotEngine(numeric_state).status()['counts']['events']==0
