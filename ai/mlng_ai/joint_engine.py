"""MILG common-coefficient training, regression checks and engine installation.
Uses the existing CumulativeModel and semantic slots, not a separate sklearn classifier.
Rebuilds a versioned common model from replay data with train-only calibration.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import zipfile
import numpy as np
import pandas as pd
from .features import SLOTS
from .model import CumulativeModel
from .strict_evaluation import TrainTransform,threshold_on_validation
from .evaluate_processes import metrics
from .adapters import input_features,standardize

def quantity(column):
    if column.startswith(('F_','force_')) or 'TCP_force_' in column and int(column.split('TCP_force_')[1].split('_')[0])<3:return 'force'
    if column.startswith(('M_','torque_','stage_')) or 'TCP_force_' in column or column.startswith('output_double_register_25'):return 'torque'
    if column.startswith(('A_','vib','AE_')):return 'vibration'
    if column.startswith('smc'):return 'current'
    if column.startswith('PF_'):
        return 'force' if any(v in column for v in ('Force','Fmax')) else 'dimension' if any(v in column for v in ('s0','smax')) else 'unknown'
    return 'unknown'

def fit_adapter(df,recipe,target,synthetic=False):
    prep=TrainTransform().fit(df).export()
    return {'recipe':recipe,'columns':list(df.columns),'preprocessing':prep,
            'quantity_map':{c:quantity(c) for c in df},'target':target,'synthetic':synthetic,
            'semantic_version':'milg_shared_deviation_v1','slots':list(SLOTS)}

def semantic_transform(df,adapter):
    if adapter.get('semantic_version')!='milg_shared_deviation_v1' or adapter['slots']!=list(SLOTS):
        raise ValueError('Semantic adapter version/slot mismatch')
    columns=adapter['columns'];z=standardize(df,adapter['preprocessing'])
    result=pd.DataFrame(0.,index=np.arange(len(df)),columns=SLOTS)
    for q in sorted(set(adapter['quantity_map'].values())):
        js=[i for i,c in enumerate(columns) if adapter['quantity_map'][c]==q]
        result['q:'+q]=np.abs(z[:,js]).max(1)
    result['all_max']=np.abs(z).max(1);result['n_out']=(np.abs(z)>2).mean(1)
    result['missing']=(~np.isfinite(df[columns].to_numpy(float))).mean(1)
    js=[i for i,c in enumerate(columns) if c.endswith(('_std','_rms','_p2p','_delta_rms'))]
    if js:result['spread']=np.maximum(z[:,js],0).max(1)
    js=[i for i,c in enumerate(columns) if c.endswith('_slope')]
    if js:result['wave:trend']=z[:,js].mean(1)
    js=[i for i,c in enumerate(columns) if c.endswith('_delta_rms')]
    if js:result['wave:rough']=np.maximum(z[:,js],0).max(1)
    # Same ordered shape endpoints are used in training and inference.
    shape_pairs=[(columns.index(c),columns.index(c.removesuffix('_shape_0')+'_shape_23'))
                 for c in columns if c.endswith('_shape_0') and c.removesuffix('_shape_0')+'_shape_23' in columns]
    if shape_pairs:result['wave:trend']=np.mean([z[:,b]-z[:,a] for a,b in shape_pairs],axis=0)
    if not np.isfinite(result.to_numpy()).all():raise ValueError('Nonfinite semantic features')
    return result.to_numpy()

def grouped_split(groups):
    groups=np.asarray(groups).astype(str);names=sorted(set(groups))
    if len(names)<3:raise ValueError('At least three independent groups needed')
    a=max(1,min(len(names)-2,int(.6*len(names))));b=max(a+1,min(len(names)-1,int(.8*len(names))))
    return np.where(np.isin(groups,names[:a]),'train',np.where(np.isin(groups,names[a:b]),'validation','test'))

def data_part(X,y,groups,recipe,target,split=None,synthetic=False):
    X=X.reset_index(drop=True);y=np.asarray(y,int);groups=np.asarray(groups).astype(str)
    split=grouped_split(groups) if split is None else np.asarray(split)
    if len(X)!=len(y) or len(y)!=len(groups) or len(split)!=len(X):raise ValueError('Data alignment failed')
    if not set(y)<={0,1}:raise ValueError('Binary target required')
    for s in ('train','validation','test'):
        mask=split==s
        if not mask.any() or len(set(y[mask]))<2:raise ValueError('Both classes required in '+s+' for '+target)
    sets=[set(groups[split==s]) for s in ('train','validation','test')]
    if sets[0]&sets[1] or sets[0]&sets[2] or sets[1]&sets[2]:raise ValueError('Group overlap across partitions')
    adapter=fit_adapter(X.loc[split=='train'],recipe,target,synthetic)
    return {'features':X,'X':semantic_transform(X,adapter),'y':y,'groups':groups,'split':split,'adapter':adapter}

def load_benchmarks(mct_dir,screw_dir,extra_dir,max_rows,screw_v2=False):
    from .mct import hmotp_table,nasa_table,stiehl_table,ST
    from .stiehl import LABEL
    from .train_screw import load_data,STAGES,STATS
    from .train_causalman import JoinedZip
    from .extra_screw import audit_aursad,aursad_features
    root=Path(mct_dir); result={};manifest={}
    print('Loading MCT sensor summaries',flush=True)
    tables={'mct_hmotp':hmotp_table(root/'_milg'/'hmotp_cuts.csv'),'mct_nasa':nasa_table(),
            'mct_stiehl':stiehl_table(root/'_milg'/'stiehl_runs.csv')}
    for name,(table,raw) in tables.items():
        if name=='mct_nasa':cols=[c for c in ('smcAC','smcDC','vib_table','vib_spindle','AE_table','AE_spindle') if c in table.df]
        else:
            channels={'F_x','F_y','F_z','M_z','A_x','A_y','A_z'} if name=='mct_hmotp' else set(LABEL)
            cols=[c for c in table.df if c.rpartition('_')[0] in channels and c.rpartition('_')[2] in ST]
        groups=raw.machine_id if name=='mct_stiehl' else table.df.tool_id
        result[name]=data_part(table.df[cols],table.df.tool_ng,groups,'mct_sensor_columns_v1','evaluation_wear_threshold')
    print('Loading isolated synthetic press-fit measurements',flush=True)
    joined=JoinedZip(root)
    try:
        with zipfile.ZipFile(joined) as z:
            with z.open('dataset0/dataset0_partially_observable.csv') as f:raw=pd.read_csv(f,nrows=max_rows)
            with z.open('dataset0/interventional_table.csv') as f:inter=pd.read_csv(f,nrows=max_rows)
    finally:joined.close()
    if len(raw)!=len(inter):raise ValueError('CausalMan row alignment failed')
    raw=raw.loc[inter.eq(0).all(axis=1)].reset_index(drop=True)
    cols=[f'PF_M1_T{t}_{v}' for t in (1,2) for v in ('s0','Force','sgrad','Fmax','smax')]
    cols+= [c+'_relative_to_tolerance' for c in cols.copy()]
    X=input_features('causalman_scalar_v1',raw,cols)
    if not set(raw.Sec_C2_Machine1_ProcessResult.unique())<={0,1}:raise ValueError('Unexpected synthetic quality labels')
    result['causalman_pressfit']=data_part(X,1-raw.Sec_C2_Machine1_ProcessResult,raw.batch_ID_num,
                                         'causalman_scalar_v1','synthetic_process_result',synthetic=True)
    print('Loading screw curves with global duplicate and part grouping checks',flush=True)
    feature_fn=None
    if screw_v2:
        from .shared_private import screw_features_v2
        feature_fn=screw_features_v2
    screw,manifest['screw']=load_data(screw_dir,feature_fn)
    cols=[c for c in screw if c.startswith('stage_')] if screw_v2 else [f'stage_{i}_{s}' for i in range(len(STAGES)) for s in STATS]
    result['screw_tightening']=data_part(screw[cols],screw.faulty,screw.group,
              'screw_stage_torque_angle_v1','experimental_condition',screw.split)
    if screw_v2:result['screw_tightening']['adapter']['recipe']='screw_stage_torque_angle_v2'
    print('Auditing and loading AURSAD measured sequences',flush=True)
    info,records=audit_aursad(Path(extra_dir)/'AURSAD.h5');aur=aursad_features(Path(extra_dir)/'AURSAD.h5',records)
    manifest['aursad']={'audit':info,'cleaning':aur.attrs['cleaning']}
    cols=[c for c in aur if c not in ('run','fault_type','target')]
    split=np.where(aur.run<2457,'train',np.where(aur.run<3276,'validation','test'))
    result['aursad_screw']=data_part(aur[cols],aur.target,aur.run,
               'aursad_post_operation_progress_shapes_v1','experimental_anomaly',split)
    manifest['limits']='Existing public test data, not a new blind factory trial. Stiehl held out by machine; HMoTP/NASA by tool; synthetic press-fit by batch; screw by physical part; AURSAD chronological operation IDs without unique physical-part IDs. M2/M3 and preload regression are excluded from binary joint training.'
    return result,manifest

def fit_common(data,keys,batches=4):
    model=CumulativeModel(SLOTS)
    for key in keys:
        d=data[key];y=d['y'][d['split']=='train'];model.add_process(key,(y.sum()+.5)/(len(y)+1))
    parts={key:np.array_split(np.flatnonzero(data[key]['split']=='train'),batches) for key in keys}
    for b in range(batches):
        print('Common model batch',b+1,'/',batches,'processes',len(keys),flush=True)
        model.update([(key,data[key]['X'][parts[key][b]],data[key]['y'][parts[key][b]],np.ones(len(parts[key][b])))
                      for key in keys if len(parts[key][b])],lam=1.,weight_cap=400.,iters=15)
    return model

def evaluate_common(model,data):
    report={};thresholds={}
    for key in model.procs:
        d=data[key];v=d['split']=='validation';t=d['split']=='test'
        vs=model.prob(key,model.predict(key,d['X'][v],np.ones(v.sum()))[0])
        threshold=threshold_on_validation(d['y'][v],vs,data[key].get('fpr',.05))
        ts=model.prob(key,model.predict(key,d['X'][t],np.ones(t.sum()))[0]);thresholds[key]=threshold
        report[key]={'validation':metrics(d['y'][v],vs,threshold),'test':metrics(d['y'][t],ts,threshold),
                     'synthetic':d['adapter']['synthetic'],
                     'test_by_group':{g:metrics(d['y'][t][d['groups'][t]==g],ts[d['groups'][t]==g],threshold) for g in sorted(set(d['groups'][t]))}}
    return report,thresholds

def regression_gate(reference,candidate,auc_drop=.01,recall_drop=.03,fpr_rise=.02):
    failures=[];comparisons={}
    for key in reference:
        comparisons[key]={}
        for phase in ('validation','test'):
            a=reference[key][phase];b=candidate[key][phase]
            deltas={m:None if a[m] is None or b[m] is None else b[m]-a[m] for m in ('auc','recall','false_positive_rate')}
            comparisons[key][phase]=deltas
            if any(v is None for v in deltas.values()):failures.append(key+'/'+phase+': insufficient class coverage');continue
            if deltas['auc'] < -auc_drop:failures.append(key+'/'+phase+': AUC regression')
            if deltas['recall'] < -recall_drop:failures.append(key+'/'+phase+': recall regression')
            if deltas['false_positive_rate'] > fpr_rise:failures.append(key+'/'+phase+': false-alarm regression')
    return {'passed':not failures,'failures':failures,'deltas':comparisons,
            'tolerances':{'auc_drop':auc_drop,'recall_drop':recall_drop,'fpr_rise':fpr_rise},
            'limits':'Fixed gates; test results block installation but must not be used to retune thresholds. Reference is a same-split refit, not a claim of equivalence to older saved model metrics.'}

def build(args):
    from threadpoolctl import threadpool_limits
    out=Path(args.out)/datetime.now().strftime('%Y%m%d-%H%M%S-%f');out.mkdir(parents=True)
    data,manifest=load_benchmarks(args.mct_dir,args.screw_dir,args.extra_dir,args.max_rows)
    for d in data.values():d['fpr']=args.target_fpr
    for key,d in data.items():
        pd.DataFrame({'group':d['groups'],'split':d['split'],'target':d['y']}).to_csv(out/(key+'_split.csv'),index=False)
        # Persist the exact whitelisted input/adapter used, not test-dependent schema inference.
    reference_keys=[k for k in data if k.startswith('mct_') or k=='causalman_pressfit']
    with threadpool_limits(limits=args.threads):
        reference=fit_common(data,reference_keys,args.batches)
        candidate=fit_common(data,list(data),args.batches)
        before,_=evaluate_common(reference,data);after,thresholds=evaluate_common(candidate,data)
    gate=regression_gate(before,after,args.max_auc_drop,args.max_recall_drop,args.max_fpr_rise)
    for key in set(data)-set(reference_keys):
        v=after[key]['validation']
        if v['auc'] is None or v['auc']<.6 or v['recall']<=0:
            gate['failures'].append(key+'/validation: insufficient new-process discrimination')
    gate['passed']=not gate['failures']
    bundle={'format':'milg_joint_v1','model':candidate.to_dict(),
            'adapters':{k:d['adapter'] for k,d in data.items()},'thresholds':thresholds,
            'regression_gate':gate,'training':'joint replay refit using existing MILG CumulativeModel; shared beta plus process deltas'}
    (out/'joint.json').write_text(json.dumps(bundle,indent=2),encoding='utf-8')
    (out/'reference.json').write_text(json.dumps(reference.to_dict(),indent=2),encoding='utf-8')
    report={'production_modified':False,'reference':before,'joint':after,'gate':gate,'manifest':manifest,
            'shared_beta_change_l2':float(np.linalg.norm(candidate.m[:candidate.K]-reference.m[:reference.K])),
            'partitions':{k:{s:int((d['split']==s).sum()) for s in ('train','validation','test')} for k,d in data.items()},
            'limits':'This is a retrained candidate using MILG common coefficients, not raw-waveform deep learning. Synthetic press-fit remains explicitly marked. No global accuracy across different targets. Existing state/old routes are untouched until installation.'}
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    lines=['# MILG 공동 학습 평가','', '| 공정 | 모델 | AUC | 검출률 | 정상 오경보 |','|---|---|---|---|---|']
    for key in data:
        for name,values in [('기준 재학습',before),('공동 학습',after)]:
            if key not in values:continue
            m=values[key]['test'];lines.append(f"| {key} | {name} | {m['auc']:.4f} | {m['recall']:.2%} | {m['false_positive_rate']:.2%} |")
    lines+=['',f"기존 공정 성능 유지 기준 통과: {gate['passed']}",f"공통 계수 변화 L2: {report['shared_beta_change_l2']:.6f}",
            '','같은 입력·분할로 다시 학습한 기준 모델과 비교합니다. 과거 저장 모델의 수치와 직접 비교하지 않습니다.',
            'CausalMan 압입은 합성 데이터입니다. 서로 다른 판정 목표를 하나의 정확도로 합치지 않습니다.',
            '시험 구간은 이전에 확인한 공개 데이터입니다. 새로운 외부 공장 검증은 아닙니다.']
    lines+=['- '+f for f in gate['failures']]
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print('\n'.join(lines),flush=True);print('Saved:',out.resolve(),flush=True)
    return out

def install_joint(bundle,state,by):
    from .routing import Router
    artifact=json.loads(Path(bundle).read_text(encoding='utf-8'))
    if artifact.get('regression_gate',{}).get('passed') is not True:raise ValueError('Joint regression gate failed; previous routes retained')
    router=Router(state)
    previous=router.routes()
    try:
        for process in artifact['adapters']:router.register(process,bundle,'joint',by)
    except Exception:
        router.registry.write_text(json.dumps(previous,indent=2),encoding='utf-8');raise
    # One rollback restores the whole installation, not just the final process.
    (Path(state)/'routes.previous.json').write_text(json.dumps(previous,indent=2),encoding='utf-8')
    return router.routes()

def install_specialists(screw_result,extra_result,state,by):
    from .routing import Router
    router=Router(state);previous=router.routes();installed=[]
    try:
        for directory,target,prefix,process in [(screw_result,'faulty','faulty','screw_tightening'),
                      (extra_result,'aursad_training','aursad','aursad_screw')]:
            root=Path(directory);report=json.loads((root/'report.json').read_text(encoding='utf-8'))
            section=report[target];selected=section['selected_on_validation']
            if selected is None:raise ValueError('No validation-selected candidate for '+process)
            scores=section['candidates'][selected]['validation']
            if scores['auc'] is None or scores['auc']<.6 or scores['recall']<=0:
                raise ValueError('Insufficient validation discrimination for '+process)
            router.register(process,root/(prefix+'_'+selected+'.joblib'),'specialist',by);installed.append(process)
        preload=Path(extra_result)/'clamping_preload_ridge.joblib'
        if preload.exists():
            r=json.loads((Path(extra_result)/'report.json').read_text(encoding='utf-8'))['clamping_training']
            if r['mae_kN']<r['train_mean_baseline_mae_kN']:
                router.register('clamping_preload',preload,'specialist',by);installed.append('clamping_preload')
    except Exception:
        router.registry.write_text(json.dumps(previous,indent=2),encoding='utf-8');raise
    (Path(state)/'routes.previous.json').write_text(json.dumps(previous,indent=2),encoding='utf-8')
    return installed

def verify_routes(state,mct_dir,screw_dir,extra_dir,out):
    """Run actual raw inputs through Service, including automatic contract selection."""
    import io
    import h5py
    from .service import Service
    from .mct import nasa_table,stiehl_table
    from .extra_screw import read_slice
    from .train_causalman import JoinedZip
    service=Service(state);results={};routes=service.router.routes()
    with zipfile.ZipFile(next(Path(screw_dir).glob('s04_*.zip'))) as z:
        raw=json.loads(z.read(next(n for n in z.namelist() if n.endswith('.json'))))
    samples={'screw_tightening':pd.DataFrame([{'tightening steps':raw['tightening steps']}])}
    with h5py.File(Path(extra_dir)/'AURSAD.h5','r') as f:
        g=f['complete_data'];columns={}
        for i in range(4):columns.update({c.decode():(i,j) for j,c in enumerate(g[f'block{i}_items'][:])})
        ids=read_slice(g,columns,'sample_nr',0,len(g['axis1']));indices=np.flatnonzero(ids==2)
        channels=['output_double_register_25']+[f'actual_TCP_force_{i}' for i in range(6)]
        samples['aursad_screw']=pd.DataFrame([{c:read_slice(g,columns,c,int(indices[0]),int(indices[-1])+1).tolist() for c in channels}])
    with zipfile.ZipFile(Path(extra_dir)/'Dataset on force and torque for fastener clamping.zip') as z:
        n=next(n for n in z.namelist() if n.endswith('/raw_data_torque_test_01t.csv'))
        curve=pd.read_csv(io.BytesIO(z.read(n)))
        samples['clamping_preload']=pd.DataFrame([{'angle_values':curve.iloc[:,0].tolist(),'torque_values':curve.iloc[:,1].tolist()}])
    if any(r['kind'] in ('joint','joint_v2') for r in routes.values()):
        samples['mct_hmotp']=pd.read_csv(Path(mct_dir)/'_milg'/'hmotp_cuts.csv').iloc[:1]
        samples['mct_nasa']=nasa_table()[0].df.iloc[:1]
        samples['mct_stiehl']=stiehl_table(Path(mct_dir)/'_milg'/'stiehl_runs.csv')[0].df.iloc[:1]
        joined=JoinedZip(mct_dir)
        try:
            with zipfile.ZipFile(joined) as z:
                with z.open('dataset0/dataset0_partially_observable.csv') as f:samples['causalman_pressfit']=pd.read_csv(f,nrows=1)
        finally:joined.close()
    for key,df in samples.items():
        if key not in routes or routes[key]['kind'] not in ('specialist','joint','joint_v2'):continue
        explicit=service.predict(key,df)
        if len(explicit.get('rows',[]))!=len(df) or explicit['mode']!='registered_model':raise ValueError('Engine route verification failed: '+key)
        results[key]={'explicit':explicit}
        if not routes[key]['synthetic']:
            auto=service.predict('auto',df)
            if auto.get('process')!=key:raise ValueError('Automatic process routing failed: '+key)
            results[key]['automatic']=auto
    if not results:raise ValueError('No integrated routes installed')
    Path(out).write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Verified raw-input engine routes:',', '.join(results),flush=True)
    return results

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    b=sub.add_parser('build')
    for key in ('mct-dir','screw-dir','extra-dir','out'):b.add_argument('--'+key,required=True)
    b.add_argument('--max-rows',type=int,default=100000);b.add_argument('--threads',type=int,default=4)
    b.add_argument('--batches',type=int,default=4);b.add_argument('--target-fpr',type=float,default=.05)
    b.add_argument('--max-auc-drop',type=float,default=.01);b.add_argument('--max-recall-drop',type=float,default=.03);b.add_argument('--max-fpr-rise',type=float,default=.02)
    i=sub.add_parser('install-joint');i.add_argument('--bundle',required=True);i.add_argument('--state',required=True);i.add_argument('--by',required=True)
    s=sub.add_parser('install-specialists')
    for key in ('screw-result','extra-result','state','by'):s.add_argument('--'+key,required=True)
    r=sub.add_parser('predict');r.add_argument('--state',required=True);r.add_argument('--process',default='auto');r.add_argument('--input',required=True);r.add_argument('--out',required=True)
    v=sub.add_parser('verify')
    for key in ('state','mct-dir','screw-dir','extra-dir','out'):v.add_argument('--'+key,required=True)
    a=parser.parse_args()
    if a.action=='build':
        if a.max_rows<1000 or a.threads<1 or a.batches<1 or not 0<a.target_fpr<1 or min(a.max_auc_drop,a.max_recall_drop,a.max_fpr_rise)<0:parser.error('Invalid training settings')
        build(a)
    elif a.action=='install-joint':print(json.dumps(install_joint(a.bundle,a.state,a.by),indent=2))
    elif a.action=='install-specialists':print(install_specialists(a.screw_result,a.extra_result,a.state,a.by))
    elif a.action=='verify':verify_routes(a.state,a.mct_dir,a.screw_dir,a.extra_dir,a.out)
    else:
        from .service import Service
        source=json.loads(Path(a.input).read_text(encoding='utf-8'))
        df=pd.DataFrame(source if isinstance(source,list) else [source])
        result=Service(a.state).predict(a.process,df)
        Path(a.out).write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
