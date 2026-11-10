"""Separate post-operation screw-tightening candidates; never updates engine state."""
import argparse
import hashlib
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
from .strict_evaluation import TrainTransform, threshold_on_validation
from .evaluate_processes import metrics

STAGES = ('Finding', 'Thread forming', 'Pre-tightening', 'Tightening 1.4')
STATS = ('present', 'mean', 'std', 'max', 'end', 'angle_span', 'slope', 'area')

def curve_features(raw):
    result = {}
    stages = raw.get('tightening steps', [])
    if len({s['name'] for s in stages}) != len(stages):
        raise ValueError('Duplicate stage names')
    if any(s['name'] not in STAGES for s in stages):
        raise ValueError('Unknown stage contract')
    valid = False
    for index, name in enumerate(STAGES):
        step = next((s for s in stages if s['name'] == name), {})
        graph = step.get('graph', {})
        angle = np.asarray(graph.get('angle values', []), dtype=float)
        torque = np.asarray(graph.get('torque values', []), dtype=float)
        if len(angle) != len(torque) or not np.isfinite(angle).all() or not np.isfinite(torque).all():
            raise ValueError('Invalid measured curve')
        values = [0., np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan]
        if len(torque) >= 2:
            valid = True
            centered = angle - angle.mean()
            denom = float(centered @ centered)
            values = [1., torque.mean(), torque.std(), torque.max(), torque[-1],
                      angle[-1]-angle[0], float(centered @ torque)/denom if denom > 1e-12 else 0.,
                      float(np.trapezoid(torque, angle))]
        result.update({f'stage_{index}_{k}': float(v) for k, v in zip(STATS, values)})
    if not valid:
        raise ValueError('No usable measured curves')
    return result

def partition(group):
    # Label-independent; identical physical workpiece IDs remain together across ZIPs.
    bucket = int(hashlib.sha256(('milg-screw-v1:'+str(group)).encode()).hexdigest()[:12], 16) % 100
    return 'train' if bucket < 60 else 'validation' if bucket < 80 else 'test'

def load_data(root,feature_fn=None):
    feature_fn=feature_fn or curve_features
    rows = []; seen = {}; inputs = []; counts = {'input_rows': 0, 'duplicates': 0, 'exceptions': 0, 'invalid_curves': 0}
    files = sorted(Path(root).glob('s0[1-6]_*.zip'))
    if len(files) != 6:
        raise ValueError('Expected exactly six s01-s06 ZIPs in data directory')
    for file in files:
        print('Reading', file.name, flush=True)
        inputs.append({'path': str(file.resolve()), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
        with zipfile.ZipFile(file) as archive:
            names = archive.namelist()
            label_files = [n for n in names if n.endswith('labels.csv')]
            if len(label_files) != 1: raise ValueError('Expected one labels.csv')
            labels = pd.read_csv(io.BytesIO(archive.read(label_files[0])), dtype=str)
            mapping = {}
            for name in names:
                if name.endswith('.json'):
                    basename = Path(name).name
                    if basename in mapping: raise ValueError('Ambiguous run filename')
                    mapping[basename] = name
            for row in labels.to_dict('records'):
                counts['input_rows'] += 1
                payload = archive.read(mapping[row['file_name']])
                digest = hashlib.sha256(payload).hexdigest()
                if row['scenario_condition'] not in ('normal', 'faulty') or row['workpiece_result'] not in ('OK', 'NOK'):
                    raise ValueError('Unknown target encoding')
                if pd.isna(row['workpiece_id']): raise ValueError('Missing workpiece ID')
                identity = (row['workpiece_id'], row['scenario_condition'], row['workpiece_result'], row['scenario_exception'])
                if digest in seen:
                    if seen[digest] != identity: raise ValueError('Duplicate run has conflicting annotations')
                    counts['duplicates'] += 1
                    continue
                seen[digest] = identity
                if row['scenario_exception'] != '0':
                    counts['exceptions'] += 1
                    continue
                try: features = feature_fn(json.loads(payload))
                except ValueError:
                    counts['invalid_curves'] += 1
                    continue
                features.update(group=row['workpiece_id'], split=partition(row['workpiece_id']),
                                scenario=file.name[:3], sha256=digest,
                                class_value=row['class_value'],workpiece_usage=row.get('workpiece_usage',''),
                                workpiece_location=row.get('workpiece_location',''),
                                faulty=int(row['scenario_condition']=='faulty'), nok=int(row['workpiece_result']=='NOK'))
                rows.append(features)
    if not rows: raise ValueError('No usable curves')
    return pd.DataFrame(rows), {'files': inputs, 'counts': counts}

def train_target(df, target, out, fpr):
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    columns = [f'stage_{i}_{s}' for i in range(4) for s in STATS]
    train, val, test = [df.loc[df.split==s] for s in ('train','validation','test')]
    if any(set(d[target]) != {0,1} for d in (train,val,test)):
        raise ValueError('Both classes required in each partition for '+target)
    transform = TrainTransform().fit(train[columns])
    a,b,c = [transform.transform(d[columns]) for d in (train,val,test)]
    weight = 1/train.groupby('group')['group'].transform('count').to_numpy()
    weight *= len(weight)/weight.sum()
    models = {'linear': LogisticRegression(max_iter=1000, random_state=42),
              'nonlinear': HistGradientBoostingClassifier(max_iter=120, max_leaf_nodes=15,
                  min_samples_leaf=30, l2_regularization=2, early_stopping=False, random_state=42)}
    reports = {}; predictions = []
    for name, model in models.items():
        print('Training', target, name, flush=True)
        model.fit(a, train[target], sample_weight=weight)
        vs = model.predict_proba(b)[:,1]
        threshold = threshold_on_validation(val[target], vs, fpr)
        validation = metrics(val[target], vs, threshold)
        score = model.predict_proba(c)[:,1]
        reports[name] = {'validation': validation, 'test': metrics(test[target], score, threshold),
                         'by_scenario': {s:metrics(test.loc[test.scenario==s,target], score[(test.scenario==s).to_numpy()],threshold)
                                          for s in sorted(test.scenario.unique())}}
        joblib.dump({'model':model, 'preprocessing':transform.export(), 'threshold':threshold,
                     'feature_recipe':'screw_stage_torque_angle_v1', 'target':target,
                     'process':'screw_tightening', 'inference_scope':'post_operation',
                     'target_meaning':'experimental condition' if target=='faulty' else 'equipment NOK, not independent strength test'},
                    out/(target+'_'+name+'.joblib'))
        pred = test[['group','scenario',target]].copy(); pred['candidate']=name; pred['score']=score
        predictions.append(pred)
    eligible = [k for k,v in reports.items() if v['validation']['false_positive_rate'] <= fpr+1e-12]
    selected = max(eligible,key=lambda k:(reports[k]['validation']['recall'],reports[k]['validation']['precision'] or 0)) if eligible else None
    pd.concat(predictions).to_csv(out/(target+'_test_predictions.csv'),index=False)
    return {'selected_on_validation':selected, 'candidates':reports, 'features':columns}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',required=True); parser.add_argument('--out',required=True)
    parser.add_argument('--target-fpr',type=float,default=.05); parser.add_argument('--threads',type=int,default=4)
    args=parser.parse_args()
    if not 0 < args.target_fpr < 1 or args.threads < 1: parser.error('Invalid configuration')
    from threadpoolctl import threadpool_limits
    out=Path(args.out)/datetime.now().strftime('%Y%m%d-%H%M%S-%f'); out.mkdir(parents=True)
    df, manifest=load_data(args.data_dir)
    df[['group','split','scenario','sha256','faulty','nok']].to_csv(out/'split.csv',index=False)
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    report={'process':'screw_tightening','production_modified':False,'counts':manifest['counts'],
            'usable_rows':len(df),'split_method':'physical workpiece ID SHA256 buckets 60/20/20; no label stratification',
            'partitions':{s:{'rows':len(g),'parts':g.group.nunique()} for s,g in df.groupby('split')},
            'limits':'Post-operation measured-curve baselines. Same scenarios occur across partitions; not unseen-factory validation. Faulty conditions and equipment NOK are different targets; neither proves joint strength. No automatic engine registration.'}
    with threadpool_limits(limits=args.threads):
        for target in ('faulty','nok'):
            report[target]=train_target(df,target,out,args.target_fpr)
            (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True); print('Saved:',out.resolve(),flush=True)

if __name__=='__main__': main()
