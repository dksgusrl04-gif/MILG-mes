"""Local pilot runtime: versioned public models and tenant-local press-fit references.

No machine control or automatic learning. Only trusted local release artifacts load.
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
        if hashlib.sha256((self.release/'routes.json').read_bytes()).hexdigest() != self.manifest['routes_sha256']:
            raise ValueError('Pilot route registry checksum mismatch')
        self.router = Router(self.release)
        self.dbpath = self.state / 'pilot.sqlite3'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                  created TEXT NOT NULL, process TEXT NOT NULL, scope TEXT NOT NULL,
                  lot TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY, event TEXT NOT NULL,
                  created TEXT NOT NULL, actor TEXT NOT NULL, outcome TEXT NOT NULL,
                  evidence TEXT NOT NULL, note TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS baselines(id TEXT PRIMARY KEY, created TEXT NOT NULL,
                  scope TEXT NOT NULL, artifact TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS field_models(id TEXT PRIMARY KEY, created TEXT NOT NULL,
                  scope TEXT NOT NULL, process TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL);
            ''')

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
                      for table in ('events', 'feedback', 'baselines', 'field_models')}
        return {'release': self.manifest['id'], 'mode': 'pilot',
                'processes': self.manifest['processes'], 'counts': counts,
                'field_validated': False, 'state': str(self.state)}

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
            features = input_features(route['input_contract'], frame, route['feature_columns'])
            # Missing whole channels must not silently become a confident prediction.
            if route['input_contract'] == 'mct_sensor_columns_v1' and not np.isfinite(features.to_numpy()).all():
                raise ValueError('MCT pilot requires finite values for every contracted sensor feature')
            result = self.router.predict(process, frame)
            capability = self.manifest['processes'][process]
            from .adapters import REGRESSION_TARGETS
            continuous = result.get('target') in (REGRESSION_TARGETS - {'preload_kN'})
            result['decision_status'] = ('benchmark_estimate' if continuous else
                'experimental_alert' if capability.get('alert_enabled', False) else 'needs_threshold_validation')
            for row in result['rows']:
                row['quality_prediction'] = None
                row['benchmark_alert'] = row.get('alert')
                if not capability.get('alert_enabled', False):
                    row['alert'] = None
            result['probability_note'] = ('Public benchmark estimate in source target units' if continuous else
                'Dataset classifier score; not calibrated to this factory or independent joint strength')
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
        return input_features(route['input_contract'], pd.DataFrame(rows), route['feature_columns'])

    def _field_prediction(self, process, rows, context, result):
        import joblib
        from .adapters import standardize, _input_evidence
        with self.db() as db:
            entry = db.execute('SELECT id,path,sha256 FROM field_models WHERE scope=? AND process=? ORDER BY created DESC LIMIT 1',
                               (context_key(context), process)).fetchone()
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
        with self.db() as db:
            entries = db.execute('''SELECT e.id,e.lot,e.request,f.outcome FROM events e JOIN feedback f ON f.event=e.id
                WHERE e.scope=? AND e.process=? AND f.outcome IN ('pass','fail')
                AND f.id=(SELECT MAX(id) FROM feedback WHERE event=e.id) ORDER BY e.created,e.id''', (scope,process)).fetchall()
        entries = [e for e in entries if not json.loads(e[2]).get('demo',False)]
        lots = list(dict.fromkeys(e[1] for e in entries))
        if len(entries) < 100 or len(lots) < 5:
            raise ValueError('Field quality training needs >=100 inspected cycles across >=5 lots in the same context')
        a,b = max(1,int(.6*len(lots))), max(2,int(.8*len(lots)))
        sets = [set(lots[:a]),set(lots[a:b]),set(lots[b:])]
        masks = [np.array([e[1] in s for e in entries]) for s in sets]
        y = np.array([int(e[3]=='fail') for e in entries])
        for mask,minimum in zip(masks,(10,5,5)):
            if any(int(np.sum(y[mask]==value)) < minimum for value in (0,1)):
                raise ValueError('Each split needs both outcomes: train >=10/class, validation and test >=5/class')
        rows = [json.loads(e[2])['rows'][0] for e in entries]
        frame = self._feature_frame(process, rows)
        tr,val,test = masks
        transform = TrainTransform().fit(frame.loc[tr])
        X = [transform.transform(frame.loc[m]) for m in masks]
        models = {'linear':LogisticRegression(max_iter=2000,random_state=42),
                  'nonlinear':HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=7,min_samples_leaf=10,
                                l2_regularization=2,early_stopping=False,random_state=42)}
        validation = {}
        with threadpool_limits(limits=4):
            for key, model in models.items():
                model.fit(X[0],y[tr])
                score = model.predict_proba(X[1])[:,1]
                threshold = threshold_on_validation(y[val],score,.05)
                validation[key] = metrics(y[val],score,threshold)
        selected = max(validation,key=lambda k:(validation[k]['recall'],validation[k]['auc']))
        threshold = validation[selected]['threshold']
        evaluation = metrics(y[test],models[selected].predict_proba(X[2])[:,1],threshold)
        # Predeclared pilot screening gates; not a customer acceptance guarantee.
        passed = all(m['auc'] >= .65 and m['recall'] >= .5 and m['false_positive_rate'] <= .1
                     for m in (validation[selected],evaluation))
        previous_comparison = None
        with self.db() as db:
            previous = db.execute('SELECT path,sha256 FROM field_models WHERE scope=? AND process=? ORDER BY created DESC LIMIT 1',
                                  (scope,process)).fetchone()
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
                  'validation':validation,'test':evaluation,'installed':passed,
                  'previous_comparison':previous_comparison,
                  'split_lots':[sorted(s) for s in sets], 'splits':{s:[e[0] for e,m in zip(entries,mask) if m]
                                  for s,mask in zip(('train','validation','test'), masks)},
                  'gates':{'min_auc':.65,'min_recall':.5,'max_fpr':.1},
                  'limitations':'Historical LOT holdout; repeated tuning requires a fresh external test set'}
        artifact = {'model':models[selected],'preprocessing':transform.export(),'threshold':threshold,
                    'inspection_outcomes':{e[0]:e[3] for e in entries},'process':process,'context':context}
        joblib.dump(artifact,folder/'model.joblib')
        write_json(folder/'report.json',report)
        if passed:
            path=folder/'model.joblib'
            with self.db() as db:
                db.execute('INSERT INTO field_models VALUES(?,?,?,?,?,?)',
                    (identity,now(),scope,process,path.relative_to(self.state).as_posix(),hashlib.sha256(path.read_bytes()).hexdigest()))
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
            result = self.predict(payload.get('process', 'auto'), payload['rows'], payload['context'])
            if result['mode'] == 'needs_confirmation':
                return result
            result.update(event_id=event_id, created=now())
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?)',
                       (event_id, request_hash, result['created'], result['process'], scope, payload['lot_id'],
                        serialized, json.dumps(result, ensure_ascii=False, allow_nan=False)))
        return result

    def feedback(self, event_id, outcome, actor, evidence, note=''):
        if outcome not in ('pass', 'fail', 'unknown') or not isinstance(actor, str) or not actor.strip():
            raise ValueError('outcome pass/fail/unknown and actor required')
        if not isinstance(evidence, str) or (outcome != 'unknown' and not evidence.strip()):
            raise ValueError('Independent inspection reference required for pass/fail')
        if len(actor) > 100 or len(evidence) > 1000 or not isinstance(note, str) or len(note) > 2000:
            raise ValueError('Feedback text too long')
        with self.db() as db:
            if not db.execute('SELECT 1 FROM events WHERE id=?', (event_id,)).fetchone():
                raise ValueError('Unknown cycle_id in this tenant')
            cur = db.execute('INSERT INTO feedback(event,created,actor,outcome,evidence,note) VALUES(?,?,?,?,?,?)',
                            (event_id, now(), actor, outcome, evidence, note))
        return {'feedback_id': cur.lastrowid, 'event_id': event_id, 'training_performed': False}

    def calibrate(self, context):
        scope = context_key(context)
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
            db.execute('INSERT INTO baselines VALUES(?,?,?,?)', (identity, now(), scope, json.dumps(artifact)))
        return {'baseline_id': identity, **artifact}


def serve(state, port):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    engine = PilotEngine(state)
    token_path = Path(state)/'pilot-token.txt'
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(32), encoding='utf-8')
    token = token_path.read_text(encoding='utf-8').strip()
    page = (Path(__file__).parent/'pilot_ui.html').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, data, content_type='application/json; charset=utf-8'):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/':
                return self.respond(200, page, 'text/html; charset=utf-8')
            return self.respond(404, {'error': 'Not found'})

        def do_POST(self):
            if not secrets.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token):
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
                elif self.path == '/api/cycle':
                    result = engine.submit(body)
                elif self.path == '/api/feedback':
                    result = engine.feedback(**body)
                elif self.path == '/api/calibrate':
                    result = engine.calibrate(body['context'])
                else:
                    return self.respond(404, {'error': 'Not found'})
                self.respond(200, result)
            except (ValueError, TypeError, KeyError) as exc:
                self.respond(400, {'error': str(exc)})
            except Exception:
                self.respond(500, {'error': 'Internal error; inspect local engine files'})

        def log_message(self, *args):
            pass

    print(f'Pilot listening: http://127.0.0.1:{port}', flush=True)
    print('Token file:', token_path, flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status','predict','submit','feedback','calibrate','train-field','serve','rollback'])
    parser.add_argument('--state', required=True)
    parser.add_argument('--input')
    parser.add_argument('--out')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.command == 'serve':
        return serve(args.state, args.port)
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
