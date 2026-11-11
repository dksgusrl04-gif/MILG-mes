"""Train, calibrate, group-test and atomically register MILG widget quality routes."""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime,timezone
import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from threadpoolctl import threadpool_limits
from .widget_engine import WidgetEngineModel,select_inputs
from .strict_evaluation import TrainTransform,partitions,threshold_on_validation
from .evaluate_processes import metrics
from .packs import load_tables
from .local_inputs import DROP
from .pilot import PilotEngine,write_json
from .train_process_packs import activate

POLICY={'auc_min':.75,'recall_min':.30,'test_fpr_max':.10,'validation_fpr_max':.05,'min_validation_normals':10}

def train_calibrate(train,y,validation,yval,validation_groups):
    options=[]
    validation_groups=np.asarray(validation_groups)
    for count in (8,16,24):
        cols=select_inputs(train,y,max_features=count)
        transform=TrainTransform().fit(train[cols])
        a,b=transform.transform(train[cols]),transform.transform(validation[cols])
        for strength in (.2,.5):
            model=WidgetEngineModel(tau_delta=strength).fit(a,y)
            score=model.predict_proba(b)[:,1]
            # Every calibration condition must satisfy the alarm target.
            threshold=max(threshold_on_validation(np.asarray(yval)[validation_groups==g],score[validation_groups==g],POLICY['validation_fpr_max'])
                          for g in sorted(set(validation_groups),key=str) if (np.asarray(yval)[validation_groups==g]==0).any())
            cal=metrics(yval,score,threshold)
            options.append((cal['recall'] or 0,cal['auc'] or .5,-count,-strength,model,transform,threshold,cal))
    chosen=max(options,key=lambda v:v[:4])
    _,_,_,_,model,transform,threshold,cal=chosen
    return {'model':model,'threshold':threshold,'target':'injection_nok',
            'preprocessing':transform.export(),'feature_recipe':'widget_numeric_v1',
            'selected_on_validation':{'max_features':len(transform.columns),'tau_delta':model.tau_delta},
            'validation_candidates':[{'features':-v[2],'tau_delta':-v[3],'recall':v[0],'auc':v[1]} for v in options],
            'calibration':cal},transform

def calibration_partition(groups,y,heldout):
    tr,va,te=partitions(groups,heldout)
    # Only development labels may ensure calibration has independently inspected pass/fail.
    # The held-out group's outcomes are never inspected here.
    for value in (1,0):
        need=1 if value else POLICY['min_validation_normals']
        for g in sorted(set(groups[tr]),key=str):
            if ((y==value)&va).sum()>=need:break
            if ((y==value)&(groups==g)).any():
                va|=groups==g;tr=~(va|te)
    return tr,va,te

def evaluate(t,groups,info,out):
    y=t.df[info['y']].to_numpy(float)
    x=t.df.drop(columns=[c for c in t.df if c in DROP or c==info['y']]).apply(pd.to_numeric,errors='coerce')
    groups=np.asarray(groups);folds=[];ys=[];ss=[];aa=[];index=[]
    for group in sorted(set(groups),key=str):
        tr,va,te=calibration_partition(groups,y,group)
        if len(np.unique(y[tr]))<2 or (y[va]==0).sum()<POLICY['min_validation_normals']:
            folds.append({'group':str(group),'status':'insufficient_training_or_calibration','rows':int(te.sum())})
            continue
        artifact,prep=train_calibrate(x.loc[tr],y[tr],x.loc[va],y[va],groups[va])
        score=artifact['model'].predict_proba(prep.transform(x.loc[te,prep.columns]))[:,1]
        test=metrics(y[te],score,artifact['threshold'])
        folds.append({'group':str(group),'status':'evaluated','test':test,
                      'train_groups':sorted(set(groups[tr]),key=str),'validation_groups':sorted(set(groups[va]),key=str),
                      'validation':artifact['calibration'],'threshold':artifact['threshold']})
        ys.extend(y[te]);ss.extend(score);aa.extend(score>artifact['threshold']);index.extend(np.flatnonzero(te))
        print(t.key,'held out',group,'recall',test['recall'],'fpr',test['false_positive_rate'],flush=True)
    yy=np.asarray(ys);al=np.asarray(aa);score=np.asarray(ss)
    summary={'auc':float(roc_auc_score(yy,score)) if len(np.unique(yy))==2 else None,
             'recall':float(al[yy==1].mean()) if (yy==1).any() else None,
             'false_positive_rate':float(al[yy==0].mean()) if (yy==0).any() else None,
             'evaluated_rows':len(yy),'total_rows':len(y),'coverage':len(yy)/len(y)}
    passed=summary['auc'] is not None and summary['auc']>=POLICY['auc_min'] and summary['coverage']==1 and summary['recall']>=POLICY['recall_min'] and summary['false_positive_rate']<=POLICY['test_fpr_max'] and not info.get('hold') and info['lic']!='미확인'
    tr,va,_=calibration_partition(groups,y,'final-calibration')
    if len(np.unique(y[tr]))<2 or (y[va]==0).sum()<POLICY['min_validation_normals']:
        raise ValueError('Final calibration partition has insufficient normal samples')
    artifact,prep=train_calibrate(x.loc[tr],y[tr],x.loc[va],y[va],groups[va])
    artifact.update(training_groups=sorted(set(groups[tr]),key=str),calibration_groups=sorted(set(groups[va]),key=str),
                    source=t.source,license=info['lic'],evaluation_policy=POLICY)
    passed=passed and (artifact['calibration']['recall'] or 0)>=POLICY['recall_min']
    result={'test':summary,'validation':artifact['calibration'],'folds':folds,'gate_passed':bool(passed),
            'policy':POLICY,'input_columns':prep.columns,'calibration_groups':artifact['calibration_groups'],
            'training_groups':artifact['training_groups'],'limitations':'Public development benchmark; new factory validation required. Test labels never select inputs, thresholds or model configuration.'}
    artifact['evaluation_gate']=bool(passed)
    artifact['evaluation']=result['test']
    joblib.dump(artifact,out/(t.key+'.joblib'))
    pd.DataFrame({'source_row':index,'y':yy,'score':score,'benchmark_alert':aa}).to_csv(out/(t.key+'_heldout.csv'),index=False)
    return result,out/(t.key+'.joblib'),x.loc[[x.index[0]],prep.columns]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--summaries',required=True,type=Path);p.add_argument('--state',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path);p.add_argument('--install',action='store_true')
    a=p.parse_args();out=a.out.resolve()/datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f');out.mkdir(parents=True)
    results={};artifacts={}
    with threadpool_limits(limits=2):
        for key,(t,g,info) in load_tables(a.summaries).items():
            if key!='inj_quality':continue
            r,path,sample=evaluate(t,g,info,out);results[key]=r
            if r['gate_passed']:artifacts[key]=(path,r,sample)
            write_json(out/'report.json',results)
    installed=[]
    if a.install and artifacts:
        release=activate(a.state.resolve(),out,artifacts,{k:{'purpose':'Public quality benchmark','alert_enabled':False,'auto_route':False} for k in artifacts})
        engine=PilotEngine(a.state.resolve())
        for key,(_,_,sample) in artifacts.items():
            assert engine.predict(key,sample.to_dict('records'))['rows'][0]['alert'] is None
        installed=sorted(artifacts)
    write_json(out/'deployment.json',{'installed':installed,'release':release if installed else None})
    print('RESULT',json.dumps({k:{'test':r['test'],'validation':r['validation'],'gate':r['gate_passed']} for k,r in results.items()},ensure_ascii=False),flush=True)
    print('Installed:',installed,'Report:',out,flush=True)
if __name__=='__main__':main()
