import copy
import hashlib
import json

import numpy as np
import pytest

from mlng_ai.pilot import PilotEngine, press_features, write_json


@pytest.fixture
def installed(tmp_path):
    release = tmp_path/'release'
    write_json(release/'routes.json', {})
    write_json(release/'release.json', {'id':'test-release', 'processes':{'pressfit':{}},
               'routes_sha256':hashlib.sha256((release/'routes.json').read_bytes()).hexdigest()})
    pointer = {'release':str(release),'sha256':hashlib.sha256((release/'release.json').read_bytes()).hexdigest()}
    write_json(tmp_path/'A'/'pilot-active.json', pointer)
    write_json(tmp_path/'B'/'pilot-active.json', pointer)
    return PilotEngine(tmp_path/'A'), PilotEngine(tmp_path/'B')


def payload(i=0):
    return {'process':'pressfit','cycle_id':str(i),'lot_id':'L'+str(i//15),
            'context':{'machine_id':'M','part_number':'P','program':'v1'},
            'rows':[{'units':{'force':'N','displacement':'mm'},'force':np.linspace(0,80+(i%5),16).tolist(),
                     'displacement':np.linspace(0,8,16).tolist()}]}


def test_pressfit_rejects_wrong_units_return_stroke_and_nonfinite():
    row = payload()['rows'][0]
    assert press_features(row)['travel_mm'] == 8
    for change in ({'units':{'force':'kN','displacement':'mm'}},
                   {'displacement':list(reversed(row['displacement']))},
                   {'force':[float('nan')]*16}):
        with pytest.raises(ValueError):
            press_features({**row, **change})


def test_event_idempotency_and_tenant_isolation(installed):
    a,b = installed
    first = a.submit(payload())
    assert first['mode'] == 'needs_normal_reference'
    assert a.submit(payload()) == first
    altered = payload(); altered['rows'][0]['force'][1] += 2
    with pytest.raises(ValueError, match='different input'):
        a.submit(altered)
    with pytest.raises(ValueError, match='Unknown cycle'):
        b.feedback('0','pass','operator','inspection-1')
    with pytest.raises(ValueError, match='Independent inspection'):
        a.feedback('0','pass','operator','')
    assert b.status()['counts']['events'] == 0


def test_confirmed_normal_learning_is_explicit_grouped_and_local(installed):
    a,b = installed
    for i in range(45):
        a.submit(payload(i))
        a.feedback(str(i),'pass','inspector','inspection-'+str(i))
    assert a.predict('pressfit', payload()['rows'], payload()['context'])['rows'][0]['alert'] is None
    reference = a.calibrate(payload()['context'])
    assert not set(reference['training_events']) & set(reference['calibration_events'])
    assert {int(x)//15 for x in reference['training_events']}.isdisjoint({int(x)//15 for x in reference['calibration_events']})
    unusual = copy.deepcopy(payload(99)); unusual['rows'][0]['force'] = [x*10 for x in unusual['rows'][0]['force']]
    assert a.submit(unusual)['rows'][0]['alert'] is True
    assert b.predict('pressfit', unusual['rows'], unusual['context'])['mode'] == 'needs_normal_reference'
    assert a.status()['counts']['baselines'] == 1
    changed = {**unusual['context'],'program':'v2'}
    assert a.predict('pressfit', unusual['rows'], changed)['mode'] == 'needs_normal_reference'


def test_latest_inspection_overrides_previous_pass(installed):
    a,_ = installed
    for i in range(40):
        a.submit(payload(i)); a.feedback(str(i),'pass','inspector','ref')
    a.feedback('0','fail','inspector','reinspection')
    with pytest.raises(ValueError, match='40'):
        a.calibrate(payload()['context'])


def test_manifest_or_route_tampering_fails_closed(installed):
    a,_ = installed
    (a.release/'routes.json').write_text('{"unexpected":{}}')
    with pytest.raises(ValueError, match='registry checksum'):
        PilotEngine(a.state)


def test_service_uses_active_pilot(installed):
    import pandas as pd
    from mlng_ai.service import Service
    a,_ = installed
    result = Service(a.state).predict('pressfit', pd.DataFrame(payload()['rows']), {'context':payload()['context']})
    assert result['release'] == 'test-release'
    assert result['rows'][0]['quality_prediction'] is None


def test_demo_examples_never_calibrate_field_reference(installed):
    a,_ = installed
    for i in range(45):
        p = payload(i); p['demo'] = True
        a.submit(p); a.feedback(str(i),'pass','inspector','ref')
    with pytest.raises(ValueError, match='40'):
        a.calibrate(payload()['context'])


def test_http_auth_input_and_feedback(installed):
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.request
    import urllib.error
    a,_ = installed
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
    proc = subprocess.Popen([sys.executable,'-m','mlng_ai.pilot','serve','--state',str(a.state),'--port',str(port)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=.2) as response:
                    assert 'MILG'.encode() in response.read()
                break
            except (OSError, urllib.error.URLError): time.sleep(.1)
        else: pytest.fail('Pilot HTTP server failed to start')
        token = (a.state/'pilot-token.txt').read_text()
        def post(path, body, auth=token):
            req=urllib.request.Request(f'http://127.0.0.1:{port}/api/'+path,
                 data=json.dumps(body).encode(), headers={'Authorization':'Bearer '+auth,'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=10) as response: return json.load(response)
        with pytest.raises(urllib.error.HTTPError) as exc: post('status',{},'bad')
        assert exc.value.code == 401
        assert post('cycle',payload())['mode'] == 'needs_normal_reference'
        assert post('feedback',{'event_id':'0','outcome':'unknown','actor':'operator','evidence':''})['training_performed'] is False
        assert post('status',{})['counts']['events'] == 1
    finally:
        proc.terminate(); proc.communicate(timeout=10)


def test_field_quality_training_promotes_only_inspected_local_model(installed):
    a,b = installed
    with pytest.raises(ValueError, match='100'):
        a.train_field('pressfit',payload()['context'])
    for i in range(200):
        p = payload(i); p['lot_id']='Q'+str(i//20)
        failed = i%20 >= 10
        if failed:
            p['rows'][0]['force'] = [v*10 for v in p['rows'][0]['force']]
        a.submit(p); a.feedback(str(i),'fail' if failed else 'pass','inspector','independent-'+str(i))
    report = a.train_field('pressfit',payload()['context'])
    assert report['installed'] is True
    train,val,test = map(set,report['split_lots'])
    assert not train & val and not train & test and not val & test
    result = a.predict('pressfit',payload()['rows'],payload()['context'])
    assert result['mode'] == 'tenant_quality_candidate'
    assert b.predict('pressfit',payload()['rows'],payload()['context'])['mode'] == 'needs_normal_reference'
    repeated = a.train_field('pressfit',payload()['context'])
    assert repeated['installed'] is True
    assert repeated['previous_comparison']['test']['nonregression'] is True
    a.feedback('0','fail','inspector','inspection-correction')
    result = a.predict('pressfit',payload()['rows'],payload()['context'])
    assert result['field_model_status'] == 'invalidated_by_inspection_revision'
    assert result['rows'][0]['alert'] is None


def test_mct_raw_contract_matches_training_summary():
    from mlng_ai.pilot import mct_waveform
    from mlng_ai.stiehl import _stats
    channels = {'force_x':'N','force_y':'N','force_z':'N','torque_spindle':'N*m'}
    row = {'phase':'cutting','units':channels,'signals':{k:list(range(16)) for k in channels}}
    features = mct_waveform(row)
    assert features['force_x_p2p'] == _stats(np.arange(16),'force_x')['force_x_p2p']
    with pytest.raises(ValueError, match='cutting'):
        mct_waveform({**row,'phase':'idle'})


def test_engineering_limits_are_separate_from_normal_model(installed):
    a,_ = installed
    p = payload(); p['rows'][0]['engineering_limits']={'peak_force_N':[10,20]}
    result = a.submit(p)['rows'][0]
    assert result['engineering_checks']['peak_force_N']['within'] is False
    assert result['alert'] is None and result['quality_prediction'] is None
    p['cycle_id']='invalid'; p['rows'][0]['engineering_limits']={'peak_force_N':[20,10]}
    with pytest.raises(ValueError, match='increasing pairs'):
        a.submit(p)


def test_field_gate_rejects_uninformative_features(installed):
    a,_ = installed
    for i in range(100):
        p=payload();p['cycle_id']=str(i);p['lot_id']='G'+str(i//20)
        a.submit(p);a.feedback(str(i),'pass' if i%2 else 'fail','inspector','ref-'+str(i))
    report=a.train_field('pressfit',payload()['context'])
    assert report['installed'] is False
    assert a.status()['counts']['field_models'] == 0
