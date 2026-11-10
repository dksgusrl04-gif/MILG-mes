import json
import numpy as np
import pandas as pd
import pytest
from mlng_ai.model import CumulativeModel
from mlng_ai.routing import Router
from mlng_ai.service import Service
from mlng_ai.features import SLOTS

def bundle(tmp_path):
    m = CumulativeModel(SLOTS)
    m.add_process('mct_hmotp',.1)
    m.add_process('mct_nasa',.4)
    path = tmp_path/'mct.json'
    path.write_text(json.dumps({'model':m.to_dict()}),encoding='utf-8')
    return path

def test_service_routes_and_preserves_legacy(tmp_path):
    source = bundle(tmp_path); state = tmp_path/'tenant'
    r = Router(state)
    r.register('mct_hmotp',source,'mct','tester')
    r.register('mct_nasa',source,'mct','tester')
    s = Service(state)
    x = pd.DataFrame([dict.fromkeys(SLOTS,0.)])
    a,b = s.predict('mct_hmotp',x),s.predict('mct_nasa',x)
    assert a['mode'] == 'registered_model'
    assert a['rows'][0]['p'] != b['rows'][0]['p']
    assert r.predict('unknown',x) is None
    assert len(s.status()['model_routes']) == 2
    with pytest.raises(ValueError,match='exact calibrated'): s.predict('mct_hmotp',pd.DataFrame({'force':[1]}))
    with pytest.raises(ValueError,match='specialist'): s.update('mct_hmotp',x)
    # Source training cannot mutate the frozen deployed bundle.
    source.write_text('{}',encoding='utf-8')
    assert s.predict('mct_hmotp',x)['rows'] == a['rows']
    fresh = Service(state)
    assert fresh.predict('mct_hmotp',x)['rows'] == a['rows']
    r.rollback()
    assert 'mct_nasa' not in r.routes() and 'mct_hmotp' in r.routes()

def test_checksum_and_tenant_isolation(tmp_path):
    src=bundle(tmp_path); a=Router(tmp_path/'A'); b=Router(tmp_path/'B')
    entry=a.register('mct_hmotp',src,'mct','test')
    assert b.routes() == {}
    (a.directory/entry['model']).write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='checksum'): a.predict('mct_hmotp',pd.DataFrame([dict.fromkeys(SLOTS,0.)]))

def test_causal_preprocessing_exact_and_no_label_leak(tmp_path):
    m=CumulativeModel(['force','force_relative_to_tolerance']);m.add_process('causalman_pressfit',.2)
    src=tmp_path/'model.json';m.save(src)
    recipe={'columns':m.slots,'raw_columns':['force'],'synthetic':True,
        'mean':dict.fromkeys(m.slots,0.),'std':dict.fromkeys(m.slots,1.),'clip':[-8,8]}
    pp=tmp_path/'preprocessing.json';pp.write_text(json.dumps(recipe),encoding='utf-8')
    r=Router(tmp_path/'tenant');r.register('causalman_pressfit',src,'causalman','test',pp)
    frame=pd.DataFrame({'force':[5.],'force_LTL':[0.],'force_UTL':[10.],'ProcessResult':[0]})
    result=r.predict('causalman_pressfit',frame)
    assert result['synthetic'] is True
    frame.ProcessResult=1
    assert r.predict('causalman_pressfit',frame)['rows'] == result['rows']
    frame.force_UTL=0
    with pytest.raises(ValueError,match='width'): r.predict('causalman_pressfit',frame)
    with pytest.raises(ValueError):r.register('pressfit',src,'causalman','test',pp)
