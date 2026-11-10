"""Audit three additional screw datasets; isolated, optional baseline training.
No state writes, no inferred quality labels, no blending incompatible datasets.
"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile
import numpy as np
import pandas as pd
from .strict_evaluation import TrainTransform, threshold_on_validation
from .evaluate_processes import metrics

FILES = ('AURSAD.h5', 'Dataset on force and torque for fastener clamping.zip', 'Datasets.zip')
SOURCES = ('https://zenodo.org/records/4559556', 'https://data.mendeley.com/datasets/jffwrb4pcd/4',
           'https://zenodo.org/records/13878636')
EXPECTED_MD5 = {'AURSAD.h5':'08e4706cf15144761a12cb86bd071d72',
                'Datasets.zip':'612dbad4872f1a59f78ad151d61b57bc'}

def file_hashes(path):
    sha=hashlib.sha256(); md5=hashlib.md5()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024), b''):
            sha.update(chunk); md5.update(chunk)
    return {'sha256':sha.hexdigest(), 'md5':md5.hexdigest(), 'bytes':path.stat().st_size}

def describe_curve(angle, value):
    angle=np.asarray(angle,float); value=np.asarray(value,float)
    if angle.ndim!=1 or value.shape!=angle.shape or len(angle)<3 or not np.isfinite(angle).all() or not np.isfinite(value).all():
        raise ValueError('Invalid numeric curve')
    diff=np.diff(angle)
    return {'points':len(angle), 'angle_reversals':int((diff<0).sum()),
            'repeated_angles':int((diff==0).sum()), 'angle_span':float(angle[-1]-angle[0]),
            'min':float(value.min()), 'max':float(value.max())}

def audit_aursad(path):
    import h5py
    with h5py.File(path,'r') as file:
        g=file['complete_data']; n=len(g['axis1']); columns={}
        for i in range(4):
            names=[x.decode() for x in g[f'block{i}_items'][:]]
            if g[f'block{i}_values'].shape!=(n,len(names)):raise ValueError('HDF block dimensions changed')
            columns.update({c:(i,j) for j,c in enumerate(names)})
        required=['label','sample_nr','timestamp','output_double_register_25']+[f'actual_TCP_force_{i}' for i in range(6)]
        if not set(required)<=set(columns):raise ValueError('Missing AURSAD columns')
        nonfinite={}
        for i in range(4):
            data=g[f'block{i}_values']; bad=0
            for start in range(0,n,100000):bad+=int((~np.isfinite(data[start:start+100000])).sum())
            nonfinite[str(i)]=bad
        if sum(nonfinite.values()):raise ValueError('Nonfinite AURSAD measurements; investigate before training')
        def read(name):
            i,j=columns[name];return g[f'block{i}_values'][:,j]
        ids=read('sample_nr'); labels=read('label'); timestamp=read('timestamp')
        if not set(np.unique(labels))<=set(range(6)):raise ValueError('Unknown AURSAD label')
        bounds=np.r_[0,np.flatnonzero(np.diff(ids)!=0)+1,n]
        if len(bounds)-1!=len(np.unique(ids)):raise ValueError('Sample IDs appear in multiple disjoint runs')
        records=[];fingerprints=set();duplicates=0;time_reversals=0;excluded_time_resets=0
        for a,b in zip(bounds[:-1],bounds[1:]):
            lab=np.unique(labels[a:b])
            if len(lab)!=1:raise ValueError('AURSAD operation contains multiple labels')
            resets=int((np.diff(timestamp[a:b])<=0).sum())
            if lab[0]==5:
                excluded_time_resets+=resets
                continue
            time_reversals+=resets
            # Only physical measurements; no result/status/command/position registers.
            torque=read_slice(g,columns,'output_double_register_25',a,b)
            digest=hashlib.sha256(torque.tobytes()).hexdigest()
            if digest in fingerprints:duplicates+=1
            fingerprints.add(digest)
            records.append({'sample_nr':int(ids[a]),'label':int(lab[0]),'frames':int(b-a)})
        if time_reversals:raise ValueError('Nonmonotone within-operation time')
    return {'rows':n,'columns':len(columns),'nonfinite_values':nonfinite,
            'tightening_operations':len(records),'label_counts':dict(Counter(r['label'] for r in records)),
            'loosening_operations':len(bounds)-1-len(records),'exact_torque_duplicates':duplicates,
            'excluded_loosening_time_resets':excluded_time_resets,'tightening_time_resets':time_reversals,
            'limits':'Repeated physical screws/holes are not identified uniquely. Chronological run split is not physical-part or factory holdout.'}, records

def read_slice(g,columns,name,a,b):
    i,j=columns[name]; return g[f'block{i}_values'][a:b,j]

def load_clamping(path):
    rows=[];audits=[]
    with zipfile.ZipFile(path) as archive:
        if archive.testzip():raise ValueError('Clamping ZIP CRC failed')
        names=archive.namelist()
        table=next(n for n in names if n.endswith('/4-DOE_tables/table_2.csv'))
        doe=pd.read_csv(io.BytesIO(archive.read(table)),sep=';',encoding='utf-8-sig')
        doe.columns=[c.strip() for c in doe]
        doe=doe.apply(pd.to_numeric,errors='coerce').dropna(subset=['Run #'])
        if len(doe)!=48 or doe['Run #'].nunique()!=48:raise ValueError('Expected 48 DOE runs')
        for row in doe.to_dict('records'):
            run=int(row['Run #']); curves={}
            for kind,suffix in [('torque','t'),('force','f')]:
                name=next(n for n in names if n.endswith(f'/raw_data_{kind}_test_{run:02}{suffix}.csv'))
                d=pd.read_csv(io.BytesIO(archive.read(name))).to_numpy(float)
                if d.shape[1]!=2:raise ValueError('Unknown curve columns')
                audits.append({'file':name,**describe_curve(d[:,0],d[:,1])});curves[kind]=d
            torque=curves['torque']; x=signal_features(torque[:,1],'torque')
            x['angle_span']=float(torque[-1,0]-torque[0,0])
            x.update(run=run,condition='|'.join(str(row[k]) for k in ('μ','V','KTL nut','h')),
                     preload_kN=float(row['P']), stick_slip_factor=float(row['fss']))
            rows.append(x)
    return pd.DataFrame(rows), {'runs':len(rows),'curves':audits,
             'limits':'48 trials only. Speed units disagree between source files/paper; speed excluded. Preload is a measured continuous target, not a failure label. Force curves excluded from preload predictors.'}

def audit_m2m3(path):
    import openpyxl
    curves=[];skipped=[];seen={};duplicates=[];partial=0
    with zipfile.ZipFile(path) as archive:
        if archive.testzip():raise ValueError('M2/M3 ZIP CRC failed')
        for name in archive.namelist():
            if not name.endswith('.xlsx'):continue
            if 'Gaussian Constant' in name:
                skipped.append(name);continue
            book=openpyxl.load_workbook(io.BytesIO(archive.read(name)),read_only=True,data_only=True)
            for sheet in book:
                if sheet.title!='Test Data':
                    skipped.append(name+'::'+sheet.title);continue
                data=list(sheet.iter_rows(values_only=True)); headers=data[2]
                for col in range(0,len(headers),2):
                    if 'Angle' not in str(headers[col]) or col+1>=len(headers) or 'Torque' not in str(headers[col+1]):
                        raise ValueError('Unknown raw curve spreadsheet layout')
                    points=[]
                    for row in data[3:]:
                        a,v=row[col:col+2]
                        if a is None and v is None:continue
                        if a is None or v is None:
                            partial+=1;continue
                        points.append((float(a),float(v)))
                    arr=np.array(points)
                    if partial:raise ValueError('One-sided missing raw measurements')
                    digest=hashlib.sha256(arr.astype('<f8').tobytes()).hexdigest()
                    ident=name+'::'+str(headers[col])
                    if digest in seen:duplicates.append([seen[digest],ident])
                    seen[digest]=ident
                    curves.append({'curve':ident,'sha256':digest,**describe_curve(arr[:,0],arr[:,1])})
            book.close()
    return {'raw_curves':len(curves),'curves':curves,'exact_duplicates':duplicates,'excluded_sheets':skipped,
            'supervised_quality_training':False,
            'limits':'No verified independent defect/strength labels. Raw Test Data only; GPR/simulated/calculated/fitting sheets excluded. Torque units must be reconciled before sharing features with other datasets.'}

def signal_features(values,prefix):
    a=np.asarray(values,float); result={}
    for k,v in [('mean',a.mean()),('std',a.std()),('min',a.min()),('max',a.max()),
                ('q10',np.quantile(a,.1)),('q50',np.quantile(a,.5)),('q90',np.quantile(a,.9)),
                ('delta_rms',np.sqrt(np.mean(np.diff(a)**2)))]:result[prefix+'_'+k]=float(v)
    # Preserve ordered shape at a fixed relative progress, without treating points as independent trials.
    for i,v in enumerate(np.interp(np.linspace(0,1,24),np.linspace(0,1,len(a)),a)):
        result[f'{prefix}_shape_{i}']=float(v)
    return result

def aursad_features(path,records):
    import h5py
    rows=[];wanted={r['sample_nr'] for r in records};signatures={}
    channels=['output_double_register_25']+[f'actual_TCP_force_{i}' for i in range(6)]
    with h5py.File(path,'r') as file:
        g=file['complete_data'];columns={}
        for i in range(4):columns.update({c.decode():(i,j) for j,c in enumerate(g[f'block{i}_items'][:])})
        ids=read_slice(g,columns,'sample_nr',0,len(g['axis1']));bounds=np.r_[0,np.flatnonzero(np.diff(ids)!=0)+1,len(ids)]
        label_by_id={r['sample_nr']:r['label'] for r in records}
        for a,b in zip(bounds[:-1],bounds[1:]):
            run=int(ids[a])
            if run not in wanted:continue
            raw=np.column_stack([read_slice(g,columns,c,a,b) for c in channels])
            digest=hashlib.sha256(raw.tobytes()).hexdigest();target=int(label_by_id[run]!=0)
            signatures.setdefault(digest,[]).append(target)
            x={}
            for j,c in enumerate(channels):x.update(signal_features(raw[:,j],c))
            # IDs retained for chronological partition and audit only.
            x.update(run=run,fault_type=label_by_id[run],target=target,signature=digest);rows.append(x)
    # Drop EVERY occurrence of any nonunique complete sensor sequence. This is
    # independent of labels and avoids using test outcomes to choose a training
    # copy or repair an annotation. Do not just keep the earliest labelled copy.
    df=pd.DataFrame(rows);keep=~df.signature.duplicated(keep=False)
    clean=df.loc[keep].drop(columns='signature').copy()
    clean.attrs['cleaning']={'input_operations':len(df),'excluded_nonunique_operations':int((~keep).sum()),
          'duplicate_signature_groups':sum(len(v)>1 for v in signatures.values()),
          'binary_label_conflicting_groups_for_audit_only':sum(len(set(v))>1 for v in signatures.values()),
          'retained_operations':len(clean),'policy':'Remove all copies of every nonunique seven-channel sequence, without consulting labels.'}
    return clean

def train_aursad(path,records,out,fpr,features=None):
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    import joblib
    df=aursad_features(path,records) if features is None else features.copy()
    columns=[c for c in df if c not in ('run','fault_type','target')]
    # Fixed acquisition-order split, chosen without labels. No shuffled frame split.
    split=np.where(df.run<2457,'train',np.where(df.run<3276,'validation','test'));df['split']=split
    sets=[df.loc[df.split==s] for s in ('train','validation','test')]
    if any(set(d.target)!={0,1} for d in sets):raise ValueError('Both classes required in each split')
    scaler=TrainTransform().fit(sets[0][columns]); a,b,c=[scaler.transform(d[columns]) for d in sets]
    models={'linear':LogisticRegression(max_iter=1200,random_state=42),
            'nonlinear':HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=10,
                min_samples_leaf=20,l2_regularization=2,early_stopping=False,random_state=42)}
    report={}; predictions=[]
    for name,model in models.items():
        print('Training AURSAD',name,flush=True);model.fit(a,sets[0].target)
        vs=model.predict_proba(b)[:,1];threshold=threshold_on_validation(sets[1].target,vs,fpr)
        scores=model.predict_proba(c)[:,1]
        report[name]={'validation':metrics(sets[1].target,vs,threshold),'test':metrics(sets[2].target,scores,threshold),
             'test_by_fault_type':{str(k):{'rows':int((sets[2].fault_type==k).sum()),
                  'alert_rate':float((scores[(sets[2].fault_type==k).to_numpy()]>threshold).mean()) if (sets[2].fault_type==k).any() else None} for k in range(5)}}
        joblib.dump({'model':model,'preprocessing':scaler.export(),'threshold':threshold,
                     'process':'screw_tightening','dataset':'AURSAD','target':'experimental_anomaly',
                     'feature_recipe':'aursad_post_operation_progress_shapes_v1','source':SOURCES[0]},out/('aursad_'+name+'.joblib'))
        p=sets[2][['run','fault_type','target']].copy();p['candidate']=name;p['score']=scores;predictions.append(p)
    eligible=[k for k,v in report.items() if v['validation']['false_positive_rate']<=fpr+1e-12]
    selected=max(eligible,key=lambda k:(report[k]['validation']['recall'],report[k]['validation']['precision'] or 0)) if eligible else None
    df[['run','fault_type','target','split']].to_csv(out/'aursad_split.csv',index=False)
    pd.concat(predictions).to_csv(out/'aursad_predictions.csv',index=False)
    return {'selected_on_validation':selected,'candidates':report,
            'cleaning':df.attrs.get('cleaning',{}),
            'partition_counts':{s:{str(k):int(v) for k,v in d.fault_type.value_counts().items()} for s,d in zip(('train','validation','test'),sets)},
            'limits':'Post-operation binary anomaly benchmark, not early warning or strength prediction. Types 2 and 4 are absent from this chronological test; do not claim their validation. Physical screw/plate reuse may remain.'}

def train_clamping(df,out):
    from sklearn.linear_model import Ridge
    from sklearn.metrics import mean_absolute_error,mean_squared_error
    import joblib
    cols=[c for c in df if c not in ('run','condition','preload_kN','stick_slip_factor')]
    predictions=[]
    # Leave all replicates of one complete experimental condition out together.
    for condition in sorted(df.condition.unique()):
        train=df.loc[df.condition!=condition];test=df.loc[df.condition==condition]
        scaler=TrainTransform().fit(train[cols]); model=Ridge(alpha=10.)
        model.fit(scaler.transform(train[cols]),train.preload_kN)
        pred=model.predict(scaler.transform(test[cols]))
        for run,y,p in zip(test.run,test.preload_kN,pred):
            predictions.append({'run':int(run),'target_kN':float(y),'prediction_kN':float(p),
                                'train_mean_baseline_kN':float(train.preload_kN.mean())})
    p=pd.DataFrame(predictions);p.to_csv(out/'clamping_group_cv_predictions.csv',index=False)
    scaler=TrainTransform().fit(df[cols]); model=Ridge(alpha=10.);model.fit(scaler.transform(df[cols]),df.preload_kN)
    joblib.dump({'model':model,'preprocessing':scaler.export(),'target':'preload_kN',
                 'dataset':'clamping_48_trials','source':SOURCES[1],'inference_scope':'post_operation'},out/'clamping_preload_ridge.joblib')
    return {'rows':len(df),'held_out_conditions':df.condition.nunique(),
            'mae_kN':float(mean_absolute_error(p.target_kN,p.prediction_kN)),
            'rmse_kN':float(np.sqrt(mean_squared_error(p.target_kN,p.prediction_kN))),
            'train_mean_baseline_mae_kN':float(mean_absolute_error(p.target_kN,p.train_mean_baseline_kN)),
            'limits':'Fixed-alpha Ridge; no tuning on held-out results. Final artifact fit on all 48 rows after grouped evaluation. Small-domain regression candidate only; not a deployable preload safety judgement.'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',required=True);p.add_argument('--out',required=True)
    p.add_argument('--train',choices=('aursad','clamping','both'));p.add_argument('--threads',type=int,default=4)
    p.add_argument('--target-fpr',type=float,default=.05);args=p.parse_args()
    if args.threads<1 or not 0<args.target_fpr<1:p.error('Invalid configuration')
    root=Path(args.data_dir);out=Path(args.out)/datetime.now().strftime('%Y%m%d-%H%M%S-%f');out.mkdir(parents=True)
    report={'production_modified':False,'manifest':[],'source_licenses':'CC BY 4.0 in publisher metadata; retain source attribution with artifacts.'}
    for name,source in zip(FILES,SOURCES):
        path=root/name
        print('Hashing',name,flush=True);info=file_hashes(path)
        if name in EXPECTED_MD5 and info['md5']!=EXPECTED_MD5[name]:raise ValueError('Publisher checksum mismatch: '+name)
        report['manifest'].append({'file':str(path.resolve()),'source':source,**info})
    print('Auditing complete AURSAD numeric blocks',flush=True)
    report['aursad'],records=audit_aursad(root/FILES[0])
    print('Checking full seven-channel operation duplicates',flush=True)
    aursad_clean=aursad_features(root/FILES[0],records)
    report['aursad']['cleaning']=aursad_clean.attrs['cleaning']
    print('Auditing clamping raw curves and DOE',flush=True)
    clamp,report['clamping']=load_clamping(root/FILES[1])
    print('Auditing M2/M3 raw spreadsheets',flush=True)
    report['m2m3']=audit_m2m3(root/FILES[2])
    (out/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if args.train:
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=args.threads):
            if args.train in ('aursad','both'):report['aursad_training']=train_aursad(root/FILES[0],records,out,args.target_fpr,aursad_clean)
            if args.train in ('clamping','both'):report['clamping_training']=train_clamping(clamp,out)
        (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('AURSAD:',report['aursad']['label_counts'],flush=True)
    print('Clamping runs:',report['clamping']['runs'],'M2/M3 raw curves:',report['m2m3']['raw_curves'],flush=True)
    print('Saved:',out.resolve(),flush=True)

if __name__=='__main__':main()
