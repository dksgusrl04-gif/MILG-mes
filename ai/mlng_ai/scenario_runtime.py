"""Trusted scenario bundles: immutable routing, checked inputs and shadow-only decisions."""
import argparse
import fnmatch
import hashlib
import json
import shutil
import uuid
from pathlib import Path
import joblib
import numpy as np
import pandas as pd

FORMAT='milg_scenario_v1'
POLICY={'auc_min':.75,'recall_min':.5,'fpr_max':.10,'min_test_positives':5,'min_test_normals':20}

class ContextReference:
    """Frozen context/scaling/tail references; no source rows or fitting during serving."""
    def __init__(self,state):self.state=state
    def score(self,X,context,fallback=None):
        s=self.state;a=X[s['columns']].to_numpy(float);n=len(a)
        med=np.empty_like(a);scale=np.empty_like(a)
        for i,key in enumerate(context):
            ref=s['references'].get(str(key))
            if ref is None and fallback is not None:ref=s['fallback_references'].get(str(fallback[i]))
            if ref is None:ref=s['global_reference']
            med[i],scale[i]=ref
        z=np.abs((a-med)/scale);valid=np.isfinite(a).all(axis=1)
        if s['calibration']=='empirical':
            reference=s['tail_reference'];count=len(reference);mx=np.maximum(reference[-1],1e-9)
            rare=np.empty_like(z)
            for j in range(z.shape[1]):
                ge=count-np.searchsorted(reference[:,j],z[:,j],side='left')
                rare[:,j]=-np.log10((ge+1)/(count+1))
                over=z[:,j]>mx[j];rare[over,j]+=np.log10(z[over,j]/mx[j])*3
        else:rare=np.clip(z,0,s['clip'])
        if s['combine']=='group':
            rare=np.stack([rare[:,np.asarray(s['group_ids'])==g].mean(axis=1) for g in range(len(s['group_names']))],axis=1)
            names=np.asarray(s['group_names'])
        else:names=np.asarray(s['columns'])
        order=np.argsort(-rare,axis=1)[:,:s['top_k']]
        score=(rare[np.arange(n),order[:,0]] if s['combine']=='group' else np.take_along_axis(rare,order,axis=1).mean(axis=1))
        top=names[order[:,0]].astype(object)
        score[~valid]=np.nan;top[~valid]='sensor_missing'
        return score,top


def binary_gate(report):
    m=report['test'];validation=report.get('validation',{})
    return bool(m.get('roc_auc') is not None and m['roc_auc']>=POLICY['auc_min']
        and m.get('recall',0)>=POLICY['recall_min'] and m['confusion']['fp']/max(1,m['confusion']['fp']+m['confusion']['tn'])<=POLICY['fpr_max']
        and m.get('n_ng',0)>=POLICY['min_test_positives'] and m.get('n',0)-m.get('n_ng',0)>=POLICY['min_test_normals']
        and validation.get('recall',0)>=POLICY['recall_min'] and validation.get('false_positive_rate',1)<=POLICY['fpr_max']
        and (report.get('threshold') or m.get('threshold',0))>0
        and report.get('beats_baseline',False))


def save_bundle(out_dir,model,columns,cfg,report,threshold,context_columns=(),extra=None):
    """Write a candidate even when it fails; registration checks the full gate."""
    task=cfg['task'];passed=False
    if task=='binary':passed=binary_gate(report)
    elif task=='regression':
        passed=report['test'].get('n',0)>=20 and report['test'].get('mae',float('inf'))<.9*report['test_baseline'].get('mae',0) and report.get('beats_baseline',False)
    elif task=='multiclass':
        passed=report['test'].get('n',0)>=60 and report['test'].get('macro_f1',0)>=.75 and report.get('beats_baseline',False)
    elif task=='anomaly':
        e=report.get('label_eval') or {};normal=bool(cfg.get('anomaly',{}).get('normal_only',False))
        if normal:
            passed=report['test']['n']>=30 and sum(report['test'].get(k) or 0 for k in ('warn_rate','ng_rate'))<=POLICY['fpr_max']
        elif e:
            passed=(e.get('auc',0)>=POLICY['auc_min'] and e.get('test_defects',0)>=5
                    and e.get('test_units',0)-e.get('test_defects',0)>=20
                    and e.get('defects_caught_warn_or_ng',0)/max(e.get('test_defects',0),1)>=POLICY['recall_min']
                    and (e.get('false_alarm_warn_or_ng') if e.get('false_alarm_warn_or_ng') is not None else 1)<=POLICY['fpr_max'])
        passed=passed and report.get('invalid_input_units',0)==0
    notes=(' '.join(map(str,cfg.get('notes',[])))+' '+str(cfg.get('unit',{}))).replace('_','-')
    research_only=bool('BY-NC' in notes.upper() or '비상업' in notes or '비상업' in str(cfg.get('unit',{})))
    passed=passed and report.get('invalid_test_units',0)==0
    gate={'passed':bool(passed and not research_only),'policy':POLICY,'research_only':research_only,
          'scope':'anomaly_reference_only' if task=='anomaly' else 'regression_reference' if task=='regression' else 'dataset_quality_classifier'}
    report['deployment_gate']=gate
    artifact={'format':FORMAT,'task':task,'model':model,'columns':list(columns),'context_columns':list(context_columns),
              'threshold':threshold,'target':{'anomaly':'scenario_anomaly','binary':'scenario_quality_score','regression':'scenario_estimate','multiclass':'scenario_class'}[task],
              'evaluation_gate':gate,'evaluation':report.get('test'), 'scenario':cfg['scenario'],
              'engine_version':report['engine_version'],'input_stage':'completed_unit_features',
              'template':cfg,'source_license':None,**(extra or {})}
    import sklearn,platform
    artifact['training_environment']={'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__,'scikit_learn':sklearn.__version__}
    artifact['feature_recipe']={'unit':cfg.get('unit'),'derive':cfg.get('derive',[]),'trailing_baseline':cfg.get('features',{}).get('trailing_baseline')}
    artifact['template_sha256']=hashlib.sha256(json.dumps(cfg,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    out=Path(out_dir);path=out/'candidate.joblib';joblib.dump(artifact,path)
    contract={k:artifact[k] for k in ('format','task','columns','context_columns','scenario','input_stage','engine_version','evaluation_gate')}
    contract['artifact_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    (out/'input-contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
    report['model_bundle']='candidate.joblib'
    return path


def frame_for(artifact,df):
    x=df.reindex(columns=artifact['columns']).apply(pd.to_numeric,errors='coerce').astype(float)
    context=df.reindex(columns=artifact.get('context_columns',[])).copy()
    return x,context


def predict_artifact(a,df):
    x,context=frame_for(a,df);valid=np.isfinite(x.to_numpy()).all(axis=1) & context.notna().all(axis=1).to_numpy()
    out=[];score=np.full(len(df),np.nan);top=[[] for _ in range(len(df))]
    classes_pred=None
    if a['task'] in ('regression','multiclass') and valid.any():
        predicted=a['model'].predict(x.loc[valid])
        if a['task']=='regression':score[valid]=predicted
        else:
            classes_pred=np.full(len(df),'',dtype=object);classes_pred[valid]=predicted.astype(str)
            score[valid]=a['model'].predict_proba(x.loc[valid]).max(axis=1)
    elif a['task']=='binary' and valid.any():
        model=a['model'];p=model.predict_proba(x.loc[valid]);classes=list(model.classes_)
        score[valid]=p[:,classes.index(1)]
        est=model.steps[-1][1] if hasattr(model,'steps') else model
        if hasattr(est,'explain'):
            explanations=est.explain(x.loc[valid])
            for i,ex in zip(np.flatnonzero(valid),explanations):
                top[i]=[{'feature':r['feature'],'standardized_value':r['z'],'contribution':r['contrib'],
                         **({'measured_value':float(x.iloc[i][r['feature']])} if r['feature'] in x.columns else {})} for r in ex]
    elif a['task']=='anomaly':
        safe_context=context.astype(object).where(context.notna(),'__missing__').astype(str)
        keys=safe_context[a.get('anomaly_context_columns',a['context_columns'])].agg('|'.join,axis=1).to_numpy() if len(a.get('anomaly_context_columns',a['context_columns'])) else np.array(['all']*len(df))
        fb=a.get('fallback_columns',[])
        fbkeys=safe_context[fb].agg('|'.join,axis=1).to_numpy() if fb else None
        if isinstance(a['model'],ContextReference):score,names=a['model'].score(x,keys,fbkeys)
        else:score,names=a['model'].score(x,keys)
        top=[[{'feature':str(name),'method':'normal_reference_deviation'}] if valid[i] else [] for i,name in enumerate(names)]
    for i,value in enumerate(score):
        usable=bool(valid[i] and np.isfinite(value))
        if not usable:
            out.append({'i':i,'decision':'UNKNOWN','alert':None,'quality_prediction':None,'top':[],
                        'input_valid':False,'reason':'센서 누락 · 판단 불가'});continue
        if a['task']=='regression':
            dec=a['template'].get('decision',{});limit=dec.get('limit')
            benchmark='NG' if limit is not None and value>=limit else 'OK'
            row={a['target']:float(value)}
        elif a['task']=='multiclass':
            benchmark='REFERENCE';row={'predicted_class':str(classes_pred[i]),'class_score':float(value)}
        elif a['task']=='binary':
            benchmark='NG' if value>=a['threshold'] else 'OK'
            row={'p':float(value),'risk':float(value)}
        else:
            thresholds=a['threshold'];ctx='|'.join(str(df.iloc[i][c]) for c in a.get('anomaly_context_columns',a['context_columns'])) or 'all'
            warn,ng=thresholds.get('contexts',{}).get(ctx,[thresholds['warn'],thresholds['ng']])
            benchmark='NG' if value>ng else 'WARN' if value>warn else 'OK'
            row={'anomaly_score':float(value)}
        out.append({'i':i,**row,'decision':'REVIEW' if benchmark in ('NG','WARN') else 'REFERENCE',
                    'benchmark_decision':benchmark,'alert':None,'benchmark_alert':None if a['task']=='multiclass' else benchmark in ('NG','WARN'),
                    'quality_prediction':None,'input_valid':True,'top':top[i]})
    return {'mode':'registered_scenario','target':a['target'],'decision_status':'scenario_reference',
            'input_contract':'scenario_numeric_v1','scenario':a['scenario'],'engine_version':a['engine_version'],'process_label':a['template'].get('process',a['scenario']),
            'input_stage':a['input_stage'],'rows':out,'field_validated':False,
            'probability_note':'공개 데이터 기반 참고 분석 · 현장 합불/설비 제어 권한 없음'}


def register(router,process,source,approved_by):
    a=joblib.load(source)
    if a.get('format')!=FORMAT or a.get('task') not in ('binary','anomaly','regression','multiclass'):raise ValueError('Unsupported scenario bundle')
    if a.get('evaluation_gate',{}).get('passed') is not True:raise ValueError('Scenario evaluation gate failed')
    if a['evaluation_gate'].get('research_only'):raise ValueError('Research-only scenario cannot enter commercial runtime')
    if a.get('source_license') not in ('CC-BY-4.0','CC0-1.0','MIT','Etalab-2.0','KAMP-permitted','customer-authorized'):
        raise ValueError('Explicit verified source license required')
    folder=router.directory/'routed_models'/uuid.uuid4().hex;folder.mkdir(parents=True)
    dest=folder/'scenario.joblib';shutil.copyfile(source,dest)
    entry={'kind':'scenario','approved_by':approved_by,'model':dest.relative_to(router.directory).as_posix(),
           'sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),'input_contract':'scenario_numeric_v1',
           'feature_columns':a['columns']+a['context_columns'],'synthetic':False}
    routes=router.routes();routes[process]=entry
    from .pilot import write_json
    if router.registry.exists():shutil.copyfile(router.registry,router.directory/'routes.previous.json')
    write_json(router.registry,routes)
    return entry


def predict(router,process,route,df):
    path=router.directory/route['model']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=route['sha256']:raise ValueError('Scenario checksum mismatch')
    result=predict_artifact(joblib.load(path),df);result['process']=process;result['adapter']='scenario';return result


def install(candidate,state,process,license,approved_by,out):
    a=joblib.load(candidate)
    if a.get('evaluation_gate',{}).get('passed') is not True:raise ValueError('Scenario evaluation gate failed; existing release retained')
    a['source_license']=license;a['approved_by']=approved_by
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True);path=out/'approved-scenario.joblib';joblib.dump(a,path)
    example=Path(candidate).parent/'example-features.json'
    sample=pd.DataFrame([json.loads(example.read_text(encoding='utf-8'))])
    from .train_process_packs import activate
    return activate(Path(state).resolve(),out,{process:(path,{'test':a['evaluation']},sample)},
                    {process:{'mode':'scenario_reference','source_license':license,'input_stage':a['input_stage'],'template_sha256':a.get('template_sha256'),'alert_enabled':False,'auto_route':False}},
                    route_kind='scenario',approved_by=approved_by)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--candidate',required=True,type=Path)
    p.add_argument('--state',required=True,type=Path);p.add_argument('--process',required=True)
    p.add_argument('--license',required=True);p.add_argument('--approved-by',required=True);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();print(install(a.candidate,a.state,a.process,a.license,a.approved_by,a.out))
if __name__=='__main__':main()
