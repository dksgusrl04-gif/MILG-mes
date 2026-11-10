"""Offline, grouped ablation: whole-curve summaries versus added stage recipe.
Usage: python -m mlng_ai.profile_compare path/to/s04.zip output.json
The target is experimental faulty/normal, not measured joint strength.
"""
import csv
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
import numpy as np
from scipy.stats import rankdata
from .profiles import ProfileRuntime, load_profile
from .model import CumulativeModel

def auc(y, scores):
    n = int(y.sum()); m = len(y)-n
    return float((rankdata(scores)[y == 1].sum()-n*(n+1)/2)/(n*m)) if n*m else None

def evaluate_zip(path):
    with zipfile.ZipFile(path) as z:
        labels = list(csv.DictReader(io.StringIO(z.read('labels.csv').decode('utf-8-sig'))))
        names = {n.rsplit('/',1)[-1]:n for n in z.namelist() if n.endswith('.json')}
        rows, groups, targets, raw = [], [], [], []
        seen = set()
        for l in labels:
            b = z.read(names[l['file_name']]); h = hashlib.sha256(b).hexdigest()
            if h in seen or l.get('scenario_exception') != '0': continue
            seen.add(h)
            j = json.loads(b)
            signal = [v for s in j.get('tightening steps',[]) for v in (s.get('graph') or {}).get('torque values',[])]
            if not signal or not np.isfinite(signal).all(): continue
            raw.append([np.mean(signal), np.std(signal), np.max(signal)])
            rows.append(dict(machine_id='public_station', tool_id='public_spindle',
                installation_id='public_study', part_number='study_part', screw_type='study_screw',
                program=j.get('prg name','unknown'), **{'tightening steps':j['tightening steps']}))
            groups.append(l['workpiece_id']); targets.append(int(l['scenario_condition']=='faulty'))
    test = np.array([int(hashlib.sha256(g.encode()).hexdigest()[:8],16)%5 == 0 for g in groups])
    y = np.array(targets); base = np.array(raw); runtime = ProfileRuntime(load_profile('screw_tightening'), 'public-benchmark')
    # Offline calibration uses only the training partition's known control group.
    for i in np.flatnonzero(~test & (y == 0)):
        runtime.observe(dict(rows[i], confirmed_normal=True))
    extra, valid = [], []
    for row in rows:
        r = runtime.observe(row); extra.append(list(r['features'].values())); valid.append(r['available'])
    valid = np.array(valid); train = ~test & valid; test &= valid
    output = {'dataset':Path(path).name, 'target':'experimental condition faulty (not product defect)',
              'split':'held-out workpiece SHA256 modulo 5', 'train':int(train.sum()),'test':int(test.sum()),
              'excluded_unavailable':int((~valid).sum()),'template_hash':runtime.fingerprint,
              'calibration':'training control samples only; offline benchmark', 'variants':{}}
    for name, X in [('common_summary',base),('summary_plus_template',np.column_stack([base,extra]))]:
        mean = X[train].mean(0); std = X[train].std(0); std[std < 1e-9] = 1
        X = np.clip((X-mean)/std,-8,8)
        model = CumulativeModel([f'x{i}' for i in range(X.shape[1])]); model.add_process('screw',(y[train].sum()+.5)/(train.sum()+1))
        model.update([('screw',X[train],y[train],np.ones(train.sum()))])
        scores = model.predict('screw',X[test],np.ones(test.sum()))[0]
        # Threshold chosen from training normal data, never from test labels.
        tr = model.predict('screw',X[train & (y == 0)],np.ones((train & (y == 0)).sum()))[0]
        threshold = float(np.quantile(tr,.95)); predicted = scores > threshold; yy = y[test]
        output['variants'][name] = {'auc':auc(yy,scores),
            'recall':float(predicted[yy == 1].mean()), 'false_positive_rate':float(predicted[yy == 0].mean()),
            'threshold':threshold}
    return output

def evaluate_mct(path):
    """Held-out tools with an explicitly approved initial calibration period.
    Existing measured RMS summaries are used; no raw waveform is fabricated.
    """
    import pandas as pd
    df = pd.read_csv(path).sort_values(['tool_id','cut_no'])
    cols = ['F_z_rms', 'M_z_rms', 'A_z_rms']
    y = (df.wear_um.to_numpy() >= 200).astype(int)
    raw = df[cols].to_numpy(float); relative = np.zeros_like(raw)
    calibration = np.zeros(len(df),bool)
    for _, ii in df.groupby('tool_id',sort=False).indices.items():
        ii = np.asarray(ii); refs = ii[:10]
        if (y[refs] != 0).any():
            raise ValueError('Initial calibration contains measured wear failures')
        calibration[refs] = True
        base = np.median(raw[refs],axis=0)
        relative[ii] = (raw[ii]-base)/np.maximum(np.abs(base),1e-9)
    result = {'dataset':Path(path).name,'target':'measured wear >= 200 um, benchmark only',
        'calibration':'first 10 cuts per tool, measured normal; excluded from scoring',
        'split':'leave one physical tool out', 'template':'mct_endmill relative reference scenario on precomputed RMS',
        'variants':{}}
    for name, XX in [('common_summary',raw),('summary_plus_template',np.column_stack([raw,relative]))]:
        yy, ss, pp = [],[],[]
        for tool in df.tool_id.unique():
            test = (df.tool_id.to_numpy()==tool) & ~calibration
            train = (df.tool_id.to_numpy()!=tool) & ~calibration
            mean=XX[train].mean(0); std=XX[train].std(0); std[std<1e-9]=1
            X=np.clip((XX-mean)/std,-8,8)
            m=CumulativeModel([f'x{i}' for i in range(X.shape[1])]); m.add_process('mct',(y[train].sum()+.5)/(train.sum()+1))
            m.update([('mct',X[train],y[train],np.ones(train.sum()))])
            score=m.predict('mct',X[test],np.ones(test.sum()))[0]
            normal=train & (y==0)
            threshold=np.quantile(m.predict('mct',X[normal],np.ones(normal.sum()))[0],.95)
            yy.extend(y[test]); ss.extend(score); pp.extend(score>threshold)
        yy=np.array(yy); pp=np.array(pp)
        result['variants'][name]={'auc':auc(yy,np.array(ss)), 'recall':float(pp[yy==1].mean()),
                                  'false_positive_rate':float(pp[yy==0].mean()),'test':len(yy)}
    return result

if __name__ == '__main__':
    result = evaluate_mct(sys.argv[1]) if sys.argv[1].endswith('.csv') else evaluate_zip(sys.argv[1])
    out = Path(sys.argv[2]); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2),encoding='utf-8'); print(json.dumps(result,indent=2))
