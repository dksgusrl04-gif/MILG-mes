"""Train and evaluate local Training Pack data without mixing process targets."""
import argparse
import io
import json
import shutil
import uuid
import zipfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import average_precision_score, mean_absolute_error
from threadpoolctl import threadpool_limits

from .evaluate_processes import metrics
from .pilot import PilotEngine, now, write_json
from .routing import Router, digest
from .strict_evaluation import TrainTransform, threshold_on_validation

INJECTION = (
    'Injection_Time Filling_Time Plasticizing_Time Cycle_Time Clamp_Close_Time '
    'Cushion_Position Switch_Over_Position Plasticizing_Position Clamp_Open_Position '
    'Max_Injection_Speed Max_Screw_RPM Average_Screw_RPM Max_Injection_Pressure '
    'Max_Switch_Over_Pressure Max_Back_Pressure Average_Back_Pressure'
).split() + [f'Barrel_Temperature_{i}' for i in range(1, 7)]
ID, DATE, TIME = '배정번호', '작업일', 'TAG_MIN'
HEAT = (
    '건조 1존 OP|건조 2존 OP|건조로 온도 1 Zone|건조로 온도 2 Zone|세정기|'
    '소입1존 OP|소입2존 OP|소입3존 OP|소입4존 OP|소입로 CP 값|'
    '소입로 온도 1 Zone|소입로 온도 2 Zone|소입로 온도 3 Zone|소입로 온도 4 Zone|'
    '솔트 컨베이어 온도 1 Zone|솔트 컨베이어 온도 2 Zone|솔트조 온도 1 Zone|솔트조 온도 2 Zone'
).split('|')


def chronological(group):
    """The input order defines time, and a whole group belongs to one split."""
    group = pd.Series(group).reset_index(drop=True)
    if group.isna().any():
        raise ValueError('Missing time/group key')
    keys = group.drop_duplicates().tolist()
    n = len(keys)
    if n < 5:
        raise ValueError('At least five independent groups required')
    # A reused group can straddle others, so refuse interleaved time groups.
    if len(group[group.ne(group.shift())]) != n:
        raise ValueError('Temporal groups are interleaved')
    return [group.isin(keys[a:b]).to_numpy()
            for a, b in ((0, int(n*.6)), (int(n*.6), int(n*.8)), (int(n*.8), n))]


def classify(X, y, masks):
    tr, va, te = masks
    if any(len(np.unique(y[m])) != 2 for m in masks):
        return {'status': 'insufficient_classes', 'split_positives': [int(y[m].sum()) for m in masks]}, None
    prep = TrainTransform().fit(X.loc[tr])
    a, b, c = [prep.transform(X.loc[m]) for m in masks]
    models = {
        'logistic': LogisticRegression(max_iter=1500, class_weight='balanced', random_state=17),
        'forest': RandomForestClassifier(n_estimators=150, min_samples_leaf=12,
                                         class_weight='balanced_subsample', random_state=17, n_jobs=2),
    }
    validation = {}
    for key, model in models.items():
        model.fit(a, y[tr])
        score = model.predict_proba(b)[:, 1]
        threshold = threshold_on_validation(y[va], score, .05)
        validation[key] = {**metrics(y[va], score, threshold),
                           'average_precision': float(average_precision_score(y[va], score)),
                           'threshold': threshold}
    selected = max(validation, key=lambda k: (validation[k]['recall'], validation[k]['average_precision']))
    threshold = validation[selected]['threshold']
    score = models[selected].predict_proba(c)[:, 1]
    test = {**metrics(y[te], score, threshold), 'average_precision': float(average_precision_score(y[te], score)),
            'prevalence': float(y[te].mean())}
    gate = (min(int(y[va].sum()), int(y[te].sum())) >= 10 and test['recall'] >= .5
            and test['false_positive_rate'] <= .10 and test['average_precision'] > test['prevalence'])
    return {'status': 'evaluated', 'selected': selected, 'validation': validation, 'test': test, 'gate': gate}, {
        'model': models[selected], 'threshold': threshold, 'preprocessing': prep.export()}


def regress(X, y, masks, bounds=None):
    tr, va, te = masks
    prep = TrainTransform().fit(X.loc[tr])
    a, b, c = [prep.transform(X.loc[m]) for m in masks]
    center = float(np.median(y[tr]))
    models = {'forest': RandomForestRegressor(n_estimators=160, min_samples_leaf=6,
                                              random_state=17, n_jobs=2),
              'ridge': Ridge(alpha=10)}
    def evaluate(actual, predicted):
        if bounds is not None:
            predicted = np.clip(predicted,*bounds)
        return {'rows': len(actual), 'mae': float(mean_absolute_error(actual, predicted)),
                'median_baseline_mae': float(np.mean(abs(actual-center)))}
    validation = {}
    for key, model in models.items():
        model.fit(a, y[tr])
        validation[key] = evaluate(y[va], model.predict(b))
    selected = min(validation, key=lambda k: validation[k]['mae'])
    test = evaluate(y[te], models[selected].predict(c))
    gate = all(m['rows'] >= 20 and m['mae'] < .9*m['median_baseline_mae']
               for m in (validation[selected], test))
    return {'selected': selected, 'validation': validation, 'test': test, 'gate': gate}, {
        'model': models[selected], 'preprocessing': prep.export()}


def injection(path):
    with zipfile.ZipFile(path) as z:
        with z.open('dataset/labeled_data.csv') as f:
            d = pd.read_csv(f, encoding='utf-8-sig', usecols=['_id','TimeStamp','EQUIP_CD','PassOrFail',*INJECTION])
    original_rows = len(d)
    original_defects = int(d.PassOrFail.eq('N').sum())
    d = d.drop_duplicates()
    if d['_id'].isna().any() or d['_id'].duplicated().any() or not set(d.PassOrFail) <= {'Y','N'}:
        raise ValueError('Invalid KAMP injection IDs or quality labels')
    d['TimeStamp'] = pd.to_datetime(d.TimeStamp, errors='raise')
    d = d.sort_values('TimeStamp').reset_index(drop=True)
    masks = chronological(d.TimeStamp.dt.normalize())
    X = d[INJECTION].apply(pd.to_numeric, errors='raise')
    result, artifact = classify(X, d.PassOrFail.eq('N').to_numpy(int), masks)
    result.update(raw_rows=original_rows, raw_defects=original_defects, duplicates_removed=original_rows-len(d),
                  rows=len(d), defects=int(d.PassOrFail.eq('N').sum()), features=list(X),
                  date_ranges=[[str(d.TimeStamp[m].min()),str(d.TimeStamp[m].max())] for m in masks],
                  split_rows=[int(m.sum()) for m in masks])
    return result, artifact, X.loc[masks[2]].dropna().head(1)


def heat_source(path, raw, label, encoding):
    with zipfile.ZipFile(path) as z:
        labels = pd.read_excel(io.BytesIO(z.read(label)), engine='openpyxl')
        parts = []
        with z.open(raw) as f:
            for chunk in pd.read_csv(f, usecols=[ID,TIME,*HEAT], encoding=encoding, chunksize=150_000,
                                     dtype={ID:'Int64',TIME:str,**dict.fromkeys(HEAT,float)}):
                chunk = chunk.dropna(subset=[ID,TIME])
                x = chunk[HEAT]
                by = chunk[ID]
                counts = x.groupby(by).count()
                sums = x.groupby(by).sum()
                squares = x.pow(2).groupby(by).sum()
                stamp_hash = pd.util.hash_pandas_object(chunk[TIME], index=False).groupby(by).sum()
                value_hash = pd.util.hash_pandas_object(chunk[[TIME,*HEAT]], index=False).groupby(by).sum()
                parts.append((counts,sums,squares,chunk.groupby(ID).size(),stamp_hash,value_hash))
    count, total, square, n, stamp, values = (
        pd.concat(items).groupby(level=0).sum() for items in zip(*parts))
    mean = total/count.replace(0,np.nan)
    std = ((square-total*mean)/(count-1).where(count>1)).clip(lower=0).pow(.5)
    features = pd.concat([mean.add_suffix('_Avg'),std.add_suffix('_Std')],axis=1)
    return labels, features, pd.DataFrame({'rows':n,'stamp_hash':stamp,'value_hash':values})


def heat(quality, optimization):
    print('Reading KAMP heat-quality sensors by chunk', flush=True)
    labels, X, audit = heat_source(quality,'data/data.csv','data/열처리_품질데이터.xlsx','cp949')
    print('Checking overlap with KAMP heat optimization', flush=True)
    other_labels, _, other_audit = heat_source(optimization,'data/raw_total_data.csv','data/label.xlsx','utf-8-sig')
    overlap = audit.join(other_audit,how='inner',lsuffix='_quality',rsuffix='_optimization')
    same_time = (overlap.rows_quality==overlap.rows_optimization)&(overlap.stamp_hash_quality==overlap.stamp_hash_optimization)
    same_values = same_time & (overlap.value_hash_quality==overlap.value_hash_optimization)
    common = labels.merge(other_labels,on=ID,suffixes=('_quality','_optimization'),validate='one_to_one')
    consistent = (common['불량수량_quality']==common['불량수량_optimization']) & (common['총수량_quality']==common['총수량_optimization'])
    if labels[ID].isna().any() or labels[ID].duplicated().any():
        raise ValueError('Heat-treatment quality table needs one row per assignment')
    joined = labels.join(X,on=ID,how='inner',validate='one_to_one').sort_values([DATE,ID]).reset_index(drop=True)
    if not (joined['총수량'].gt(0) & joined['불량수량'].ge(0) & joined['불량수량'].le(joined['총수량'])).all():
        raise ValueError('Invalid heat-treatment counts')
    masks = chronological(pd.to_datetime(joined[DATE],errors='raise').dt.normalize())
    X = joined[list(X)]
    # A channel unavailable in training cannot be part of the input contract.
    X = X.loc[:,X.loc[masks[0]].notna().any()]
    y = (joined['불량수량']/joined['총수량']).to_numpy(float)
    result, artifact = regress(X,y,masks,bounds=(0,1))
    with zipfile.ZipFile(quality) as z:
        with z.open('data/train.csv') as f:
            reference = pd.read_csv(f,encoding='cp949')
    # Source indices are treated as a proposed mapping. Compare sensor summaries
    # before trusting the mapping or interpreting the vendor's risk labels.
    indices = pd.to_numeric(reference.iloc[:,0],errors='raise').astype(int)
    aligned = labels.reset_index(drop=True).reindex(indices).set_index(ID)
    indexed = joined.set_index(ID).reindex(aligned.index)
    cols = list(set(reference.columns)&set(X.columns))
    comparisons = np.isclose(reference[cols].to_numpy(float),indexed[cols].to_numpy(float),rtol=.001,atol=.001)
    comparable = np.isfinite(reference[cols].to_numpy(float)) & np.isfinite(indexed[cols].to_numpy(float))
    result.update(label_rows=len(labels),joined_rows=len(joined),features=list(X),
                  split_rows=[int(m.sum()) for m in masks],
                  date_ranges=[[str(joined.loc[m,DATE].min()),str(joined.loc[m,DATE].max())] for m in masks],
                  overlap={'shared_label_assignments':len(common),'matching_label_counts':int(consistent.sum()),
                           'shared_sensor_assignments':len(overlap),'same_timestamp_multiset':int(same_time.sum()),
                           'same_shared_sensor_rows':int(same_values.sum()),'optimization_rows_used_for_training':0},
                  guide_reference={'rows':len(reference),'classes':reference['불량단계'].value_counts().to_dict(),
                                   'shared_features':len(cols),'proposed_index_matches':int(aligned.index.isin(joined[ID]).sum()),
                                   'comparable_cells':int(comparable.sum()),'matching_cells':int((comparisons&comparable).sum()),
                                   'verified_for_same_target_comparison':False})
    return result, artifact, X.loc[masks[2]].head(1)


def activate(state, out, artifacts, capability_details=None):
    """Extend an existing pilot snapshot and atomically switch its pointer."""
    from .service import Service
    prior = (state/'pilot-active.json').read_bytes()
    base = PilotEngine(state)
    if base.pointer.read_bytes()!=prior:
        raise ValueError('Pilot release changed before candidate verification; rerun')
    release = state/'pilot-releases'/('process-packs-'+uuid.uuid4().hex)
    release.mkdir(parents=True)
    for name in ('routed_models','examples'):
        if (base.release/name).is_dir():
            shutil.copytree(base.release/name,release/name)
    shutil.copyfile(base.release/'routes.json',release/'routes.json')
    manifest = json.loads(json.dumps(base.manifest))
    manifest.update(id=release.name,created=now(),process_pack_report=str(out/'report.json'))
    router = Router(release)
    for key, (artifact, result, sample) in artifacts.items():
        router.register(key,artifact,'specialist','public-process-pack')
        manifest['processes'][key] = {'mode':'public_benchmark_shadow','target':joblib.load(artifact)['target'],
            'alert_enabled':False,'auto_route':False,'quality_release_authority':False,
            'test':result['test']}
        manifest['processes'][key].update((capability_details or {}).get(key, {}))
        record = json.loads(sample.to_json(orient='records'))[0]
        write_json(release/'examples'/(key+'.json'),{'process':key,'cycle_id':'example-'+key,
                   'lot_id':'EXAMPLE-LOT','demo':True,'context':{'machine_id':'EXAMPLE','part_number':'EXAMPLE','program':'v1'},
                   'rows':[record]})
    manifest['routes_sha256'] = digest(release/'routes.json')
    write_json(release/'release.json',manifest)
    pointer = {'release':str(release),'sha256':digest(release/'release.json')}
    verify = out/'verification-state'
    write_json(verify/'pilot-active.json',pointer)
    pilot = PilotEngine(verify)
    for key in artifacts:
        example = json.loads((release/'examples'/(key+'.json')).read_text(encoding='utf-8'))
        if len(pilot.submit(example)['rows']) != 1:
            raise ValueError('Engine smoke check failed: '+key)
    with Service(state)._exclusive():
        if base.pointer.read_bytes()!=prior:
            raise ValueError('Pilot release changed during candidate verification; rerun')
        write_json(state/'pilot-previous.json',json.loads(prior))
        write_json(base.pointer,pointer)
    return str(release)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--packs',type=Path,required=True)
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--install',action='store_true')
    a = p.parse_args()
    out = a.out.resolve()/('packs-'+uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    print('Reports:',out,flush=True)
    report = {'created':now(),'installed':[]}
    artifacts = {}
    from .pack_waveforms import scatim, pressfit_benchmark

    def pack_file(folder, filename, legacy=None):
        organized = a.packs / folder / filename
        return organized if organized.is_file() else a.packs / (legacy or filename)

    with threadpool_limits(limits=2):
        for key, train in (
            ('injection_kamp',lambda:injection(pack_file('사출', '04. Dataset_Molding.zip'))),
            ('heat_kamp',lambda:heat(pack_file('열처리', 'Dataset_열처리 품질보증 AI 데이터셋.zip'),
                                     pack_file('열처리', 'Dataset_열처리 공정최적화 AI 데이터셋.zip'))),
        ):
            print('Training',key,flush=True)
            result, artifact, sample = train()
            report[key] = result
            if artifact:
                path = out/(key+'.joblib')
                artifact.update(feature_recipe=key+'_summary_v1',
                                target='batch_defect_rate' if key=='heat_kamp' else 'kamp_injection_nok')
                joblib.dump(artifact,path)
                if result.get('gate') and not sample.empty:
                    artifacts[key] = path,result,sample
            write_json(out/'report.json',report)
        print('Training separate SCATIM regression candidates',flush=True)
        report['scatim'], scatim_ready = scatim(pack_file('사출', 'MILG_01_Injection.zip'),out)
        artifacts.update(scatim_ready)
        write_json(out/'report.json',report)
        print('Checking press-fit waveform inputs and proxy evaluation',flush=True)
        report['pressfit'] = pressfit_benchmark(pack_file('압입', 'MILG_02_Pressfit.zip', 'MILG_02_Heat_Treatment_Pressfit.zip'),a.state.resolve(),out)
        write_json(out/'report.json',report)
    if a.install and artifacts:
        report['release'] = activate(a.state.resolve(),out,artifacts)
        report['installed'] = sorted(artifacts)
    write_json(out/'report.json',report)
    print('Installed:',report['installed'],flush=True)
    print('Report:',out/'report.json',flush=True)


if __name__=='__main__':
    main()
