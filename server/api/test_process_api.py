from test_ai_api import client  # shared isolated tenant fixture

def test_process_template_endpoint(client):
    c,_=client
    row={'machine_id':'M1','tool_id':'T','installation_id':'I',
         'part_number':'P','screw_type':'S','program':'P',
         'tightening steps':[{'name':'Thread forming','graph':
            {'torque values':[1,2,3],'angle values':[0,1,2]}}]}
    r=c.post('/ai/v1/analyze-process',json={'rows':[row]})
    assert r.status_code==200,r.text
    assert r.json()['template']['id']=='screw_tightening'
    assert r.json()['quality_prediction'] is None
    p=c.post('/ai/v1/predict',json={'process':'auto','rows':[row]})
    assert p.status_code==200,p.text
    assert p.json()['mode']=='template_analysis_only'
    bad=c.post('/ai/v1/analyze-process',json={'process':'mct','rows':[row]})
    assert bad.json()['mode']=='needs_confirmation'

def test_field_workflow_and_human_feedback_are_tenant_local(client):
    from pathlib import Path
    c,d=client
    row={'machine_id':'M1','tool_id':'T','installation_id':'I',
         'part_number':'P','screw_type':'S','program':'P',
         'tightening steps':[{'name':'Thread forming','graph':
            {'torque values':[1,2,3],'angle values':[0,1,2]}}]}
    r=c.post('/ai/v1/workflow',json={'process':'auto','rows':[row]})
    assert r.status_code==200,r.text
    event=r.json();assert event['recognition']['family']=='screw'
    assert event['prediction']['mode']=='template_analysis_only'
    assert event['raw_input_saved'] is False
    event_file=Path(d,'tenants','public','events',event['event_id']+'.json')
    saved=event_file.read_text(encoding='utf-8')
    assert 'torque values' not in saved and event['input_sha256']
    fb={'event_id':event['event_id'],'action':'investigate','outcome':'unknown',
        'actor':'조립 담당자','note':'체결 이력 확인'}
    assert c.post('/ai/v1/feedback',json=fb).status_code==200
    record=Path(d,'tenants','public','feedback.jsonl').read_text(encoding='utf-8')
    assert '조립 담당자' in record and 'investigate' in record
    assert c.post('/ai/v1/feedback',json={**fb,'event_id':'0'*32}).status_code==400
    assert c.post('/ai/v1/feedback',json={**fb,'action':'retrain'}).status_code==400
