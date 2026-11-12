import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from test_pilot import installed, payload
from mlng_ai.learning import LearningManager, Busy, exclusive, DEFAULTS
from mlng_ai.pilot import PilotEngine, context_key, write_json
from mlng_ai.delivery import backup, restore


def seed(engine, start=0, count=100, close=True, demo=False, reverse=False):
    manager=LearningManager(engine)
    for i in range(start,start+count):
        sample=payload(i);sample['lot_id']='Q'+str(i//20)
        sample['observed_at']=(datetime(2020,1,1,tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat()
        sample['demo']=demo
        failed=i%20>=10
        if failed:sample['rows'][0]['force']=[v*10 for v in sample['rows'][0]['force']]
        engine.submit(sample)
        engine.feedback(str(i),'fail' if failed != reverse else 'pass','quality','inspection-'+str(i),source_id='inspection-'+str(i))
    if close and not demo:
        for lot in range(start//20,(start+count)//20):
            manager.close_lot('pressfit',payload()['context'],'Q'+str(lot),'MES-adapter')


def setup(engine):
    manager=LearningManager(engine)
    manager.configure('pressfit',payload()['context'])
    return manager


def expire_cooldown(engine):
    with engine.db() as db:db.execute("UPDATE learning_runs SET created='2000-01-01T00:00:00+00:00'")


def test_automatic_learning_gates_fresh_lots_and_isolation(installed):
    a,b=installed;manager=setup(a)
    seed(a,close=False)
    assert manager.tick()['policies'][0]['status']=='waiting'
    for i in range(5):manager.close_lot('pressfit',payload()['context'],'Q'+str(i),'MES')
    assert manager.tick()['policies'][0]['status']=='activated'
    first=manager.status()['runs'][0]
    assert 'milg' in first['details']['report']['validation']
    assert a.predict('pressfit',payload()['rows'],payload()['context'])['field_model_id']
    assert b.status()['counts']['field_models']==0
    expire_cooldown(a)
    assert 'fresh chronological' in manager.tick()['policies'][0]['reason']
    seed(a,100,40)
    assert manager.tick()['policies'][0]['status']=='activated'
    runs=manager.status()['runs']
    assert len(runs)==2
    newest=runs[0]['details']['report']
    assert set(newest['splits']['test']).isdisjoint(first['details']['report']['splits']['test'])
    assert newest['previous_comparison']['test']['nonregression']
    previous=manager.rollback('pressfit',payload()['context'],'MILG-engineer')
    assert previous['restored']==first['details']['report']['id']
    assert PilotEngine(a.state).predict('pressfit',payload()['rows'],payload()['context'])['field_model_id']==previous['restored']


def test_demo_data_and_missing_labels_cannot_train_or_close(installed):
    a,_=installed;manager=setup(a)
    seed(a,demo=True)
    with pytest.raises(ValueError,match='non-demo'):manager.close_lot('pressfit',payload()['context'],'Q0','MES')
    assert manager.tick()['policies'][0]['status']=='waiting'
    a.submit(payload(999))
    with pytest.raises(ValueError,match='ISO timestamp'):manager.close_lot('pressfit',payload()['context'],payload(999)['lot_id'],'MES')
    assert a.status()['counts']['field_models']==0


def test_closed_lot_immutable_but_exact_retry_allowed(installed):
    a,_=installed;seed(a,count=20)
    with a.db() as db:sample=json.loads(db.execute("SELECT request FROM events WHERE id='0'").fetchone()[0])
    assert a.submit(sample)['event_id']=='0'
    sample['cycle_id']='extra'
    with pytest.raises(ValueError,match='closed'):a.submit(sample)


def test_inspection_idempotency_and_revision_identity(installed):
    a,_=installed;a.submit(payload())
    original=a.feedback('0','pass','QA','inspect-1',source_id='QA-1')
    repeat=a.feedback('0','pass','QA','inspect-1',source_id='QA-1')
    assert original['feedback_id']==repeat['feedback_id']
    with pytest.raises(ValueError,match='revision ID'):a.feedback('0','fail','QA','inspect-1',source_id='QA-1')
    assert a.status()['counts']['feedback']==1


def test_lock_excludes_second_process_and_releases_after_exception(installed):
    import subprocess,sys
    a,_=installed
    with exclusive(a.state):
        run=subprocess.run([sys.executable,'-m','mlng_ai.learning','tick','--state',str(a.state)],capture_output=True,text=True)
        assert run.returncode==0 and json.loads(run.stdout)['status']=='busy'
        with pytest.raises(Busy):LearningManager(a).train_manual('pressfit',payload()['context'])
    with exclusive(a.state):pass


def test_changed_inspection_during_fit_prevents_activation(installed,monkeypatch):
    a,_=installed;manager=setup(a);seed(a)
    original=a._train_field_candidate
    def change(*args,**kwargs):
        report=original(*args,**kwargs)
        a.feedback('0','fail','QA','correction')
        return report
    monkeypatch.setattr(a,'_train_field_candidate',change)
    result=manager.tick()['policies'][0]
    assert result['status']=='held'
    assert manager.status()['runs'][0]['details']['report']['activation_checks']['labels_unchanged'] is False
    assert a.status()['counts']['field_models']==0


def test_policy_pause_during_fit_prevents_activation(installed,monkeypatch):
    a,_=installed;manager=setup(a);seed(a)
    original=a._train_field_candidate
    def pause(*args,**kwargs):
        report=original(*args,**kwargs)
        manager.configure('pressfit',payload()['context'],{'enabled':False})
        return report
    monkeypatch.setattr(a,'_train_field_candidate',pause)
    assert manager.tick()['policies'][0]['status']=='held'
    assert a.status()['counts']['field_models']==0


def test_revision_and_corruption_automatically_revoke_active_model(installed):
    a,_=installed;manager=setup(a);seed(a)
    assert manager.tick()['policies'][0]['status']=='activated'
    a.feedback('0','unknown','QA','revision')
    result=manager.tick()['policies'][0]
    assert result['status']=='rolled_back' and result['monitor']['restored'] is None
    assert a.predict('pressfit',payload()['rows'],payload()['context'])['mode']=='needs_normal_reference'


def test_post_activation_labelled_drift_triggers_rollback(installed):
    a,_=installed;manager=setup(a);seed(a)
    assert manager.tick()['policies'][0]['status']=='activated'
    with a.db() as db:db.execute("UPDATE field_models SET created='2020-01-01T00:01:39+00:00'")
    seed(a,100,100,reverse=True)
    result=manager.tick()['policies'][0]
    assert result['status']=='rolled_back'
    assert result['monitor']['metrics']['auc']<.2
    assert result['monitor']['restored'] is None


def test_normal_reference_only_mode_does_not_claim_quality(installed):
    a,_=installed;manager=LearningManager(a)
    manager.configure('pressfit',payload()['context'],{'mode':'normal_reference'})
    for i in range(60):
        sample=payload(i);sample['lot_id']='Q'+str(i//20)
        sample['observed_at']=(datetime(2020,1,1,tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat()
        a.submit(sample);a.feedback(str(i),'pass','QA','inspection')
    for i in range(3):manager.close_lot('pressfit',payload()['context'],'Q'+str(i),'MES')
    assert manager.tick()['policies'][0]['status']=='normal_reference_created'
    assert manager.tick()['policies'][0]['status']=='normal_reference_ready'
    result=a.predict('pressfit',payload()['rows'],payload()['context'])
    assert result['mode']=='normal_reference' and result['rows'][0]['quality_prediction'] is None
    assert a.status()['counts']['field_models']==0


def test_interrupted_run_does_not_reuse_consumed_test_lots(installed,monkeypatch):
    a,_=installed;manager=setup(a);seed(a)
    def failed(*args,**kwargs):raise RuntimeError('simulated interruption')
    monkeypatch.setattr(a,'_train_field_candidate',failed)
    assert manager.tick()['policies'][0]['status']=='error'
    with a.db() as db:db.execute("UPDATE learning_runs SET status='running',created='2000-01-01T00:00:00+00:00'")
    result=manager.tick()['policies'][0]
    assert 'fresh chronological' in result['reason']
    assert manager.status()['runs'][0]['status']=='interrupted'


def test_inbox_recovers_dependencies_idempotently(installed):
    a,_=installed;manager=LearningManager(a)
    inbox=a.state/'inbox';inbox.mkdir()
    write_json(inbox/'a-feedback.ready.json',dict(format='milg_inbox_v1',kind='feedback',items=[
        dict(event_id='0',outcome='pass',actor='QA',evidence='independent',source_id='inspection-0')]))
    write_json(inbox/'b-cycles.ready.json',dict(format='milg_inbox_v1',kind='cycles',items=[payload()]))
    assert manager.tick()['inbox'][0]['status']=='rejected'
    assert manager.tick()['inbox'][0]['status']=='complete'
    assert manager.tick()['inbox']==[]
    assert a.status()['counts']['events']==1 and a.status()['counts']['feedback']==1


def test_policy_cannot_weaken_gates_or_implicitly_change_context(installed):
    a,_=installed;manager=LearningManager(a)
    for cfg in ({'max_fpr':.8},{'min_rows':10},{'enabled':'true'},{'min_auc':float('nan')}):
        with pytest.raises(ValueError):manager.configure('pressfit',payload()['context'],cfg)
    manager.configure('pressfit',{**payload()['context'],'program':'v2'})
    seed(a)
    assert manager.tick()['policies'][0]['status']=='waiting'


def test_overlapping_lots_rejected(installed):
    a,_=installed;seed(a,count=40)
    with a.db() as db:
        raw=json.loads(db.execute("SELECT request FROM events WHERE id='0'").fetchone()[0])
        raw['observed_at']='2020-01-01T00:00:25.5+00:00'
        db.execute('UPDATE events SET request=? WHERE id=?',(json.dumps(raw),'0'))
    with pytest.raises(ValueError,match='overlap'):LearningManager(a).entries(context_key(payload()['context']),'pressfit')


def test_backup_preserves_activation_history_and_policy(installed,tmp_path):
    a,_=installed;manager=setup(a);seed(a)
    assert manager.tick()['policies'][0]['status']=='activated'
    source=backup(a.state,tmp_path/'backup')
    recovered=PilotEngine(restore(source,tmp_path/'recovered'))
    assert LearningManager(recovered).status()['active']==manager.status()['active']
    assert LearningManager(recovered).status()['policies'][0]['enabled'] is False
    assert recovered.predict('pressfit',payload()['rows'],payload()['context'])['field_model_id']
