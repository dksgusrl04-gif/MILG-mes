"""Isolated CRIA insertion baselines. No operational model updates.
Two targets: post-episode failure and causal, current-frame anomaly detection.
"""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from .strict_evaluation import TrainTransform, threshold_on_validation
from .evaluate_processes import metrics

def causal_features(df):
    """Use only current/past measured state. No labels, commands or IDs."""
    chunks=df['observation.state.wrench_raw_chunk']
    lengths=df['observation.state.wrench_chunk_valid_len'].to_numpy(int)
    means=[];rms=[]
    for chunk,n in zip(chunks,lengths):
        a=np.stack(chunk).astype(float)
        if a.shape!=(4,6) or not 1<=n<=4:raise ValueError('Invalid wrench chunk')
        # Locally inspected first-frame padding repeats the earliest value on
        # the LEFT. Verify that convention instead of silently dropping data.
        if n<4 and not np.allclose(a[:4-n],a[4-n],rtol=0,atol=1e-7):
            raise ValueError('Unknown chunk padding layout')
        a=a[-n:]
        if not np.isfinite(a).all():raise ValueError('Invalid measured wrench')
        means.append(a.mean(0));rms.append(np.sqrt((a*a).mean(0)))
    pose=np.stack(df['observation.state.ee_pose']).astype(float)
    if pose.shape!=(len(df),7) or not np.isfinite(pose).all():raise ValueError('Invalid pose')
    m=np.array(means);r=np.array(rms)
    values=np.column_stack([m,r,pose[:,:3]-pose[0,:3],
        np.vstack([np.zeros(3),np.diff(pose[:,:3],axis=0)]),
        np.vstack([np.zeros(6),np.diff(m,axis=0)])])
    cols=[f'wrench_mean_{i}' for i in range(6)]+[f'wrench_rms_{i}' for i in range(6)]+[
        f'position_from_start_{i}' for i in range(3)]+[f'position_delta_{i}' for i in range(3)]+[
        f'wrench_delta_{i}' for i in range(6)]
    return pd.DataFrame(values,columns=cols)

def load_data(root):
    episode_rows=[];frames=[];manifest=[];seen=set();index=0
    for task in sorted((Path(root)/'insert').glob('insert_*')):
        files=sorted((task/'data').rglob('*.parquet'))
        info=json.loads((task/'meta'/'info.json').read_text(encoding='utf-8'))
        if len(files)!=info['total_episodes']:raise ValueError('Incomplete task download: '+task.name)
        print('Reading',task.name,len(files),'episodes',flush=True)
        for file in files:
            h=hashlib.sha256(file.read_bytes()).hexdigest()
            if h in seen:raise ValueError('Duplicate episode file: '+str(file))
            seen.add(h);df=pd.read_parquet(file)
            if not set(df.episode_label.unique())<={0,1} or df.episode_label.nunique()!=1:
                raise ValueError('Invalid episode labels')
            if not set(df.frame_label.unique())<={0,1}:raise ValueError('Invalid frame labels')
            if (np.diff(df.timestamp.to_numpy())<=0).any():raise ValueError('Nonmonotone time')
            day=str(pd.to_datetime(df.timestamp_us.iloc[0],unit='us',utc=True).date())
            x=causal_features(df)
            episode={}
            for c in x:
                # Duration/frame count deliberately excluded from model features.
                for name,value in [('mean',x[c].mean()),('std',x[c].std(ddof=0)),('max',x[c].abs().max())]:
                    episode[c+'__'+name]=float(value)
            episode.update(eid=index,task=task.name,date=day,target=1-int(df.episode_label.iloc[0]))
            episode_rows.append(episode)
            x['target']=1-df.frame_label.to_numpy(int);x['eid']=index;x['task']=task.name;x['date']=day
            frames.append(x)
            manifest.append({'eid':index,'path':str(file.relative_to(root)),'sha256':h,'date':day,'task':task.name,
                             'frames':len(df),'episode_target':episode['target']})
            index+=1
    if not frames:raise ValueError('Expected ROOT/insert/insert_*/data/*.parquet')
    return pd.DataFrame(episode_rows),pd.concat(frames,ignore_index=True),manifest

def split_episodes(episodes, holdout_task=None):
    if holdout_task:
        tasks=sorted(set(episodes.task)-{holdout_task})
        if holdout_task not in set(episodes.task) or len(tasks)<2:raise ValueError('Unknown held-out task')
        validation=set(episodes.loc[episodes.task==tasks[-1],'eid'])
        test=set(episodes.loc[episodes.task==holdout_task,'eid'])
        train=set(episodes.eid)-validation-test
        method='held-out task; last other task validation'
    else:
        days=sorted(episodes.date.unique())
        if len(days)<5:raise ValueError('Too few dates for grouped split')
        a=max(1,int(.6*len(days)));b=max(a+1,int(.8*len(days)))
        train=set(episodes.loc[episodes.date.isin(days[:a]),'eid'])
        validation=set(episodes.loc[episodes.date.isin(days[a:b]),'eid'])
        test=set(episodes.eid)-train-validation
        method='UTC acquisition date chronological 60/20/20 by date count'
    if not train or not validation or not test or train&validation or train&test or validation&test:
        raise ValueError('Invalid episode partitions')
    return {'train':sorted(train),'validation':sorted(validation),'test':sorted(test),'method':method}

def train_target(df, split, out, target_name, fpr, frames_per_episode=120):
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    import joblib
    features=[c for c in df if c not in ('target','eid','task','date')]
    train=df[df.eid.isin(split['train'])].copy()
    val=df[df.eid.isin(split['validation'])].copy();test=df[df.eid.isin(split['test'])].copy()
    if target_name=='frame_anomaly':
        # Uniform temporal sampling without looking at labels, capped per episode.
        parts=[]
        for _,g in train.groupby('eid',sort=False):
            parts.append(g.iloc[np.unique(np.linspace(0,len(g)-1,min(len(g),frames_per_episode)).astype(int))])
        train=pd.concat(parts)
    if any(len(set(x.target))!=2 for x in (train,val,test)):
        raise ValueError(target_name+': both classes required in train/validation/test')
    scaler=TrainTransform().fit(train[features])
    a,b,c=[scaler.transform(x[features]) for x in (train,val,test)]
    weights=1/train.groupby('eid').eid.transform('count').to_numpy()
    weights*=len(weights)/weights.sum()
    models={'linear':LogisticRegression(max_iter=600,random_state=42),
            'nonlinear':HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=15,
                max_bins=63,min_samples_leaf=20,l2_regularization=2,early_stopping=False,random_state=42)}
    reports={};predictions=[]
    for name,model in models.items():
        print('Training',target_name,name,'rows',len(train),flush=True)
        model.fit(a,train.target,sample_weight=weights)
        vs=model.predict_proba(b)[:,1]
        threshold=threshold_on_validation(val.target.to_numpy(),vs,fpr)
        validation=metrics(val.target,vs,threshold)
        # No use of test labels until model and threshold have been fixed.
        ts=model.predict_proba(c)[:,1]
        per_task={t:metrics(test.loc[test.task==t,'target'],ts[(test.task==t).to_numpy()],threshold) for t in sorted(test.task.unique())}
        reports[name]={'validation':validation,'test':metrics(test.target,ts,threshold),'by_task':per_task}
        artifact={'model':model,'preprocessing':scaler.export(),'threshold':threshold,
                  'feature_recipe':'cria_wrench_pose_v1','target':target_name,
                  'inference_scope':'post_episode' if target_name=='episode_failure' else 'current_and_past_frames',
                  'synthetic':False,'source':'https://huggingface.co/datasets/Joringell/CRIA'}
        joblib.dump(artifact,out/(target_name+'_'+name+'.joblib'))
        pred=test[['eid','task','date','target']].copy();pred['candidate']=name;pred['score']=ts;pred['alert']=ts>threshold;predictions.append(pred)
    eligible=[k for k,r in reports.items() if r['validation']['false_positive_rate']<=fpr+1e-12]
    selected=max(eligible,key=lambda k:(reports[k]['validation']['recall'],reports[k]['validation']['precision'] or 0)) if eligible else None
    pd.concat(predictions).to_csv(out/(target_name+'_test_predictions.csv'),index=False)
    return {'train_rows':len(train),'validation_rows':len(val),'test_rows':len(test),
            'training_weighting':'equal episode weight','feature_columns':features,
            'selected_on_validation':selected,'candidates':reports}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',required=True)
    p.add_argument('--out',required=True);p.add_argument('--target-fpr',type=float,default=.05)
    p.add_argument('--frames-per-episode',type=int,default=120);p.add_argument('--holdout-task')
    p.add_argument('--threads',type=int,default=4)
    a=p.parse_args()
    if not 0<a.target_fpr<1 or a.frames_per_episode<10 or a.threads<1:p.error('Invalid training configuration')
    from threadpoolctl import threadpool_limits
    out=Path(a.out)/datetime.now().strftime('%Y%m%d-%H%M%S-%f');out.mkdir(parents=True)
    episodes,frames,manifest=load_data(a.data_dir)
    split=split_episodes(episodes,a.holdout_task)
    (out/'manifest.json').write_text(json.dumps({'files':manifest,'split':split},indent=2),encoding='utf-8')
    print('Episodes:',{k:len(split[k]) for k in ('train','validation','test')},flush=True)
    report={'source':'CRIA insertion subset','production_modified':False,'split_method':split['method'],
            'episode_count':len(episodes),'frame_count':len(frames),'target_validation_fpr':a.target_fpr,
            'excluded_features':['episode_label','frame_label','interaction_label','source_id','IDs','timestamps','commands','episode_duration'],
            'limits':'Post-operation success prediction and per-frame annotated anomaly detection are separate tasks. Frame metrics are correlated, not independent trials; no field performance claim. No images decoded or used. No automatic deployment.'}
    with threadpool_limits(limits=a.threads):
        for name,df in [('episode_failure',episodes),('frame_anomaly',frames)]:
            report[name]=train_target(df,split,out,name,a.target_fpr,a.frames_per_episode)
            (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    lines=['# CRIA insertion evaluation','',f"Episodes: {len(episodes)} / frames: {len(frames)}",split['method'],'',
           '| target | model | recall | false positive rate | precision | AUC |','|---|---|---|---|---|---|']
    for target in ('episode_failure','frame_anomaly'):
        for model,r in report[target]['candidates'].items():
            m=r['test'];lines.append(f"| {target} | {model} | {m['recall']:.2%} | {m['false_positive_rate']:.2%} | {m['precision']:.2%} | {m['auc']:.4f} |")
    lines+=['','No production model modified. Frame anomalies do not equal final assembly failures.']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print('\n'.join(lines),flush=True);print('Saved:',out.resolve(),flush=True)

if __name__=='__main__':main()
