"""Three-way grouped validation with training-only, explicitly whitelisted inputs.
This benchmark refits candidates and never modifies operational models.
"""
import argparse
import json
import zipfile
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from .model import CumulativeModel
from .train_causalman import JoinedZip
from .evaluate_processes import metrics

class TrainTransform:
    def fit(self, df):
        self.columns=list(df.columns)
        a=df.to_numpy(float,copy=True);a[~np.isfinite(a)]=np.nan
        with np.errstate(all='ignore'):
            med=np.nanmedian(a,axis=0)
        self.median=np.where(np.isfinite(med),med,0.)
        filled=np.where(np.isfinite(a),a,self.median)
        self.mean=filled.mean(0);self.std=filled.std(0)
        self.std[self.std<1e-9]=1
        return self
    def transform(self, df):
        if list(df.columns)!=self.columns:raise ValueError('Input contract changed')
        a=df.to_numpy(float,copy=True);a=np.where(np.isfinite(a),a,self.median)
        return np.clip((a-self.mean)/self.std,-8,8)
    def export(self):
        return {'columns':self.columns,'imputation':self.median.tolist(),
                'mean':self.mean.tolist(),'std':self.std.tolist(),'clip':[-8,8]}

def threshold_on_validation(y, score, fpr):
    normal=np.asarray(score)[np.asarray(y)==0]
    if not len(normal):raise ValueError('No normal validation samples for threshold calibration')
    return float(np.quantile(normal,1-fpr,method='higher'))

def fit_candidates(train, y_train, validation, y_validation, test, fpr=.05, rules=None):
    """Intentionally takes NO test labels: test outcomes cannot affect fitting."""
    if len(np.unique(y_train))<2:raise ValueError('Training partition needs both classes')
    if len(np.unique(y_validation))<2:raise ValueError('Validation partition needs both classes for selection')
    transform=TrainTransform().fit(train)
    a,b,c=[transform.transform(x) for x in (train,validation,test)]
    model=CumulativeModel(list(train.columns));model.add_process('benchmark',(np.sum(y_train)+.5)/(len(y_train)+1))
    model.update([('benchmark',a,np.asarray(y_train,float),np.ones(len(a)))])
    scores={
        'statistical_model':(model.predict('benchmark',b,np.ones(len(b)))[0],model.predict('benchmark',c,np.ones(len(c)))[0]),
        'normal_deviation_rule':(np.abs(b).max(1),np.abs(c).max(1))}
    if rules is not None:scores['tolerance_deviation_rule']=rules
    outputs={}
    for name,(vs,ts) in scores.items():
        threshold=threshold_on_validation(y_validation,vs,fpr)
        outputs[name]={'threshold':threshold,'validation':metrics(y_validation,vs,threshold),'test_scores':np.asarray(ts)}
    # Selection uses validation recall subject to its target false-positive rate.
    eligible=[k for k,v in outputs.items() if v['validation']['false_positive_rate']<=fpr+1e-12]
    selected=max(eligible,key=lambda k:(outputs[k]['validation']['recall'],outputs[k]['validation']['precision'] or 0)) if eligible else None
    return outputs,selected,transform.export()

def partitions(groups, heldout):
    names=sorted(set(groups)-{heldout},key=str)
    if len(names)<2:raise ValueError('Need at least three independent groups')
    # Deterministic rotation, independent of all outcomes.
    nval=max(1,int(np.ceil(len(names)*.2)))
    offset=sum(str(heldout).encode())%len(names)
    val=set((names+names)[offset:offset+nval]);test=np.asarray(groups)==heldout
    validation=np.isin(groups,list(val));train=~(test|validation)
    return train,validation,test

def group_cv(df, y, groups, columns, fpr=.05):
    folds=[]
    for group in sorted(set(groups),key=str):
        train,val,test=partitions(groups,group)
        record={'held_out':str(group),'train_rows':int(train.sum()),'validation_rows':int(val.sum()),
                'test_rows':int(test.sum()),'train_groups':sorted(set(np.asarray(groups)[train]),key=str),
                'validation_groups':sorted(set(np.asarray(groups)[val]),key=str)}
        print('  held out',group,flush=True)
        try:
            outputs,selected,prep=fit_candidates(df.loc[train,columns],y[train],df.loc[val,columns],y[val],df.loc[test,columns],fpr)
            record.update(status='evaluated',selected_on_validation=selected,preprocessing=prep,candidates={})
            # Only now are test labels used, exclusively for scoring.
            for name,r in outputs.items():
                record['candidates'][name]={'threshold':r['threshold'],'validation':r['validation'],
                    'test':metrics(y[test],r['test_scores'],r['threshold'])}
        except ValueError as e:record.update(status='insufficient_data',reason=str(e))
        folds.append(record)
    return {'feature_columns':columns,'folds':folds,
        'summary':summarize_folds(folds),'incomplete_folds':sum(r['status']!='evaluated' for r in folds)}

def summarize_folds(folds):
    valid=[f for f in folds if f['status']=='evaluated'];summary={}
    for name in ('statistical_model','normal_deviation_rule','validation_selected'):
        results=[]
        for f in valid:
            key=f['selected_on_validation'] if name=='validation_selected' else name
            if key:results.append(f['candidates'][key]['test'])
        if not results:continue
        counts={k:sum(r[k] for r in results) for k in ('tp','fp','tn','fn','rows','positives','negatives')}
        aucs=[r['auc'] for r in results if r['auc'] is not None]
        tp,fp,tn,fn=[counts[k] for k in ('tp','fp','tn','fn')]
        summary[name]={**counts,'recall':tp/(tp+fn) if tp+fn else None,
            'false_positive_rate':fp/(fp+tn) if fp+tn else None,
            'precision':tp/(tp+fp) if tp+fp else None,
            'macro_group_auc':float(np.mean(aucs)) if aucs else None,'auc_groups':len(aucs)}
    return summary

def evaluate_mct(directory, quick, fpr):
    from .mct import hmotp_table,nasa_table,stiehl_table,ST
    from .stiehl import LABEL
    directory=Path(directory);h,hr=hmotp_table(directory/'hmotp_cuts.csv')
    tables={'hmotp':(h,hr)}
    if not quick:
        tables['nasa']=nasa_table()
        if (directory/'stiehl_runs.csv').exists():tables['stiehl']=stiehl_table(directory/'stiehl_runs.csv')
    result={}
    for key,(t,raw) in tables.items():
        if key=='nasa':cols=[c for c in ('smcAC','smcDC','vib_table','vib_spindle','AE_table','AE_spindle') if c in t.df]
        else:
            channels={'F_x','F_y','F_z','M_z','A_x','A_y','A_z'} if key=='hmotp' else set(LABEL)
            cols=[c for c in t.df if c.rpartition('_')[0] in channels and c.rpartition('_')[2] in ST]
        if not cols:raise ValueError('No whitelisted sensor features for '+key)
        y=t.df.tool_ng.to_numpy(int);groups=t.df.tool_id.to_numpy()
        print('MCT',key,'tool evaluation',flush=True)
        result[key+'_tool']=group_cv(t.df,y,groups,cols,fpr)
        if key=='stiehl':
            print('MCT Stiehl machine evaluation',flush=True)
            result['stiehl_machine']=group_cv(t.df,y,raw.machine_id.to_numpy(),cols,fpr)
    return result

def evaluate_pressfit(folder, max_rows, fpr):
    joined=JoinedZip(folder)
    try:
        with zipfile.ZipFile(joined) as z:
            with z.open('dataset0/dataset0_partially_observable.csv') as f:df=pd.read_csv(f,nrows=max_rows)
            with z.open('dataset0/interventional_table.csv') as f:inter=pd.read_csv(f,nrows=max_rows)
    finally:joined.close()
    if len(df)!=len(inter):raise ValueError('Intervention rows mismatch')
    df=df.loc[inter.eq(0).all(axis=1)].copy().reset_index(drop=True)
    raw=[f'PF_M1_T{t}_{v}' for t in (1,2) for v in ('s0','Force','sgrad','Fmax','smax')]
    X=df[raw].copy();ratios=[]
    for c in raw:
        lo,hi=c+'_LTL',c+'_UTL'
        if lo not in df or hi not in df:raise ValueError('Missing engineering limits: '+c)
        if (df[hi]<=df[lo]).any():raise ValueError('Invalid engineering tolerance')
        X[c+'_relative_to_tolerance']=(df[c]-(df[hi]+df[lo])/2)/(df[hi]-df[lo])
        ratios.append(np.abs(X[c+'_relative_to_tolerance'].to_numpy())*2)
    rule=np.array(ratios).max(0)
    labels=df.Sec_C2_Machine1_ProcessResult
    if labels.isna().any() or not set(labels.unique())<={0,1}:raise ValueError('Invalid quality labels')
    y=1-labels.to_numpy(int);batches=sorted(df.batch_ID_num.unique())
    if len(batches)<10:raise ValueError('Need >=10 batches; increase --max-rows')
    train=df.batch_ID_num.isin(batches[:int(.6*len(batches))]).to_numpy()
    val=df.batch_ID_num.isin(batches[int(.6*len(batches)):int(.8*len(batches))]).to_numpy();test=~(train|val)
    outputs,selected,prep=fit_candidates(X.loc[train],y[train],X.loc[val],y[val],X.loc[test],fpr,(rule[val],rule[test]))
    results={k:{'validation':v['validation'],'test':metrics(y[test],v['test_scores'],v['threshold'])} for k,v in outputs.items()}
    # Fixed engineering pass/fail limits are compared without test tuning.
    results['fixed_engineering_limits']={'test':metrics(y[test],rule[test],1.)}
    return {'synthetic':True,'test_previously_inspected':True,'selected_on_validation':selected,
        'partitions':{name:{'rows':int(mask.sum()),'batches':sorted(df.loc[mask,'batch_ID_num'].unique().tolist())}
                      for name,mask in [('train',train),('validation',val),('test',test)]},
        'preprocessing':prep,'candidates':results,
        'excluded_predictors':'All MpGood, ProcessResult, IDs, hidden variables and interventions',
        'limits':'Refitted scalar synthetic benchmark. Not curve learning or a new blind field trial.'}

def markdown(r):
    def fmt(v):return 'N/A' if v is None else f'{v:.2%}'
    lines=['# MILG strict validation','', '학습/검증/시험은 공구·설비·배치 단위 분리. 임계값과 후보 선택은 검증 구간만 사용.',
        '운영 모델 교체 없음. 이전 평가와 입력·분할이 달라 수치를 직접 비교하지 마세요.','',
        '| 공정/평가 | 방식 | 검출률 | 정상 오경보 | 정밀도 |','|---|---|---|---|---|']
    for key,d in r.get('mct',{}).items():
        for name,m in d['summary'].items():lines.append(f"| {key} | {name} | {fmt(m['recall'])} | {fmt(m['false_positive_rate'])} | {fmt(m['precision'])} |")
        if d['incomplete_folds']:lines.append(f"\n{key}: 평가 불가 묶음 {d['incomplete_folds']}개. JSON의 사유 확인.\n")
    for name,c in r['pressfit']['candidates'].items():
        m=c['test'];lines.append(f"| synthetic pressfit | {name} | {fmt(m['recall'])} | {fmt(m['false_positive_rate'])} | {fmt(m['precision'])} |")
    lines += ['', '## 공구·설비별 상세', '',
        '| 평가 | 시험 묶음 | 방식 | AUC | 검출률 | 정상 오경보 |',
        '|---|---|---|---|---|---|']
    for key,d in r.get('mct',{}).items():
        for fold in d['folds']:
            if fold['status']!='evaluated':
                lines.append(f"| {key} | {fold['held_out']} | 평가 불가: {fold['reason']} | N/A | N/A | N/A |")
                continue
            for name,c in fold['candidates'].items():
                m=c['test'];auc='N/A' if m['auc'] is None else f"{m['auc']:.4f}"
                lines.append(f"| {key} | {fold['held_out']} | {name} | {auc} | {fmt(m['recall'])} | {fmt(m['false_positive_rate'])} |")
    lines += ['', '검증 구간의 목표 오경보율은 시험 구간의 오경보율을 보장하지 않습니다.',
        '한 클래스만 있는 시험 묶음의 AUC는 계산하지 않습니다.',
        '시험 데이터는 이전 실험에서 이미 확인한 자료입니다. 새로운 블라인드 시험으로 해석하지 마세요.',
        '규격 비교는 공정 종료 후 얻은 측정값 기반입니다. 사전 고장 예측 성능이 아닙니다.']
    return '\n'.join(lines)+'\n'

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',required=True)
    p.add_argument('--out',required=True);p.add_argument('--max-rows',type=int,default=100000)
    p.add_argument('--target-fpr',type=float,default=.05);p.add_argument('--quick',action='store_true')
    a=p.parse_args()
    if not 0<a.target_fpr<1:p.error('target-fpr must be between 0 and 1')
    root=Path(a.data_dir);out=Path(a.out)/datetime.now().strftime('%Y%m%d-%H%M%S-%f');out.mkdir(parents=True)
    report={'target_validation_fpr':a.target_fpr,'production_modified':False,'input_mode':'explicit sensor whitelist; no schema guessing using outcomes'}
    print('1/2 pressfit grouped train/validation/test',flush=True)
    report['pressfit']=evaluate_pressfit(root,a.max_rows,a.target_fpr)
    (out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('2/2 MCT independent tool/machine evaluation',flush=True)
    report['mct']=evaluate_mct(root/'_milg',a.quick,a.target_fpr)
    (out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    (out/'report.md').write_text(markdown(report),encoding='utf-8')
    print(markdown(report),flush=True);print('Saved:',out.resolve(),flush=True)

if __name__=='__main__':main()
