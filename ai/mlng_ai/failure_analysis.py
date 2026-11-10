"""Validation-only failure diagnostics for a frozen neural candidate.
No test scores, threshold tuning, weight writes or engine promotion.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import numpy as np
import pandas as pd
from .shared_private import file_hash,restore,probabilities
from .evaluate_processes import metrics
from .strict_evaluation import threshold_on_validation

def cohort_metrics(meta,y,scores,threshold):
    rows=[]
    for column in ('tool_id','machine_id','scenario','class_value','condition_family','workpiece_location','fault_type'):
        if column not in meta:continue
        for name,positions in meta.groupby(column,dropna=False,sort=True).indices.items():
            ids=np.asarray(positions);m=metrics(y[ids],scores[ids],threshold)
            rows.append({'field':column,'cohort':str(name),**m,
                         'limited_support':m['positives']<10 or m['negatives']<10})
    return rows

def drift_table(train,val,train_y,columns):
    p=len(columns);rows=[]
    for j,c in enumerate(columns):
        a=train[:,j];b=val[:,j];normal=a[train_y==0];bad=a[train_y==1]
        scale=float(np.sqrt((normal.var()+bad.var())/2))
        rows.append({'feature':c,'train_mean_z':float(a.mean()),'validation_mean_z':float(b.mean()),
              'mean_shift_z':float(b.mean()-a.mean()),'outside_training_range':float(((b<a.min())|(b>a.max())).mean()),
              'train_missing':float(train[:,p+j].mean()),'validation_missing':float(val[:,p+j].mean()),
              'training_class_effect_size':float((bad.mean()-normal.mean())/max(scale,1e-6))})
    return sorted(rows,key=lambda r:abs(r['mean_shift_z']),reverse=True)

def metadata(key,groups,targets,screw_dir,mct_dir,extra_dir,max_rows):
    from .mct import hmotp_table,nasa_table,stiehl_table
    if key=='screw_tightening':
        from .train_screw import load_data
        raw,_=load_data(screw_dir,feature_fn=lambda _: {})
        if len(raw)!=len(targets) or not np.array_equal(raw.faulty,targets):
            raise ValueError('Screw provenance no longer aligns with frozen inputs')
        raw['condition_family']=raw.scenario+'_'+raw.class_value.str[:1]
        result=raw[['group','scenario','class_value','condition_family','workpiece_location','workpiece_usage','faulty','nok']].copy()
        result['_group']=raw.group.astype(str).to_numpy()
        result['_split']=raw.split.astype(str).to_numpy()
        return result
    if key.startswith('mct_'):
        if key=='mct_hmotp':table,raw=hmotp_table(Path(mct_dir)/'_milg'/'hmotp_cuts.csv')
        elif key=='mct_nasa':table,raw=nasa_table()
        else:table,raw=stiehl_table(Path(mct_dir)/'_milg'/'stiehl_runs.csv')
        expected=raw.machine_id.astype(str).to_numpy() if key=='mct_stiehl' else table.df.tool_id.astype(str).to_numpy()
        if len(expected)!=len(targets) or not np.array_equal(table.df.tool_ng.to_numpy(int),targets):
            raise ValueError('MCT provenance no longer aligns with frozen inputs')
        cols=[c for c in ('tool_id','machine_id','cut_no','cut_minutes','run','wear_um','vb_mm') if c in raw]
        from .joint_engine import grouped_split
        result=raw[cols].copy();result['_group']=expected
        result['_split']=grouped_split(expected)
        return result
    if key=='aursad_screw':
        from .extra_screw import audit_aursad,aursad_features
        info,records=audit_aursad(Path(extra_dir)/'AURSAD.h5')
        aur=aursad_features(Path(extra_dir)/'AURSAD.h5',records)
        source_groups=aur.run.astype(str).to_numpy()
        if len(source_groups)!=len(targets) or not np.array_equal(aur.target.to_numpy(int),targets):
            raise ValueError('AURSAD source/target row count mismatch')
        import h5py
        with h5py.File(Path(extra_dir)/'AURSAD.h5','r') as f:
            g=f['complete_data'];ids=g['block3_values'][:,0]
            names=[c.decode() for c in g['block2_items'][:]];labels=g['block2_values'][:,names.index('label')]
            starts=np.r_[0,np.flatnonzero(np.diff(ids)!=0)+1]
            lookup={str(int(ids[i])):int(labels[i]) for i in starts}
        split=np.where(aur.run<2457,'train',np.where(aur.run<3276,'validation','test'))
        return pd.DataFrame({'fault_type':[lookup[v] for v in source_groups],
                             '_group':source_groups,'_split':split})
    from .train_causalman import JoinedZip
    import zipfile
    joined=JoinedZip(Path(mct_dir))
    try:
        with zipfile.ZipFile(joined) as z:
            with z.open('dataset0/dataset0_partially_observable.csv') as f:raw=pd.read_csv(f,nrows=max_rows)
            with z.open('dataset0/interventional_table.csv') as f:inter=pd.read_csv(f,nrows=max_rows)
    finally:joined.close()
    raw=raw.loc[inter.eq(0).all(axis=1)].reset_index(drop=True)
    cols=[f'PF_M1_T{t}_{v}' for t in (1,2) for v in ('s0','Force','sgrad','Fmax','smax')]
    cols += [c+'_relative_to_tolerance' for c in cols.copy()]
    from .adapters import input_features
    valid=np.isfinite(input_features('causalman_scalar_v1',raw,cols).to_numpy()).all(axis=1) & raw.batch_ID_num.notna().to_numpy()
    raw=raw.loc[valid].reset_index(drop=True)
    source_groups=raw.batch_ID_num.astype(str).to_numpy()
    source_target=(1-raw.Sec_C2_Machine1_ProcessResult.to_numpy(int))
    if len(source_groups)!=len(targets) or not np.array_equal(source_target,targets):
        raise ValueError('CausalMan source/target row count mismatch')
    from .joint_engine import grouped_split
    return pd.DataFrame({'batch_id':source_groups,'_group':source_groups,'_split':grouped_split(source_groups)})

def analyze(run,out,screw_dir,mct_dir,extra_dir,threads=4,max_rows=100000):
    import torch
    from threadpoolctl import threadpool_limits
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    torch.set_num_threads(threads);run=Path(run);out=Path(out)/datetime.now().strftime('%Y%m%d-%H%M%S-%f');out.mkdir(parents=True)
    frozen=json.loads((run/'frozen.json').read_text(encoding='utf-8'))
    if file_hash(run/'candidate.json')!=frozen['candidate_sha256']:raise ValueError('Frozen model changed')
    bundle=json.loads((run/'candidate.json').read_text(encoding='utf-8'));model=restore(bundle)
    report={'candidate_sha256':frozen['candidate_sha256'],'test_scored':False,'production_modified':False,'processes':{},
            'limits':'Validation diagnostics only. Cohort differences, shifts and model comparisons are hypotheses, not causal proof. Repeated rows are correlated. Condition labels do not equal measured joint strength. No thresholds or weights are changed.'}
    for key,policy in bundle['processes'].items():
        print('Analyzing',key,flush=True)
        if file_hash(run/(key+'.npz'))!=frozen['cache'][key]:raise ValueError('Frozen input cache changed')
        with np.load(run/(key+'.npz'),allow_pickle=False) as cache:
            arrays=cache['inputs'];y=cache['target']
        columns=bundle['adapters'][key]['columns']
        if any(re.search(r'(^|_)(label|target|nok|faulty|class_value|workpiece_result|tool_ng|wear_um|vb_mm)($|_)',c) for c in columns):
            raise ValueError('Outcome-like predictor in frozen contract: '+key)
        # Rebuild group IDs from the source data instead of unpickling object arrays
        # from the local NPZ cache. Source order and labels are checked against frozen targets.
        meta=metadata(key,None,y,screw_dir,mct_dir,extra_dir,max_rows)
        groups=meta['_group'].astype(str).to_numpy()
        split=meta['_split'].astype(str).to_numpy()
        if len(groups)!=len(y):raise ValueError('Frozen source/group row count mismatch: '+key)
        tr=split=='train';va=split=='validation'
        # Never forward test inputs through any network or baseline here.
        local=probabilities(model,key,arrays[va],0.)
        scores=probabilities(model,key,arrays[va],policy['shared_weight'])
        group_sets=[set(groups[split==s]) for s in ('train','validation','test')]
        if any(group_sets[i]&group_sets[j] for i,j in ((0,1),(0,2),(1,2))):
            raise ValueError('Frozen split leaks source groups: '+key)
        vm=meta.loc[va].reset_index(drop=True)
        result={'train_rows':int(tr.sum()),'validation_rows':int(va.sum()),
                'train_groups':len(set(groups[tr])),'validation_groups':len(set(groups[va])),
                'train_positive_rate':float(y[tr].mean()),'validation_positive_rate':float(y[va].mean()),
                'feature_columns':columns,'local_neural':metrics(y[va],local,policy['local_threshold']),
                'shared_neural':metrics(y[va],scores,policy['threshold']),
                'cohorts':cohort_metrics(vm,y[va],scores,policy['threshold']),
                'drift':drift_table(arrays[tr],arrays[va],y[tr],columns),'diagnostic_baselines':{}}
        with threadpool_limits(limits=threads):
            variants={'linear_full':(LogisticRegression(max_iter=1000,random_state=42),np.arange(arrays.shape[1])),
                      'tree_full':(HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=15,min_samples_leaf=20,
                                 l2_regularization=2,early_stopping=False,random_state=42),np.arange(arrays.shape[1]))}
            if key=='screw_tightening':
                from .train_screw import STATS
                base=[j for j,c in enumerate(columns) if c in {f'stage_{i}_{s}' for i in range(4) for s in STATS}]
                variants['tree_original_summaries']=(HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=15,
                    min_samples_leaf=20,l2_regularization=2,early_stopping=False,random_state=42),np.array(base+[j+len(columns) for j in base]))
                result['label_audit']=meta.groupby(['scenario','faulty','nok']).size().rename('rows').reset_index().to_dict('records')
                result['class_label_audit']=meta.groupby(['scenario','class_value']).agg(rows=('faulty','size'),
                    condition_fault_rate=('faulty','mean'),equipment_nok_rate=('nok','mean')).reset_index().to_dict('records')
            for name,(estimator,js) in variants.items():
                estimator.fit(arrays[tr][:,js],y[tr]);vs=estimator.predict_proba(arrays[va][:,js])[:,1]
                threshold=threshold_on_validation(y[va],vs,.05)
                result['diagnostic_baselines'][name]=metrics(y[va],vs,threshold)
        findings=[]
        if result['train_groups']<5:findings.append('Fewer than five independent training groups; tool/machine generalization is weakly supported.')
        if abs(result['train_positive_rate']-result['validation_positive_rate'])>.15:findings.append('Large label prevalence shift between train and validation.')
        if any(abs(d['mean_shift_z'])>1 or d['outside_training_range']>.2 for d in result['drift']):findings.append('Validation measurements shift beyond the training distribution; check calibration, conditions and coverage.')
        tree=result['diagnostic_baselines']['tree_full']
        if tree['auc']>result['local_neural']['auc']+.05:findings.append('Fixed tree baseline outperforms the neural model on the same validation inputs; investigate optimization/architecture before collecting unrelated processes.')
        if key=='screw_tightening':findings.append('Faulty is an experimental condition; equipment NOK is a different target. Do not repair either label using the other.')
        result['findings']=findings;report['processes'][key]=result
        predictions=vm.copy();predictions['target']=y[va];predictions['score']=scores;predictions['alert']=scores>policy['threshold']
        predictions['false_negative']=(y[va]==1)&~predictions.alert
        predictions.to_csv(out/(key+'_validation_predictions.csv'),index=False,encoding='utf-8-sig')
        pd.DataFrame(result['drift']).to_csv(out/(key+'_feature_shift.csv'),index=False,encoding='utf-8-sig')
        pd.DataFrame(result['cohorts']).to_csv(out/(key+'_cohorts.csv'),index=False,encoding='utf-8-sig')
        (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    lines=['# MILG 공정별 실패 진단','', '검증 구간만 분석했습니다. 최종 시험 채점·모델 변경·운영 교체는 하지 않았습니다.','',
           '| 공정 | 학습/검증 독립 묶음 | 전용 신경망 AUC | 공유 보완 AUC | 선형 AUC | 트리 AUC |','|---|---|---|---|---|---|']
    for key,r in report['processes'].items():
        lines.append(f"| {key} | {r['train_groups']}/{r['validation_groups']} | {r['local_neural']['auc']:.4f} | {r['shared_neural']['auc']:.4f} | {r['diagnostic_baselines']['linear_full']['auc']:.4f} | {r['diagnostic_baselines']['tree_full']['auc']:.4f} |")
    for key,r in report['processes'].items():
        lines+=['',f'## {key}','',f"학습 양성 비율 {r['train_positive_rate']:.2%} / 검증 {r['validation_positive_rate']:.2%}"]
        lines+=['- '+f for f in r['findings']]
        worst=sorted([c for c in r['cohorts'] if c['positives']>=10],key=lambda c:(c['recall'], -c['positives']))[:8]
        for c in worst:lines.append(f"- {c['field']}={c['cohort']}: 양성 {c['positives']}건, 놓침 {c['fn']}건, 검출률 {c['recall']:.2%}; 적은 표본 여부 {c['limited_support']}")
    lines+=['','입력 분포 차이와 모델 비교는 실패 원인에 대한 단서이며, 물리적 원인의 확정이나 새 공장 성능 증명이 아닙니다.']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8');print('Saved:',out.resolve(),flush=True)
    return out

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('run','out','screw-dir','mct-dir','extra-dir'):p.add_argument('--'+k,required=True)
    p.add_argument('--threads',type=int,default=4);p.add_argument('--max-rows',type=int,default=100000);a=p.parse_args()
    if a.threads<1 or a.max_rows<1000:p.error('threads/max-rows out of range')
    analyze(a.run,a.out,a.screw_dir,a.mct_dir,a.extra_dir,a.threads,a.max_rows)

if __name__=='__main__':main()
