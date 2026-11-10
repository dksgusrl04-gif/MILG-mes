"""Separate waveform regression and proxy benchmarks for local Training Packs."""
import hashlib
import io
import json
from pathlib import PurePosixPath
import zipfile

import h5py
import joblib
import numpy as np
import pandas as pd

from .evaluate_processes import metrics
from .pilot import PilotEngine, press_features, write_json


def waveform(values, time=None):
    x = np.asarray(values, dtype=float)
    if time is not None:
        t = np.asarray(time,dtype=float)
        good = np.isfinite(x)&np.isfinite(t)
        x,t = x[good],t[good]
        if len(t)<8 or np.any(np.diff(t)<=0):
            raise ValueError('Waveform time is missing or not increasing')
        grid = np.interp(np.linspace(t[0],t[-1],32),t,x)
    else:
        if len(x)<8 or not np.isfinite(x).all():
            raise ValueError('Incomplete waveform')
        grid = np.interp(np.linspace(0,len(x)-1,32),np.arange(len(x)),x)
    return dict(mean=float(x.mean()),std=float(x.std()),peak=float(x.max()),minimum=float(x.min()),
                **{f'shape_{i:02}':float(v) for i,v in enumerate(grid)})


def hdf_frame(group):
    blocks = []
    for name in group:
        if name.startswith('block') and name.endswith('_items'):
            names = [x.decode() for x in group[name][:]]
            blocks.append(pd.DataFrame(group[name.replace('_items','_values')][:],columns=names))
    return pd.concat(blocks,axis=1)


def scatim(path,out):
    from .train_process_packs import chronological, regress
    results, ready = {}, {}
    with zipfile.ZipFile(path) as pack:
        for i in (1,2,3):
            prefix = f'ds{i}'
            with zipfile.ZipFile(io.BytesIO(pack.read(f'SCATIM/dataset{i}.zip'))) as z:
                if i==2:
                    with h5py.File(io.BytesIO(z.read(z.namelist()[0]))) as h:
                        labels = hdf_frame(h['scalars'])
                        signals = [hdf_frame(h[key]) for key in ('Einspritzdruck','Einspritzstrom')]
                        for signal in signals:
                            signal.columns = ['time' if c=='time' else c.rsplit('_',1)[1] for c in signal]
                    targets = ['weight','GE-GE002*','GERADEHEIT-L*','PT-PT002L*']
                else:
                    with z.open(f'dataset{i}/{prefix}_scalar_and_quality.csv') as f:
                        labels = pd.read_csv(f,decimal=',')
                    signals = []
                    for kind in ('injectionpressure','injectionflow'):
                        with z.open(f'dataset{i}/{prefix}_timeseries_{kind}.csv') as f:
                            signals.append(pd.read_csv(f,decimal=','))
                    targets = ['weight','distanceA' if i==1 else 'distanceB']
            if labels.cycle_counter.duplicated().any() or any(s.columns.duplicated().any() for s in signals):
                raise ValueError('Duplicate SCATIM cycle key')
            rows, rejected = [], 0
            for cycle in sorted(labels.cycle_counter):
                key = str(int(cycle))
                row = {'cycle_counter':int(cycle)}
                try:
                    for channel,s in zip(('pressure','flow'),signals):
                        row.update({channel+'_'+k:v for k,v in waveform(s[key],s.time).items()})
                except (KeyError,ValueError):
                    rejected += 1
                    continue
                rows.append(row)
            joined = pd.DataFrame(rows).merge(labels[['cycle_counter',*targets]],on='cycle_counter',validate='one_to_one')
            features = [c for c in joined if c.startswith(('pressure_','flow_'))]
            record = {'source_rows':len(labels),'paired_cycles':len(joined),'rejected_cycles':rejected,'targets':{}}
            for j,target in enumerate(targets):
                d = joined.dropna(subset=[target]).reset_index(drop=True)
                if len(d)<30:
                    record['targets'][target] = {'status':'insufficient_measured_labels','rows':len(d)}
                    continue
                # Adjacent cycles are kept in time blocks and sets are trained independently.
                masks = chronological(np.arange(len(d))//10)
                result,artifact = regress(d[features],d[target].to_numpy(float),masks)
                filename = f'scatim_{i}_{j}.joblib'
                joblib.dump({**artifact,'target':'scatim_part_weight' if target=='weight' else target,
                             'dataset':i,'feature_recipe':'scatim_waveform_summary_v1'},out/filename)
                record['targets'][target] = {**result,'artifact':filename}
                if target=='weight' and result['gate']:
                    ready[f'scatim_{i}_weight'] = (out/filename,result,d.loc[masks[2],features].head(1))
            results[prefix] = record
    return results, ready


def proxy_metrics(X, y, groups):
    output = {}
    for held in sorted(set(groups)):
        normal = X[(groups!=held)&(y==0)]
        test = groups==held
        if len(normal)<8 or len(set(y[test]))!=2:
            output[str(held)] = {'status':'insufficient_labels','rows':int(test.sum())}
            continue
        center = np.median(normal,axis=0)
        scale = np.maximum(np.quantile(normal,.75,axis=0)-np.quantile(normal,.25,axis=0),
                           np.maximum(abs(center)*.01,1e-6))
        def score(data):
            return np.sqrt(np.mean(((data-center)/scale)**2,axis=1))
        threshold = float(np.quantile(score(normal),.95,method='higher'))
        output[str(held)] = metrics(y[test],score(X[test]),threshold)
    return output


def copper(z,state,out):
    rows, labels, groups, skipped = [], [], [], 0
    example = None
    with zipfile.ZipFile(io.BytesIO(z.read('Pressfit/Copper_Rings/insertion-dataset.zip'))) as archive:
        names = set(archive.namelist())
        for pos in sorted(names):
            p = PurePosixPath(pos)
            if not p.name.startswith('x_') or p.name.startswith('x_ref_') or p.suffix!='.csv':
                continue
            force_name = str(p.with_name('ft_'+p.name[2:]))
            if force_name not in names:
                skipped += 1
                continue
            x = np.loadtxt(io.BytesIO(archive.read(pos)),delimiter=',',ndmin=2)
            f = np.loadtxt(io.BytesIO(archive.read(force_name)),delimiter=',',ndmin=2)
            if len(x)!=len(f) or len(x)<8:
                skipped += 1
                continue
            # Robot base-frame Z approach. Keep only new depths up to the deepest
            # sample, and use force at exactly the same sample indices.
            depth = (x[0,2]-x[:,2])*1000
            end = int(np.argmax(depth))+1
            idx = np.flatnonzero(np.diff(np.r_[-np.inf,np.maximum.accumulate(depth[:end])])>1e-6)
            if len(idx)<8 or depth[idx[-1]]-depth[idx[0]]<.2:
                skipped += 1
                continue
            row = {'force':f[idx,2].tolist(),'displacement':depth[idx].tolist(),
                   'units':{'force':'N','displacement':'mm'}}
            rows.append(press_features(row))
            # The configured offset is the benchmark label, independent of final quality.
            offset = p.parent.name
            labels.append(int(offset not in ('0','x+0','x-0','y+0','y-0')))
            groups.append(p.parent.parent.name)
            if example is None:
                example = row
    if example is None:
        raise ValueError('No aligned Copper Rings force/displacement curves')
    context = {'machine_id':'EXAMPLE','part_number':'COPPER','program':'benchmark-v1'}
    payload = {'process':'pressfit','cycle_id':'example-copper','lot_id':'EXAMPLE',
               'demo':True,'context':context,'rows':[example]}
    verify = out/'pressfit-verification-state'
    write_json(verify/'pilot-active.json',json.loads((state/'pilot-active.json').read_text(encoding='utf-8')))
    result = PilotEngine(verify).submit(payload)
    if len(result['rows'])!=1 or result['rows'][0]['quality_prediction'] is not None:
        raise ValueError('Unexpected pressfit pilot result')
    write_json(out/'pressfit_copper_example.json',payload)
    return {'aligned_cycles':len(rows),'skipped':skipped,'offset_cycles':sum(labels),'nominal_cycles':len(labels)-sum(labels),
            'proxy_label':'configured_position_offset','holdout_by_slot':proxy_metrics(pd.DataFrame(rows).to_numpy(),
                np.asarray(labels),np.asarray(groups)), 'pilot_mode':result['mode']}


def crimp(z):
    # Only the exact Harvard Dataverse V3 file previously verified at download
    # is accepted. Do not deserialize any user-supplied replacement pickle.
    data = z.read('Pressfit/Crimp_Proxy/crimp_force_curves_dataset.pkl')
    if hashlib.sha256(data).hexdigest()!='18fcc4c4f450526cd76187a9a55f0ee4446af9d9d5b2ae8d34a0207728e26b7f':
        raise ValueError('Unrecognized Crimp V3 source checksum')
    d = pd.read_pickle(io.BytesIO(data))
    if d.CrimpID.duplicated().any() or not set(d.Binary_label_encoded)<={0,1}:
        raise ValueError('Invalid Crimp sample IDs or labels')
    features = pd.DataFrame([waveform(x) for x in d.Force_curve_RoI])
    groups = d['Wire_cross-section_conductor'].to_numpy()
    y = d.Binary_label_encoded.to_numpy(int)
    return {'rows':len(d),'target':'crimp_preparation_label','axes':'sensor_values_and_sample_index',
            'holdout_by_wire_section':proxy_metrics(features.to_numpy(),y,groups)}


def pressfit_benchmark(path,state,out):
    with zipfile.ZipFile(path) as z:
        return {'copper':copper(z,state,out),'crimp':crimp(z),
                'industrial_interference_fit_quality_trained':False}
