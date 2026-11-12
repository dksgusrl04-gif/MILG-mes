"""Tenant-local, closed-LOT learning controller. Never uploads data or updates base models.

Policies and activation pointers live in the tenant SQLite DB. Immutable candidate
artifacts are committed before a transactional compare-and-swap activation. A file
lock serializes workers across processes and is released by the OS after a crash.
"""
import argparse
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


DEFAULTS = dict(enabled=True, mode='quality', min_rows=100, min_lots=5,
                min_new_rows=40, min_new_lots=2, min_auc=.65, min_recall=.5,
                max_fpr=.1, interval_seconds=300, monitor_rows=100,
                monitor_lots=3, monitor_min_pass=20, monitor_min_fail=10)


class Busy(RuntimeError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def parse_time(value):
    if not isinstance(value, str): raise ValueError('observed_at requires an ISO timestamp with timezone')
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.utcoffset() is None: raise ValueError('observed_at requires a timezone')
    return value.astimezone(timezone.utc)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS learning_policies(scope TEXT,process TEXT,config TEXT NOT NULL,
        updated TEXT NOT NULL, PRIMARY KEY(scope,process));
      CREATE TABLE IF NOT EXISTS learning_lots(scope TEXT,process TEXT,lot TEXT,closed TEXT NOT NULL,
        actor TEXT NOT NULL, PRIMARY KEY(scope,process,lot));
      CREATE TABLE IF NOT EXISTS learning_heads(scope TEXT,process TEXT,model_id TEXT,
        updated TEXT NOT NULL, PRIMARY KEY(scope,process));
      CREATE TABLE IF NOT EXISTS learning_runs(id TEXT PRIMARY KEY,scope TEXT,process TEXT,
        signature TEXT,created TEXT,finished TEXT,status TEXT,details TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS learning_audit(id INTEGER PRIMARY KEY,created TEXT,scope TEXT,
        process TEXT,action TEXT,details TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS learning_quarantine(model_id TEXT PRIMARY KEY,reason TEXT,created TEXT);
      CREATE TABLE IF NOT EXISTS learning_health(id INTEGER PRIMARY KEY CHECK(id=1),updated TEXT,details TEXT);
      CREATE TABLE IF NOT EXISTS feedback_receipts(source_id TEXT PRIMARY KEY,hash TEXT,feedback_id INTEGER);
      CREATE TABLE IF NOT EXISTS learning_inbox(hash TEXT PRIMARY KEY,name TEXT,updated TEXT,result TEXT);
      CREATE INDEX IF NOT EXISTS learning_run_scope ON learning_runs(scope,process,created);
      CREATE INDEX IF NOT EXISTS event_scope ON events(scope,process,lot);
      CREATE INDEX IF NOT EXISTS feedback_event ON feedback(event,id);
    ''')


def active_entry(db, scope, process):
    head = db.execute('SELECT model_id FROM learning_heads WHERE scope=? AND process=?', (scope,process)).fetchone()
    if head is not None:
        return db.execute('SELECT id,path,sha256 FROM field_models WHERE id=?', (head[0],)).fetchone() if head[0] else None
    # Backward compatible read-only selection until the first controlled activation.
    return db.execute('SELECT id,path,sha256 FROM field_models WHERE scope=? AND process=? ORDER BY created DESC,id DESC LIMIT 1',
                      (scope,process)).fetchone()


@contextmanager
def exclusive(state):
    path = Path(state)/'learning.lock'
    with path.open('a+b') as handle:
        handle.seek(0,2)
        if handle.tell() == 0: handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Busy('A learning/rollback operation is already running for this tenant') from exc
        try: yield
        finally:
            handle.seek(0)
            if os.name == 'nt': msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(handle, fcntl.LOCK_UN)


class LearningManager:
    def __init__(self, engine):
        self.engine = engine

    def audit(self, db, scope, process, action, details):
        db.execute('INSERT INTO learning_audit(created,scope,process,action,details) VALUES(?,?,?,?,?)',
                   (stamp(),scope,process,action,encoded(details)))

    def configure(self, process, context, settings=None, actor='local-administrator'):
        from .pilot import context_key
        scope = context_key(context)
        if process not in self.engine.manifest['processes']: raise ValueError('Unknown process')
        supplied = settings or {}
        if set(supplied)-set(DEFAULTS): raise ValueError('Unknown learning policy setting')
        config = {**DEFAULTS, **supplied}
        if not isinstance(config['enabled'],bool): raise ValueError('enabled must be boolean')
        if config['mode'] not in ('quality','normal_reference'): raise ValueError('Invalid learning mode')
        if config['mode']=='normal_reference' and process!='pressfit': raise ValueError('Normal-reference mode requires pressfit')
        for key in ('min_rows','min_lots','min_new_rows','min_new_lots','interval_seconds',
                    'monitor_rows','monitor_lots','monitor_min_pass','monitor_min_fail'):
            if type(config[key]) is not int or not DEFAULTS[key] <= config[key] <= 1000000:
                raise ValueError(key+' must be an integer at least '+str(DEFAULTS[key]))
        for key in ('min_auc','min_recall','max_fpr'):
            if type(config[key]) not in (int,float) or not np.isfinite(config[key]) or not 0 < config[key] <= 1:
                raise ValueError('Invalid performance gate: '+key)
        if config['min_auc']<.65 or config['min_recall']<.5 or config['max_fpr']>.1:
            raise ValueError('Do not weaken minimum pilot screening gates')
        if not isinstance(actor,str) or not actor.strip(): raise ValueError('actor required')
        with self.engine.db() as db:
            db.execute('INSERT OR REPLACE INTO learning_policies VALUES(?,?,?,?)',(scope,process,encoded(config),stamp()))
            self.audit(db,scope,process,'configure',dict(actor=actor,config=config))
        return dict(process=process, context=context, **config)

    def close_lot(self, process, context, lot_id, actor):
        from .pilot import context_key
        scope = context_key(context)
        if not isinstance(actor,str) or not actor.strip() or len(actor)>100: raise ValueError('actor required')
        with self.engine.db() as db:
            db.execute('BEGIN IMMEDIATE')
            records = db.execute('''SELECT e.request,f.outcome FROM events e LEFT JOIN feedback f
                ON f.event=e.id AND f.id=(SELECT MAX(id) FROM feedback WHERE event=e.id)
                WHERE e.scope=? AND e.process=? AND e.lot=?''',(scope,process,lot_id)).fetchall()
            records = [(json.loads(r),o) for r,o in records if not json.loads(r).get('demo',False)]
            if not records: raise ValueError('No non-demo cycles in this LOT')
            for request,outcome in records:
                if parse_time(request.get('observed_at'))>datetime.now(timezone.utc):
                    raise ValueError('Cannot close LOT with future acquisition timestamps')
                if outcome not in ('pass','fail'): raise ValueError('Close LOT only after every cycle has an independent pass/fail inspection')
            db.execute('INSERT OR IGNORE INTO learning_lots VALUES(?,?,?,?,?)',(scope,process,lot_id,stamp(),actor))
            self.audit(db,scope,process,'close_lot',dict(lot=lot_id,actor=actor,rows=len(records)))
        return dict(lot_id=lot_id,closed=True,rows=len(records))

    def entries(self, scope, process, closed=True):
        with self.engine.db() as db:
            records = db.execute('''SELECT e.id,e.lot,e.request,f.outcome,f.id,e.created FROM events e
                JOIN feedback f ON f.event=e.id AND f.id=(SELECT MAX(id) FROM feedback WHERE event=e.id)
                WHERE e.scope=? AND e.process=? ORDER BY e.created,e.id''',(scope,process)).fetchall()
            lots = {r[0] for r in db.execute('SELECT lot FROM learning_lots WHERE scope=? AND process=?',(scope,process))}
        records = [r for r in records if not json.loads(r[2]).get('demo',False) and (not closed or r[1] in lots)]
        if closed and any(r[3] not in ('pass','fail') for r in records):
            raise ValueError('Closed LOT inspection was revised to unknown; resolve inspection first')
        records = [r for r in records if r[3] in ('pass','fail')]
        records.sort(key=lambda r:(parse_time(json.loads(r[2]).get('observed_at',r[5])),r[0]))
        # Interleaved LOTs do not provide a chronological group holdout.
        groups=[]
        for r in records:
            if not groups or groups[-1]!=r[1]: groups.append(r[1])
        if len(groups)!=len(set(groups)): raise ValueError('LOT acquisition intervals overlap; chronological holdout unavailable')
        return records, groups

    def load(self, entry):
        import joblib
        path = (self.engine.state/entry[1]).resolve()
        if not path.is_relative_to(self.engine.state.resolve()): raise ValueError('Field artifact outside tenant state')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry[2]: raise ValueError('Field artifact checksum mismatch')
        model=joblib.load(path)
        if (model.get('release', self.engine.manifest['id'])!=self.engine.manifest['id'] or
            model.get('release_sha256',self.engine.release_sha256)!=self.engine.release_sha256):
            raise ValueError('Field model belongs to a different release')
        return model

    def outcomes(self, db):
        return dict(db.execute('SELECT event,outcome FROM feedback WHERE id IN (SELECT MAX(id) FROM feedback GROUP BY event)'))

    def valid(self, model, outcomes):
        return all(outcomes.get(k)==v for k,v in model['inspection_outcomes'].items())

    def _head(self, scope, process):
        with self.engine.db() as db: return active_entry(db,scope,process)

    def rollback(self, process, context, actor, reason='manual rollback', automatic=False, monitor=None, policy=None):
        from .pilot import context_key
        scope=context_key(context)
        with exclusive(self.engine.state):
            return self._rollback(scope,process,actor,reason,automatic,monitor,policy)

    def _rollback(self, scope, process, actor, reason, automatic=False, monitor=None, policy=None):
        if not isinstance(actor,str) or not actor.strip(): raise ValueError('actor required')
        current=self._head(scope,process)
        if not current: return dict(restored=None,reason='No field model active')
        with self.engine.db() as db:
            outcomes=self.outcomes(db)
            versions=db.execute('''SELECT id,path,sha256 FROM field_models WHERE scope=? AND process=?
                AND id NOT IN (SELECT model_id FROM learning_quarantine) ORDER BY created DESC,id DESC''',(scope,process)).fetchall()
        target=None
        for entry in versions:
            if entry[0]==current[0]: continue
            try:
                model=self.load(entry)
                if not self.valid(model,outcomes): continue
                if monitor and not self.passes(self.evaluate(model,process,monitor),policy): continue
                target=entry;break
            except (ValueError,OSError,KeyError): continue
        with self.engine.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if active_entry(db,scope,process)!=current: raise ValueError('Active model changed during rollback')
            if target and not self.valid(self.load(target),self.outcomes(db)):
                target=None
            db.execute('INSERT OR REPLACE INTO learning_quarantine VALUES(?,?,?)',(current[0],reason,stamp()))
            db.execute('INSERT OR REPLACE INTO learning_heads VALUES(?,?,?,?)',(scope,process,target[0] if target else None,stamp()))
            self.audit(db,scope,process,'automatic_rollback' if automatic else 'rollback',
                       dict(actor=actor,previous=current[0],restored=target[0] if target else None,reason=reason))
        return dict(previous=current[0],restored=target[0] if target else None,reason=reason,
                    fallback='previous_field_model' if target else 'public_model_or_normal_reference')

    @staticmethod
    def passes(metric, policy):
        return (metric['auc'] is not None and metric['auc']>=policy['min_auc'] and
                metric['recall'] is not None and metric['recall']>=policy['min_recall'] and
                metric['false_positive_rate'] is not None and metric['false_positive_rate']<=policy['max_fpr'])

    def evaluate(self, model, process, records):
        from .adapters import standardize
        from .evaluate_processes import metrics
        frame=self.engine._feature_frame(process,[json.loads(r[2])['rows'][0] for r in records])
        if not np.isfinite(frame.to_numpy(float)).all(): raise ValueError('Non-finite monitoring sensors')
        score=model['model'].predict_proba(standardize(frame,model['preprocessing']))[:,1]
        return metrics(np.array([int(r[3]=='fail') for r in records]),score,model['threshold'])

    def monitor(self, scope, process, policy):
        entry=self._head(scope,process)
        if not entry:return dict(status='no_field_model')
        with self.engine.db() as db: outcomes=self.outcomes(db)
        try:
            model=self.load(entry)
            if not self.valid(model,outcomes): raise ValueError('Independent inspection labels revised')
        except (OSError,ValueError,KeyError) as exc:
            return self._rollback(scope,process,'automatic-monitor',str(exc),True)
        records,lots=self.entries(scope,process)
        # Score only work produced AFTER activation, with inspection labels now available.
        with self.engine.db() as db:
            activated=db.execute('SELECT created FROM field_models WHERE id=?',(entry[0],)).fetchone()[0]
        later=[r for r in records if r[0] not in model['inspection_outcomes'] and
               parse_time(json.loads(r[2])['observed_at'])>parse_time(activated)]
        recent_lots=list(dict.fromkeys(r[1] for r in later))[-max(policy['monitor_lots'],5):]
        later=[r for r in later if r[1] in recent_lots]
        normal=sum(r[3]=='pass' for r in later);failed=sum(r[3]=='fail' for r in later)
        if (len(later)<policy['monitor_rows'] or len(recent_lots)<policy['monitor_lots'] or
            normal<policy['monitor_min_pass'] or failed<policy['monitor_min_fail']):
            return dict(status='waiting_for_independent_monitoring',rows=len(later),normal=normal,failed=failed)
        metric=self.evaluate(model,process,later)
        if not self.passes(metric,policy):
            return dict(metrics=metric,**self._rollback(scope,process,'automatic-monitor',
                        'Post-activation closed-LOT performance gate failed',True,later,policy))
        return dict(status='within_monitoring_gates',model_id=entry[0],metrics=metric)

    def _plan(self, scope, process, policy, records, lots):
        if len(records)<policy['min_rows'] or len(lots)<policy['min_lots']:
            raise ValueError('Field quality training needs >=%d inspected cycles across >=%d lots in the same context' % (policy['min_rows'],policy['min_lots']))
        with self.engine.db() as db:
            past=db.execute('SELECT details FROM learning_runs WHERE scope=? AND process=? ORDER BY created',(scope,process)).fetchall()
        used=set();cutoff=None
        for (raw,) in past:
            detail=json.loads(raw)
            used.update(detail.get('reserved_lots',[]))
            t=detail.get('max_observed_at')
            if t and (cutoff is None or parse_time(t)>parse_time(cutoff)):cutoff=t
        if used:
            fresh=[lot for lot in lots if lot not in used and all(parse_time(json.loads(r[2]).get('observed_at',r[5]))>parse_time(cutoff)
                         for r in records if r[1]==lot)]
            fresh_rows=[r for r in records if r[1] in fresh]
            if len(fresh)<policy['min_new_lots'] or len(fresh_rows)<policy['min_new_rows']:
                raise ValueError('Waiting for fresh chronological LOTs: >=%d LOTs and >=%d cycles; previous test data cannot be reused' % (policy['min_new_lots'],policy['min_new_rows']))
            midpoint=max(1,len(fresh)//2)
            # Late data from before the last evaluation may train, but never become a new test.
            splits=[[lot for lot in lots if lot not in fresh],fresh[:midpoint],fresh[midpoint:]]
        else:
            a,b=max(1,int(.6*len(lots))),max(2,int(.8*len(lots)))
            splits=[lots[:a],lots[a:b],lots[b:]]
        for split,minimum in zip(splits,(10,5,5)):
            y=[r[3] for r in records if r[1] in split]
            if any(y.count(outcome)<minimum for outcome in ('pass','fail')):
                raise ValueError('Each split needs both outcomes: train >=10/class, validation and test >=5/class')
        return splits

    def _train(self, process, context, policy, closed=True):
        from .pilot import context_key, write_json
        scope=context_key(context)
        records,lots=self.entries(scope,process,closed)
        splits=self._plan(scope,process,policy,records,lots)
        # Audit snapshot identifiers and consume evaluation LOTs BEFORE training.
        signature=fingerprint([[(r[0],r[4]) for r in records],policy,self.engine.manifest['id']])
        before=self._head(scope,process)
        run_id=uuid.uuid4().hex
        details=dict(reserved_lots=lots,max_observed_at=max(parse_time(json.loads(r[2]).get('observed_at',r[5])) for r in records).isoformat(),
                     splits=splits,rows=len(records),release=self.engine.manifest['id'],
                     previous_model=before[0] if before else None)
        with self.engine.db() as db:
            db.execute('INSERT INTO learning_runs VALUES(?,?,?,?,?,?,?,?)',
                (run_id,scope,process,signature,stamp(),None,'running',encoded(details)))
        try:
            report=self.engine._train_field_candidate(process,context,[r[:4] for r in records],splits,
                {k:policy[k] for k in ('min_auc','min_recall','max_fpr')})
            candidate=self.engine.state/'field-candidates'/report['id']/'model.joblib'
            checksum=hashlib.sha256(candidate.read_bytes()).hexdigest()
            # Durable artifacts exist before their DB pointer becomes visible.
            with self.engine.db() as db:
                db.execute('BEGIN IMMEDIATE')
                current_versions=dict(db.execute('SELECT event,MAX(id) FROM feedback GROUP BY event'))
                unchanged=all(current_versions.get(r[0])==r[4] for r in records)
                current_policy=db.execute('SELECT config FROM learning_policies WHERE scope=? AND process=?',(scope,process)).fetchone()
                policy_same=not closed or (current_policy and json.loads(current_policy[0])==policy)
                pointer=json.loads(self.engine.pointer.read_text(encoding='utf-8'))
                release_same=(Path(pointer['release']).resolve()==self.engine.release.resolve() and
                              pointer['sha256']==self.engine.release_sha256==hashlib.sha256((self.engine.release/'release.json').read_bytes()).hexdigest())
                install=bool(report['gate_passed'] and unchanged and policy_same and release_same and active_entry(db,scope,process)==before)
                report.update(installed=install,run_id=run_id,activation_checks=dict(labels_unchanged=unchanged,
                              policy_unchanged=bool(policy_same),release_unchanged=release_same))
                if install:
                    db.execute('INSERT INTO field_models VALUES(?,?,?,?,?,?)',(report['id'],stamp(),scope,process,
                               candidate.relative_to(self.engine.state).as_posix(),checksum))
                    db.execute('INSERT OR REPLACE INTO learning_heads VALUES(?,?,?,?)',(scope,process,report['id'],stamp()))
                    self.audit(db,scope,process,'activate',dict(model_id=report['id'],previous_model=details['previous_model'],run_id=run_id))
                details['report']=report
                db.execute('UPDATE learning_runs SET status=?,finished=?,details=? WHERE id=?',
                           ('activated' if install else 'held',stamp(),encoded(details),run_id))
            write_json(candidate.parent/'report.json',report)
            return report
        except Exception as exc:
            details['error']=str(exc)
            with self.engine.db() as db:
                db.execute('UPDATE learning_runs SET status=?,finished=?,details=? WHERE id=?',('failed',stamp(),encoded(details),run_id))
            raise

    def train_manual(self, process, context):
        # Same lock, artifacts and non-reused holdouts as the automated worker.
        with exclusive(self.engine.state):
            return self._train(process,context,DEFAULTS,closed=False)

    def tick(self):
        with exclusive(self.engine.state):
            from .learning_inbox import ingest
            inbox=ingest(self.engine)
            with self.engine.db() as db:
                db.execute("UPDATE learning_runs SET status='interrupted',finished=? WHERE status='running'",(stamp(),))
                policies=db.execute('SELECT scope,process,config FROM learning_policies ORDER BY scope,process').fetchall()
            results=[]
            for scope,process,raw in policies:
                policy=json.loads(raw)
                context=dict(zip(('machine_id','part_number','program'),json.loads(scope)))
                item=dict(process=process,context=context,status='paused')
                try:
                    item['monitor']=self.monitor(scope,process,policy)
                    if item['monitor'].get('previous'):
                        item['status']='rolled_back'
                        results.append(item);continue
                    if not policy['enabled']:
                        results.append(item);continue
                    with self.engine.db() as db:
                        last=db.execute('SELECT created FROM learning_runs WHERE scope=? AND process=? ORDER BY created DESC LIMIT 1',(scope,process)).fetchone()
                    if last and time.time()-parse_time(last[0]).timestamp()<policy['interval_seconds']:
                        item['status']='cooldown'
                    elif policy['mode']=='normal_reference':
                        records,lots=self.entries(scope,process)
                        with self.engine.db() as db:
                            baseline=db.execute('SELECT artifact FROM baselines WHERE scope=? ORDER BY created DESC LIMIT 1',(scope,)).fetchone()
                        if baseline:
                            artifact=json.loads(baseline[0]);current={r[0]:r[3] for r in records}
                            valid=all(current.get(k)=='pass' for k in artifact['training_events']+artifact['calibration_events'])
                            if valid:
                                item['status']='normal_reference_ready'
                                results.append(item);continue
                        reference=self.engine.calibrate(context,entries=[r[:3] for r in records if r[3]=='pass'])
                        with self.engine.db() as db:self.audit(db,scope,process,'calibrate',dict(baseline_id=reference['baseline_id']))
                        item.update(status='normal_reference_created',baseline_id=reference['baseline_id'])
                    else:
                        report=self._train(process,context,policy)
                        item.update(status='activated' if report['installed'] else 'held',model_id=report['id'])
                except ValueError as exc: item.update(status='waiting',reason=str(exc))
                except Exception as exc: item.update(status='error',reason=str(exc))
                results.append(item)
            result=dict(updated=stamp(),policies=results,inbox=inbox)
            if policies:
                from .delivery import backup
                folder=self.engine.state/'backups'/datetime.now(timezone.utc).strftime('%Y%m%d')
                if not (folder/'backup.json').exists():
                    if folder.exists(): folder=folder.with_name(folder.name+'-'+uuid.uuid4().hex[:8])
                    try:
                        backup(self.engine.state,folder)
                        result['backup']=dict(status='created',path=str(folder))
                    except Exception as exc: result['backup']=dict(status='error',reason=str(exc))
                else:result['backup']=dict(status='daily_backup_exists',path=str(folder))
            with self.engine.db() as db:db.execute('INSERT OR REPLACE INTO learning_health VALUES(1,?,?)',(stamp(),encoded(result)))
            return result

    def status(self):
        with self.engine.db() as db:
            policies=[dict(process=p,context=dict(zip(('machine_id','part_number','program'),json.loads(s))),**json.loads(c))
                      for s,p,c in db.execute('SELECT scope,process,config FROM learning_policies')]
            health=db.execute('SELECT updated,details FROM learning_health WHERE id=1').fetchone()
            runs=[dict(id=i,process=p,created=c,finished=f,status=s,details=json.loads(d)) for i,p,c,f,s,d in
                  db.execute('SELECT id,process,created,finished,status,details FROM learning_runs ORDER BY created DESC LIMIT 20')]
            audit=[dict(created=c,process=p,action=a,details=json.loads(d)) for c,p,a,d in
                   db.execute('SELECT created,process,action,details FROM learning_audit ORDER BY id DESC LIMIT 30')]
            heads=[dict(scope=s,process=p,model_id=m) for s,p,m in db.execute('SELECT scope,process,model_id FROM learning_heads')]
        age=time.time()-parse_time(health[0]).timestamp() if health else None
        supervisor=None
        try:
            supervisor=json.loads((self.engine.state/'learning-supervisor.json').read_text(encoding='utf-8'))
        except (OSError,ValueError): pass
        supervised_recent=bool(supervisor and not supervisor.get('stopping') and not supervisor.get('error') and
            time.time()-parse_time(supervisor['updated']).timestamp()<180)
        return dict(policies=policies,last_check=json.loads(health[1]) if health else None,
                    worker_recent=supervised_recent or (age is not None and age<180),heartbeat_age_seconds=age,
                    supervisor=supervisor,runs=runs,audit=audit,active=heads)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['tick','status','configure','close-lot','rollback'])
    p.add_argument('--state',required=True);p.add_argument('--input')
    a=p.parse_args()
    from .pilot import PilotEngine
    manager=LearningManager(PilotEngine(a.state))
    body=json.loads(Path(a.input).read_text(encoding='utf-8-sig')) if a.input else {}
    try:
        if a.command=='tick': result=manager.tick()
        elif a.command=='status': result=manager.status()
        elif a.command=='configure': result=manager.configure(**body)
        elif a.command=='close-lot': result=manager.close_lot(**body)
        else:result=manager.rollback(**body)
    except Busy as exc: result=dict(status='busy',reason=str(exc))
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
