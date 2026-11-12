"""Local pilot runtime: versioned public models and tenant-local press-fit references.

No machine control. Optional tenant-local learning uses inspected, closed LOTs.
"""
import argparse
import hashlib
import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from .routing import Router


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def press_features(row):
    if row.get('units') != {'force': 'N', 'displacement': 'mm'}:
        raise ValueError('Press-fit units must be force=N, displacement=mm')
    x = np.asarray(row.get('displacement', []), dtype=float)
    y = np.asarray(row.get('force', []), dtype=float)
    if x.ndim != 1 or y.ndim != 1 or len(x) != len(y) or not 8 <= len(x) <= 100000:
        raise ValueError('Force/displacement must be aligned arrays, 8..100000 points')
    if not np.isfinite(x).all() or not np.isfinite(y).all() or np.any(np.diff(x) <= 0):
        raise ValueError('Supply finite, strictly increasing insertion displacement; remove return stroke')
    span = float(x[-1] - x[0])
    grid = np.linspace(x[0], x[-1], 32)
    shape = np.interp(grid, x, y)
    result = {'travel_mm': span, 'peak_force_N': float(y.max()), 'end_force_N': float(y[-1]),
              'work_Nmm': float(np.trapezoid(y, x)), 'slope_N_mm': float((y[-1]-y[0])/span)}
    result.update({f'shape_{i:02}': float(v) for i, v in enumerate(shape)})
    return result


def mct_waveform(row):
    from .stiehl import _stats
    if row.get('phase') != 'cutting':
        raise ValueError('MCT waveform must be an explicitly segmented cutting phase')
    required = {'force_x':'N','force_y':'N','force_z':'N','torque_spindle':'N*m'}
    if row.get('units') != required:
        raise ValueError('MCT waveform units must be force_x/y/z=N, torque_spindle=N*m')
    result = {}
    for channel in required:
        values = np.asarray(row.get('signals', {}).get(channel, []), dtype=float)
        if values.ndim != 1 or not 8 <= len(values) <= 100000 or not np.isfinite(values).all():
            raise ValueError('Each MCT channel needs 8..100000 finite cutting samples')
        result.update(_stats(values, channel))
    return result


def context_key(context):
    keys = ('machine_id', 'part_number', 'program')
    if not isinstance(context, dict) or any(not isinstance(context.get(k), str) or not context[k].strip() for k in keys):
        raise ValueError('context requires machine_id, part_number and versioned program strings')
    return json.dumps([context[k] for k in keys], ensure_ascii=False)


class PilotEngine:
    def __init__(self, state):
        self.state = Path(state)
        self.pointer = self.state / 'pilot-active.json'
        if not self.pointer.exists():
            raise ValueError('Pilot release is not installed; run Prepare-MilgPilot.ps1')
        pointer = json.loads(self.pointer.read_text(encoding='utf-8'))
        self.release = Path(pointer['release'])
        raw = (self.release / 'release.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != pointer['sha256']:
            raise ValueError('Pilot release manifest checksum mismatch')
        self.manifest = json.loads(raw)
        self.release_sha256 = hashlib.sha256(raw).hexdigest()
        if hashlib.sha256((self.release/'routes.json').read_bytes()).hexdigest() != self.manifest['routes_sha256']:
            raise ValueError('Pilot route registry checksum mismatch')
        self.router = Router(self.release)
        self.dbpath = self.state / 'pilot.sqlite3'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                  created TEXT NOT NULL, process TEXT NOT NULL, scope TEXT NOT NULL,
                  lot TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, event TEXT NOT NULL,
                  created TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, note TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY, event TEXT NOT NULL,
                  created TEXT NOT NULL, actor TEXT NOT NULL, outcome TEXT NOT NULL,
                  evidence TEXT NOT NULL, note TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS baselines(id TEXT PRIMARY KEY, created TEXT NOT NULL,
                  scope TEXT NOT NULL, artifact TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS field_models(id TEXT PRIMARY KEY, created TEXT NOT NULL,
                  scope TEXT NOT NULL, process TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL);
            ''')
            from .learning import initialize
            initialize(db)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.dbpath, timeout=30)
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            with connection:
                yield connection
        finally:
            connection.close()

    def status(self):
        with self.db() as db:
            counts = {table: db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                      for table in ('events', 'feedback', 'baselines', 'field_models', 'reviews')}
        return {'release': self.manifest['id'], 'mode': 'pilot',
                'processes': self.manifest['processes'], 'counts': counts,
                'field_validated': False, 'state': str(self.state)}

    def example(self, process):
        # Registry lookup prevents arbitrary file/path access through this API.
        if process not in self.manifest['processes']:
            raise ValueError('Unsupported pilot process')
        import copy
        sample = json.loads((self.release/'examples'/(process+'.json')).read_text(encoding='utf-8'))
        sample = copy.deepcopy(sample)
        sample.update(demo=True, cycle_id='demo-'+uuid.uuid4().hex[:16])
        return sample

    def contract(self, process):
        if process not in self.manifest['processes']:
            raise ValueError('Unsupported pilot process')
        route = self.router.routes().get(process, {})
        capability = self.manifest['processes'][process]
        return {'process':process,'release':self.manifest['id'],
                'input_contract':route.get('input_contract','pressfit_force_displacement_N_mm_v1'),
                'required_features':route.get('feature_columns',[]),
                'input_units':capability.get('input_units',{}),
                'capability':capability,'field_validated':False}

    def history(self, limit=50):
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1 <= limit <= 200:
            raise ValueError('History limit must be an integer in 1..200')
        with self.db() as db:
            rows=db.execute('''SELECT r.id,r.created,r.event,e.lot,e.process,r.actor,r.action,r.note
                FROM reviews r JOIN events e ON e.id=r.event ORDER BY r.id DESC LIMIT ?''',(limit,)).fetchall()
        keys=('review_id','created','event_id','lot_id','process','actor','action','note')
        return {'reviews':[dict(zip(keys,row)) for row in rows], 'training_performed':False}

    def predict(self, process, rows, context=None):
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValueError('rows must contain 1..100 records')
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError('Every row must be a JSON object')
        if process == 'auto':
            frame = pd.DataFrame(rows)
            from .adapters import supports
            eligible = [p for p, r in self.router.routes().items()
                        if self.manifest['processes'][p].get('auto_route', True)
                        and supports(r['input_contract'], frame, r.get('feature_columns', []))]
            if all('force' in r and 'displacement' in r for r in rows):
                eligible.append('pressfit')
            if all('signals' in r for r in rows) and 'mct_stiehl' in self.manifest['processes']:
                eligible.append('mct_stiehl')
            if len(eligible) != 1:
                return {'mode': 'needs_confirmation', 'candidates': eligible, 'rows': []}
            process = eligible[0]
        if process not in self.manifest['processes']:
            raise ValueError('Unsupported pilot process: '+str(process))
        if process == 'pressfit':
            scope = context_key(context)
            with self.db() as db:
                found = db.execute('SELECT id,artifact FROM baselines WHERE scope=? ORDER BY created DESC LIMIT 1', (scope,)).fetchone()
            baseline = json.loads(found[1]) if found else None
            baseline_status = 'ready' if baseline else 'missing'
            if baseline:
                used = set(baseline['training_events'] + baseline['calibration_events'])
                with self.db() as db:
                    latest = dict(db.execute('SELECT event,outcome FROM feedback WHERE id IN (SELECT MAX(id) FROM feedback GROUP BY event)').fetchall())
                if any(latest.get(event) != 'pass' for event in used):
                    baseline = None
                    baseline_status = 'invalidated_by_inspection_revision'
            output = []
            for i, row in enumerate(rows):
                features = press_features(row)
                limits = row.get('engineering_limits', {})
                if not isinstance(limits, dict):
                    raise ValueError('engineering_limits must map feature names to [lower,upper]')
                checks = {}
                for feature, bounds in limits.items():
                    if feature not in ('travel_mm','peak_force_N','end_force_N','work_Nmm','slope_N_mm'):
                        raise ValueError('Unknown engineering limit feature')
                    pair = np.asarray(bounds, dtype=float)
                    if pair.shape != (2,) or not np.isfinite(pair).all() or pair[0] >= pair[1]:
                        raise ValueError('Engineering limits must be finite increasing pairs')
                    checks[feature] = {'value':features[feature], 'bounds':pair.tolist(),
                                       'within':bool(pair[0] <= features[feature] <= pair[1])}
                score, alert, top = None, None, []
                if baseline:
                    columns = baseline['columns']
                    z = (np.array([features[k] for k in columns])-baseline['center'])/baseline['scale']
                    score = float(np.sqrt(np.mean(z*z)))
                    alert = score > baseline['threshold']
                    top = [{'feature': columns[j], 'value': features[columns[j]], 'deviation': float(z[j])}
                           for j in np.argsort(-np.abs(z))[:4]]
                output.append({'i': i, 'features': features, 'anomaly_score': score, 'alert': alert,
                               'engineering_checks':checks,
                               'quality_prediction': None, 'top': top})
            result = {'process': process, 'mode': 'normal_reference' if baseline else 'needs_normal_reference',
                      'baseline_id': found[0] if found else None, 'rows': output,
                      'baseline_status': baseline_status,
                      'input_contract': 'pressfit_force_displacement_N_mm_v1',
                      'target': 'deviation_from_confirmed_normal_not_final_quality'}
        else:
            if process == 'mct_stiehl' and any('signals' in row for row in rows):
                if not all('signals' in row for row in rows):
                    raise ValueError('Do not mix waveform and summary inputs in one request')
                rows = [mct_waveform(row) for row in rows]
            frame = pd.DataFrame(rows)
            route = self.router.routes()[process]
            from .adapters import input_features
            features = frame if route['kind']=='scenario' else input_features(route['input_contract'], frame, route['feature_columns'])
            # Missing whole channels must not silently become a confident prediction.
            if route['input_contract'] == 'mct_sensor_columns_v1' and not np.isfinite(features.to_numpy()).all():
                raise ValueError('MCT pilot requires finite values for every contracted sensor feature')
            result = self.router.predict(process, frame)
            capability = self.manifest['processes'][process]
            from .adapters import REGRESSION_TARGETS
            continuous = result.get('target') in (REGRESSION_TARGETS - {'preload_kN'})
            result['decision_status'] = result.get('decision_status') or ('benchmark_estimate' if continuous else
                'experimental_alert' if capability.get('alert_enabled', False) else 'needs_threshold_validation')
            for row in result['rows']:
                row['quality_prediction'] = None
                row['benchmark_alert'] = row.get('benchmark_alert',row.get('alert'))
                if not capability.get('alert_enabled', False):
                    row['alert'] = None
            result['probability_note'] = ('Public benchmark estimate in source target units' if continuous else
                'Dataset classifier score; not calibrated to this factory or independent joint strength')
            if result.get('target') == 'ddacs_sim_min_thickness_mm':
                result['probability_note'] = 'Synthetic FEM surrogate: OP10 minimum thickness after springback in mm'
            if result.get('target') == 'press_pump_anomaly_score':
                result['probability_note'] = 'Pump anomaly score; higher means farther from the learned normal reference'
        result.update(release=self.manifest['id'], field_validated=False,
                      capability=self.manifest['processes'][process])
        if context is not None:
            self._field_prediction(process, rows, context, result)
        return result

    def _feature_frame(self, process, rows):
        if process == 'pressfit':
            return pd.DataFrame([press_features(r) for r in rows])
        if process == 'mct_stiehl' and all('signals' in r for r in rows):
            rows = [mct_waveform(r) for r in rows]
        from .adapters import input_features
        route = self.router.routes()[process]
        if route['kind']=='scenario':
            import joblib
            from .scenario_runtime import frame_for
            return frame_for(joblib.load(self.release/route['model']),pd.DataFrame(rows))[0]
        return input_features(route['input_contract'], pd.DataFrame(rows), route['feature_columns'])

    def _field_prediction(self, process, rows, context, result):
        import joblib
        from .adapters import standardize, _input_evidence
        with self.db() as db:
            from .learning import active_entry
            entry = active_entry(db, context_key(context), process)
            latest = dict(db.execute('SELECT event,outcome FROM feedback WHERE id IN (SELECT MAX(id) FROM feedback GROUP BY event)').fetchall()) if entry else {}
        if not entry:
            return
        modelpath = self.state/entry[1]
        if hashlib.sha256(modelpath.read_bytes()).hexdigest() != entry[2]:
            raise ValueError('Field model checksum mismatch')
        artifact = joblib.load(modelpath)
        if any(latest.get(k) != v for k,v in artifact['inspection_outcomes'].items()):
            result['field_model_status'] = 'invalidated_by_inspection_revision'
            for row in result['rows']:
                row['alert'] = None
            return
        features = self._feature_frame(process, rows)
        if not np.isfinite(features.to_numpy(float)).all():
            raise ValueError('Field inference requires every contracted sensor value to be finite')
        if (artifact.get('release', self.manifest['id']) != self.manifest['id'] or
            artifact.get('release_sha256',self.release_sha256)!=self.release_sha256):
            result['field_model_status'] = 'release_changed_revalidation_required'
            for row in result['rows']: row['alert'] = None
            return
        if list(features) != artifact['preprocessing']['columns']:
            raise ValueError('Field feature contract changed; retraining required')
        X = standardize(features, artifact['preprocessing'])
        scores = artifact['model'].predict_proba(X)[:,1]
        evidence = _input_evidence(features, X, artifact['model'])
        result['reference_rows'] = result['rows']
        result['rows'] = [{'i': i, 'score':float(score), 'alert':bool(score>artifact['threshold']),
                           'quality_prediction':None, 'top':evidence[i]} for i,score in enumerate(scores)]
        result.update(mode='tenant_quality_candidate', target='independent_inspection_failure',
                      field_model_id=entry[0], field_model_status='pilot_validated',
                      decision_status='inspection_support',
                      probability_note='Classifier score, not a calibrated probability or automatic acceptance decision')

    def train_field(self, process, context):
        from .learning import LearningManager
        return LearningManager(self).train_manual(process, context)

    def _train_field_candidate(self, process, context, entries=None, split_lots=None, gates=None):
        """Learn an inspected, tenant-local model; promote only after preset checks."""
        import joblib
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.linear_model import LogisticRegression
        from threadpoolctl import threadpool_limits
        from .strict_evaluation import TrainTransform, threshold_on_validation
        from .evaluate_processes import metrics
        scope = context_key(context)
        if process not in self.manifest['processes']:
            raise ValueError('Unknown process')
        if entries is None:
            with self.db() as db:
                entries = db.execute('''SELECT e.id,e.lot,e.request,f.outcome FROM events e JOIN feedback f ON f.event=e.id
                WHERE e.scope=? AND e.process=? AND f.outcome IN ('pass','fail')
                AND f.id=(SELECT MAX(id) FROM feedback WHERE event=e.id) ORDER BY e.created,e.id''', (scope,process)).fetchall()
        entries = [e for e in entries if not json.loads(e[2]).get('demo',False)]
        lots = list(dict.fromkeys(e[1] for e in entries))
        if len(entries) < 100 or len(lots) < 5:
            raise ValueError('Field quality training needs >=100 inspected cycles across >=5 lots in the same context')
        a,b = max(1,int(.6*len(lots))), max(2,int(.8*len(lots)))
        sets = [set(lots[:a]),set(lots[a:b]),set(lots[b:])] if split_lots is None else [set(s) for s in split_lots]
        if any(sets[i] & sets[j] for i,j in ((0,1),(0,2),(1,2))) or set.union(*sets) != set(lots):
            raise ValueError('Invalid disjoint LOT split')
        masks = [np.array([e[1] in s for e in entries]) for s in sets]
        y = np.array([int(e[3]=='fail') for e in entries])
        for mask,minimum in zip(masks,(10,5,5)):
            if any(int(np.sum(y[mask]==value)) < minimum for value in (0,1)):
                raise ValueError('Each split needs both outcomes: train >=10/class, validation and test >=5/class')
        rows = [json.loads(e[2])['rows'][0] for e in entries]
        frame = self._feature_frame(process, rows)
        if not np.isfinite(frame.to_numpy(float)).all():
            raise ValueError('Field training requires finite contracted features')
        tr,val,test = masks
        transform = TrainTransform().fit(frame.loc[tr])
        X = [transform.transform(frame.loc[m]) for m in masks]
        from milg_engine.milg_core import MilgEngineClassifier
        models = {'milg':MilgEngineClassifier(), 'linear':LogisticRegression(max_iter=2000,random_state=42),
                  'nonlinear':HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=7,min_samples_leaf=10,
                                l2_regularization=2,early_stopping=False,random_state=42)}
        validation = {}
        gates = gates or {'min_auc':.65,'min_recall':.5,'max_fpr':.1}
        with threadpool_limits(limits=4):
            for key, model in models.items():
                model.fit(X[0],y[tr])
                score = model.predict_proba(X[1])[:,1]
                threshold = threshold_on_validation(y[val],score,min(.05,gates['max_fpr']))
                validation[key] = metrics(y[val],score,threshold)
        eligible = [k for k,m in validation.items() if m['auc']>=gates['min_auc'] and
                    m['recall']>=gates['min_recall'] and m['false_positive_rate']<=gates['max_fpr']]
        selected = max(eligible or validation,key=lambda k:(validation[k]['recall'],validation[k]['auc']))
        threshold = validation[selected]['threshold']
        evaluation = metrics(y[test],models[selected].predict_proba(X[2])[:,1],threshold)
        # Predeclared pilot screening gates; not a customer acceptance guarantee.
        passed = all(m['auc'] >= gates['min_auc'] and m['recall'] >= gates['min_recall'] and m['false_positive_rate'] <= gates['max_fpr']
                     for m in (validation[selected],evaluation))
        previous_comparison = None
        with self.db() as db:
            from .learning import active_entry
            active = active_entry(db, scope, process)
            previous = (active[1],active[2]) if active else None
        if previous:
            from .adapters import standardize
            previous_path = self.state/previous[0]
            if hashlib.sha256(previous_path.read_bytes()).hexdigest() != previous[1]:
                raise ValueError('Previous field model checksum mismatch')
            old = joblib.load(previous_path)
            current_outcomes = {e[0]:e[3] for e in entries}
            if all(current_outcomes.get(k) == v for k,v in old['inspection_outcomes'].items()):
                previous_comparison = {}
                for split, mask, new_metric in [('validation',val,validation[selected]), ('test',test,evaluation)]:
                    old_score = old['model'].predict_proba(standardize(frame.loc[mask],old['preprocessing']))[:,1]
                    old_metric = metrics(y[mask],old_score,old['threshold'])
                    retained = (new_metric['auc'] >= old_metric['auc']-.01 and
                                new_metric['recall'] >= old_metric['recall']-.03 and
                                new_metric['false_positive_rate'] <= old_metric['false_positive_rate']+.02)
                    previous_comparison[split] = {'previous':old_metric,'nonregression':retained}
                    passed = passed and retained
        identity = uuid.uuid4().hex
        folder = self.state/'field-candidates'/identity
        folder.mkdir(parents=True)
        report = {'id':identity,'process':process,'context':context,'selected':selected,
                  'validation':validation,'test':evaluation,'installed':False,'gate_passed':bool(passed),
                  'previous_comparison':previous_comparison,
                  'split_lots':[sorted(s) for s in sets], 'splits':{s:[e[0] for e,m in zip(entries,mask) if m]
                                  for s,mask in zip(('train','validation','test'), masks)},
                  'gates':gates,
                  'limitations':'Historical LOT holdout; repeated tuning requires a fresh external test set'}
        artifact = {'model':models[selected],'preprocessing':transform.export(),'threshold':threshold,
                    'inspection_outcomes':{e[0]:e[3] for e in entries},'process':process,'context':context,
                    'release':self.manifest['id'],'release_sha256':self.release_sha256}
        joblib.dump(artifact,folder/'model.joblib')
        write_json(folder/'report.json',report)
        return report

    def submit(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('Request must be a JSON object')
        event_id = payload.get('cycle_id')
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 160:
            raise ValueError('Unique cycle_id required (1..160 characters)')
        if not isinstance(payload.get('lot_id'), str) or not payload['lot_id'].strip():
            raise ValueError('lot_id required')
        if len(payload.get('rows', [])) != 1:
            raise ValueError('Submit one cycle per event so feedback labels one physical assembly')
        scope = context_key(payload.get('context'))
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
        request_hash = hashlib.sha256(serialized.encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT request_hash,result FROM events WHERE id=?', (event_id,)).fetchone()
            if existing:
                if existing[0] != request_hash:
                    raise ValueError('cycle_id already exists with different input')
                return json.loads(existing[1])
            if not isinstance(payload.get('demo', False), bool):
                raise ValueError('demo must be a boolean')
            if payload.get('observed_at') is not None:
                from .learning import parse_time
                parse_time(payload['observed_at'])
            if db.execute('SELECT 1 FROM learning_lots WHERE scope=? AND process=? AND lot=?',
                          (scope,payload.get('process'),payload['lot_id'])).fetchone():
                raise ValueError('LOT is closed; use a new LOT identifier for later operations')
            result = self.predict(payload.get('process', 'auto'), payload['rows'], payload['context'])
            if result['mode'] == 'needs_confirmation':
                return result
            if db.execute('SELECT 1 FROM learning_lots WHERE scope=? AND process=? AND lot=?',
                          (scope,result['process'],payload['lot_id'])).fetchone():
                raise ValueError('LOT is closed')
            result.update(event_id=event_id, created=now())
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?)',
                       (event_id, request_hash, result['created'], result['process'], scope, payload['lot_id'],
                        serialized, json.dumps(result, ensure_ascii=False, allow_nan=False)))
        return result

    def review(self,event_id,action,actor,note=''):
        if action not in ('investigate','hold') or not isinstance(actor,str) or not actor.strip() or len(actor)>100:
            raise ValueError('Valid review action and actor required')
        if not isinstance(note,str) or len(note)>2000:
            raise ValueError('Review note too long')
        with self.db() as db:
            if not db.execute('SELECT 1 FROM events WHERE id=?',(event_id,)).fetchone():
                raise ValueError('Unknown cycle_id in this tenant')
            cur=db.execute('INSERT INTO reviews(event,created,actor,action,note) VALUES(?,?,?,?,?)',
                           (event_id,now(),actor,action,note))
        return {'review_id':cur.lastrowid,'event_id':event_id,'action':action,'training_performed':False}

    def feedback(self, event_id, outcome, actor, evidence, note='', source_id=None):
        if outcome not in ('pass', 'fail', 'unknown') or not isinstance(actor, str) or not actor.strip():
            raise ValueError('outcome pass/fail/unknown and actor required')
        if not isinstance(evidence, str) or (outcome != 'unknown' and not evidence.strip()):
            raise ValueError('Independent inspection reference required for pass/fail')
        if len(actor) > 100 or len(evidence) > 1000 or not isinstance(note, str) or len(note) > 2000:
            raise ValueError('Feedback text too long')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if source_id is not None:
                if not isinstance(source_id,str) or not 1<=len(source_id)<=200: raise ValueError('Invalid feedback source_id')
                from .learning import fingerprint
                checksum=fingerprint([event_id,outcome,actor,evidence,note])
                previous=db.execute('SELECT hash,feedback_id FROM feedback_receipts WHERE source_id=?',(source_id,)).fetchone()
                if previous:
                    if previous[0]!=checksum: raise ValueError('Inspection source_id already has different content; use a new revision ID')
                    return {'feedback_id':previous[1],'event_id':event_id,'training_performed':False,'duplicate':True}
            if not db.execute('SELECT 1 FROM events WHERE id=?', (event_id,)).fetchone():
                raise ValueError('Unknown cycle_id in this tenant')
            cur = db.execute('INSERT INTO feedback(event,created,actor,outcome,evidence,note) VALUES(?,?,?,?,?,?)',
                            (event_id, now(), actor, outcome, evidence, note))
            if source_id is not None:db.execute('INSERT INTO feedback_receipts VALUES(?,?,?)',(source_id,checksum,cur.lastrowid))
        return {'feedback_id': cur.lastrowid, 'event_id': event_id, 'training_performed': False}

    def calibrate(self, context, entries=None):
        scope = context_key(context)
        if entries is None:
            with self.db() as db:
                entries = db.execute('''SELECT e.id,e.lot,e.request FROM events e JOIN feedback f ON f.event=e.id
                WHERE e.scope=? AND e.process='pressfit' AND f.outcome='pass'
                AND f.id=(SELECT MAX(g.id) FROM feedback g WHERE g.event=e.id) ORDER BY e.created,e.id''', (scope,)).fetchall()
        entries = [e for e in entries if not json.loads(e[2]).get('demo', False)]
        lots = list(dict.fromkeys(e[1] for e in entries))
        if len(entries) < 40 or len(lots) < 3:
            raise ValueError('Need >=40 independently confirmed normal cycles across >=3 lots for this context')
        validation_lots = set(lots[max(1, int(len(lots)*.7)):])
        features = pd.DataFrame([press_features(json.loads(e[2])['rows'][0]) for e in entries])
        validation = np.array([e[1] in validation_lots for e in entries])
        if validation.sum() < 10 or (~validation).sum() < 20:
            raise ValueError('Need >=20 train and >=10 calibration cycles in separate lots')
        train = features.loc[~validation].to_numpy()
        center = np.median(train, axis=0)
        scale = np.maximum(np.quantile(train,.75,axis=0)-np.quantile(train,.25,axis=0), np.maximum(abs(center)*.01, 1e-6))
        scores = np.sqrt(np.mean(((features.loc[validation].to_numpy()-center)/scale)**2, axis=1))
        threshold = float(np.quantile(scores,.95, method='higher'))
        artifact = {'columns': list(features), 'center': center.tolist(), 'scale': scale.tolist(),
                    'threshold': threshold, 'training_events': [e[0] for e,v in zip(entries, validation) if not v],
                    'calibration_events': [e[0] for e,v in zip(entries, validation) if v],
                    'normal_calibration_alert_rate': float(np.mean(scores>threshold)),
                    'sensitivity': None, 'independent_test': False, 'context': context}
        identity = uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = dict(db.execute('SELECT event,outcome FROM feedback WHERE id IN (SELECT MAX(id) FROM feedback GROUP BY event)'))
            if any(latest.get(e[0]) != 'pass' for e in entries):
                raise ValueError('Inspection changed during normal-reference calibration')
            db.execute('INSERT INTO baselines VALUES(?,?,?,?)', (identity, now(), scope, json.dumps(artifact)))
        return {'baseline_id': identity, **artifact}


def serve(state, port, allowed_origins=(), auto_learn=False):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlsplit, unquote
    import mimetypes
    import re
    engine = PilotEngine(state)
    token_path = Path(state)/'pilot-token.txt'
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(32), encoding='utf-8')
    token = token_path.read_text(encoding='utf-8').strip()
    admin_path = Path(state)/'pilot-admin-token.txt'
    if not admin_path.exists(): admin_path.write_text(secrets.token_urlsafe(32), encoding='utf-8')
    admin_token = admin_path.read_text(encoding='utf-8').strip()
    from .learning import LearningManager, Busy
    learning = LearningManager(engine)
    page = (Path(__file__).parent/'pilot_ui.html').read_bytes()
    demo_page = (Path(__file__).parent/'demo_ui.html').read_bytes()
    widget = (Path(__file__).resolve().parents[2]/'web'/'milg-widget.js').read_bytes()
    web_root = Path(__file__).resolve().parents[2]/'web'

    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, data, content_type='application/json; charset=utf-8'):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            origin=self.headers.get('Origin')
            if origin in allowed_origins:
                self.send_header('Access-Control-Allow-Origin',origin)
                self.send_header('Vary','Origin')
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            if self.headers.get('Origin') not in allowed_origins:
                return self.respond(403,{'error':'Origin is not allowed'})
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
            self.send_header('Vary','Origin')
            self.send_header('Access-Control-Allow-Methods','POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type')
            self.send_header('Content-Length','0')
            self.end_headers()

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path == '/mes':
                self.send_response(302); self.send_header('Location','/mes/'); self.end_headers(); return
            if path.startswith('/mes/'):
                # Only public MES assets. Never expose the repo root, state or tokens.
                relative = path[len('/mes/'):] or 'index.html'
                try:
                    asset = (web_root/relative).resolve()
                    if not asset.is_relative_to(web_root.resolve()) or not asset.is_file():
                        return self.respond(404,{'error':'Not found'})
                    if asset.suffix.lower() not in ('.html','.js','.css','.json','.svg','.png','.ico','.woff2'):
                        return self.respond(404,{'error':'Not found'})
                    data = asset.read_bytes()
                    if relative == 'index.html':
                        text=data.decode('utf-8')
                        text=re.sub(r'(<script src="js/config\.js[^\"]*"></script>)',
                                    r'\1<script src="/mes-local.js"></script>',text)
                        text=re.sub(r'<script src="js/mod_widget\.js[^\"]*"></script>','',text)
                        text=text.replace('<script src="js/boot.js',
                            '<script src="/milg-widget.js"></script><script src="js/mod_engine_widget.js"></script>\n<script src="js/boot.js')
                        data=text.encode('utf-8')
                    return self.respond(200,data,(mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')+'; charset=utf-8')
                except (OSError,ValueError):
                    return self.respond(404,{'error':'Not found'})
            if path == '/mes-local.js':
                # Local replay only. No external data, write APIs or model URL guesses.
                config=b"Object.assign(window.MLNG_CONFIG,{SUPABASE_URL:'',SUPABASE_ANON_KEY:'',ASK_URL:'off',AI_URL:'',LOCAL_ENGINE_MODE:true,START_VIEW:'engine_cycles'});document.documentElement.setAttribute('data-theme','light');"
                return self.respond(200,config,'text/javascript; charset=utf-8')
            if self.path == '/milg-widget.js':
                return self.respond(200,widget,'text/javascript; charset=utf-8')
            if self.path == '/':
                return self.respond(200, page, 'text/html; charset=utf-8')
            if self.path == '/demo':
                return self.respond(200, demo_page, 'text/html; charset=utf-8')
            if self.path == '/operations':
                return self.respond(200,(Path(__file__).parent/'operations_ui.html').read_bytes(),'text/html; charset=utf-8')
            return self.respond(404, {'error': 'Not found'})

        def do_POST(self):
            if self.headers.get('Origin') and self.headers['Origin'] not in allowed_origins and self.headers['Origin'] not in ('http://127.0.0.1:'+str(port),'http://localhost:'+str(port)):
                return self.respond(403,{'error':'Origin is not allowed'})
            admin = self.path in ('/api/learning/configure','/api/learning/rollback')
            expected_token = admin_token if admin else token
            if not secrets.compare_digest(self.headers.get('Authorization',''), 'Bearer '+expected_token):
                return self.respond(401, {'error': 'Pilot token required'})
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 2_000_000:
                    raise ValueError('JSON body limit: 2 MB')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('JSON object required')
                if self.path == '/api/status':
                    result = engine.status()
                elif self.path == '/api/example':
                    result = engine.example(body['process'])
                elif self.path == '/api/contract':
                    result = engine.contract(body['process'])
                elif self.path == '/api/history':
                    result = engine.history(body.get('limit',50))
                elif self.path == '/api/cycle':
                    result = engine.submit(body)
                elif self.path == '/api/review':
                    result = engine.review(**body)
                elif self.path == '/api/feedback':
                    result = engine.feedback(**body)
                elif self.path == '/api/lot-close':
                    result = learning.close_lot(**body)
                elif self.path == '/api/learning/status':
                    result = learning.status()
                elif self.path == '/api/learning/configure':
                    result = learning.configure(**body)
                elif self.path == '/api/learning/rollback':
                    result = learning.rollback(**body)
                elif self.path == '/api/calibrate':
                    result = engine.calibrate(body['context'])
                else:
                    return self.respond(404, {'error': 'Not found'})
                self.respond(200, result)
            except Busy as exc:
                self.respond(409, {'error':str(exc)})
            except (ValueError, TypeError, KeyError) as exc:
                self.respond(400, {'error': str(exc)})
            except Exception:
                self.respond(500, {'error': 'Internal error; inspect local engine files'})

        def log_message(self, *args):
            pass

    print(f'Pilot listening: http://127.0.0.1:{port}', flush=True)
    print(f'MES presentation: http://127.0.0.1:{port}/mes/', flush=True)
    print('Token file:', token_path, flush=True)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    supervisor = None
    if auto_learn:
        from .learning_worker import Supervisor
        supervisor = Supervisor(state); supervisor.start()
        print('Automatic learning supervisor enabled; configured scopes only',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close()
        if supervisor: supervisor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status','predict','submit','feedback','calibrate','train-field','serve','rollback'])
    parser.add_argument('--state', required=True)
    parser.add_argument('--input')
    parser.add_argument('--out')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--allow-origin',action='append',default=[])
    parser.add_argument('--auto-learn',action='store_true')
    args = parser.parse_args()
    if args.command == 'serve':
        return serve(args.state, args.port, args.allow_origin, args.auto_learn)
    if args.command == 'rollback':
        state = Path(args.state)
        previous = json.loads((state/'pilot-previous.json').read_text(encoding='utf-8'))
        raw = (Path(previous['release'])/'release.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != previous['sha256']:
            raise ValueError('Rollback manifest checksum mismatch')
        write_json(state/'pilot-active.json', previous)
        result = {'restored': previous}
    else:
        engine = PilotEngine(args.state)
        payload = json.loads(Path(args.input).read_text(encoding='utf-8-sig')) if args.input else {}
        if args.command == 'status': result = engine.status()
        elif args.command == 'predict': result = engine.predict(payload.get('process','auto'), payload['rows'], payload.get('context'))
        elif args.command == 'submit': result = engine.submit(payload)
        elif args.command == 'feedback': result = engine.feedback(**payload)
        elif args.command == 'train-field': result = engine.train_field(payload['process'], payload['context'])
        else: result = engine.calibrate(payload['context'])
    if args.out: write_json(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
