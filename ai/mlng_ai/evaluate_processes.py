"""Read-only model evaluation; no promotion or production-state writes.
MCT uses refitted leave-tool-out models. CausalMan replays the existing held-out
batch split; these are previously inspected test data, not a new blind test.
"""
import argparse
import json
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from .routing import Router, digest
from .train_causalman import JoinedZip

def metrics(y, score, threshold):
    y=np.asarray(y,dtype=int); score=np.asarray(score,dtype=float)
    if len(y)==0 or not np.isfinite(score).all() or not set(y)<= {0,1}:
        raise ValueError('Invalid labels or scores')
    pos=int(y.sum());neg=len(y)-pos;pred=score>threshold
    tp=int(np.sum(pred & (y==1)));fp=int(np.sum(pred & (y==0)))
    return {'rows':len(y),'positives':pos,'negatives':neg,'tp':tp,'fp':fp,
        'fn':pos-tp,'tn':neg-fp,'recall':tp/pos if pos else None,
        'precision':tp/(tp+fp) if tp+fp else None,
        'false_positive_rate':fp/neg if neg else None,
        'auc':float((rankdata(score)[y==1].sum()-pos*(pos+1)/2)/(pos*neg)) if pos*neg else None,
        'threshold':float(threshold)}

def evaluate_pressfit(data_dir, model_dir):
    model_dir=Path(model_dir)
    old=json.loads((model_dir/'report.json').read_text(encoding='utf-8'))
    prep=json.loads((model_dir/'preprocessing.json').read_text(encoding='utf-8'))
    nrows=old['requested_rows']
    joined=JoinedZip(data_dir)
    try:
        with zipfile.ZipFile(joined) as z:
            with z.open('dataset0/dataset0_partially_observable.csv') as f:df=pd.read_csv(f,nrows=nrows)
            with z.open('dataset0/interventional_table.csv') as f:inter=pd.read_csv(f,nrows=nrows)
    finally:joined.close()
    if len(df)!=len(inter):raise ValueError('Intervention row alignment mismatch')
    df=df.loc[inter.eq(0).all(axis=1)].copy()
    target=prep['target']
    if not set(df[target].dropna().unique())<= {0,1}:raise ValueError('Unexpected label encoding')
    work=df[prep['raw_columns']].astype(float).copy()
    for c in prep['columns']:
        if c.endswith('_relative_to_tolerance'):
            base=c.removesuffix('_relative_to_tolerance');lo=df[base+'_LTL'];hi=df[base+'_UTL']
            work[c]=(df[base]-(hi+lo)/2)/(hi-lo).where(hi>lo)
    valid=np.isfinite(work).all(axis=1) & df.batch_ID_num.notna() & df[target].notna()
    df=df.loc[valid];batches=sorted(df.batch_ID_num.unique())
    test=df.batch_ID_num.isin(batches[max(1,int(len(batches)*.8)):])
    if len(df)!=old['observational_valid_rows'] or int(test.sum())!=old['test_rows']:
        raise ValueError('Original split cannot be reproduced; do not report these as held-out data')
    # Use the production adapter in a temporary isolated registry, not duplicated normalization.
    with tempfile.TemporaryDirectory() as tmp:
        router=Router(tmp)
        router.register('causalman_pressfit',model_dir/'model.json','causalman',
                        'evaluation-only',model_dir/'preprocessing.json')
        result=router.predict('causalman_pressfit',df.loc[test])
    score=np.array([r['expected'] for r in result['rows']]);y=1-df.loc[test,target].to_numpy(int)
    summary=metrics(y,score,prep['threshold'])
    baseline=(df.loc[test,[c for c in df if c.endswith('_MpGood')]]==0).any(axis=1).to_numpy(float)
    # Simulator quality flags are a comparator only; never model inputs.
    return {'evaluation':'saved_model_existing_holdout_replay','synthetic':True,
        'source':'Zenodo 14697668 dataset0 partially observable',
        'model_sha256':digest(model_dir/'model.json'),'preprocessing_sha256':digest(model_dir/'preprocessing.json'),
        'split_reproduced':True,'reused_test_set':True,
        'model':summary,'simulator_flag_comparator':metrics(y,baseline,.5),
        'limits':'Post-operation synthetic scalar measurements. Test data have been inspected before; not a new blind or field validation.'}

def evaluate_mct(data_dir, bundle, quick=False):
    from .mct import hmotp_table,nasa_table,stiehl_table,tool_cv
    d=Path(data_dir);saved=json.loads(Path(bundle).read_text(encoding='utf-8'))
    variant=saved['variant'];rules=saved['label_rules']
    tables={'mct_hmotp':hmotp_table(d/'hmotp_cuts.csv')[0]}
    if not quick:
        tables['mct_nasa']=nasa_table()[0]
        if (d/'stiehl_runs.csv').exists():tables['mct_stiehl']=stiehl_table(d/'stiehl_runs.csv')[0]
    # Existing saved training labels must match the current benchmark configuration.
    from .mct import RULES
    for k in tables:
        if rules.get(k)!=RULES[k]:raise ValueError('MCT label rule changed: '+k)
    scores=tool_cv(tables,variant)
    return {'evaluation':'refitted_leave_tool_out_pipeline','saved_bundle_sha256':digest(bundle),
        'variant':variant,'quick':quick,'datasets':scores,
        'limits':'Cross-validation refits models. Not a blind test of the saved all-data model. Joint uses only included datasets; quick excludes NASA and Stiehl. Variant was previously selected using these data.'}

def render(report):
    lines=['# MILG MCT / 압입 평가','', '운영 모델 교체 없음. 공정별 정답·평가 방식이 달라 하나의 정확도로 합치지 않습니다.','']
    if 'pressfit' in report:
        r=report['pressfit']['model'];lines += ['## 압입 · CausalMan 합성 데이터',
            f"시험 {r['rows']}건 / 불합격 {r['positives']}건",
            f"AUC {r['auc']:.4f} · 검출률 {r['recall']:.2%} · 정상 오경보 {r['false_positive_rate']:.2%}",
            f"TP {r['tp']} / FN {r['fn']} / FP {r['fp']} / TN {r['tn']}",
            '기존 시험 구간 재평가입니다. 새로운 외부 검증이 아닙니다.','']
    if 'mct' in report:
        lines += ['## MCT · 공구 단위 교차검증','', '| 데이터 | 방식 | AUC | 상위 10% 알림의 불량 검출률 | 알림 정밀도 |','|---|---|---|---|---|']
        for k,d in report['mct']['datasets'].items():
            for mode in ('rule','local','joint'):
                s=d[mode];a='판단 불가' if s['auc'] is None else f"{s['auc']:.4f}"
                lines.append(f"| {k} | {mode} | {a} | {s['capture']:.2%} | {s['precision']:.2%} |")
        lines += ['', 'rule=이탈 규칙, local=해당 데이터만, joint=포함된 MCT 데이터 공동 학습.',
                  '평가용 모델을 다시 학습합니다. 저장된 전체학습 모델의 미사용 신규 데이터 시험은 아닙니다.']
    return '\n'.join(lines)+'\n'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mct-data',required=True);p.add_argument('--mct-model',default='state/base/mct.json')
    p.add_argument('--pressfit-data',required=True);p.add_argument('--pressfit-model',required=True)
    p.add_argument('--out',required=True);p.add_argument('--quick',action='store_true')
    a=p.parse_args();out=Path(a.out)/datetime.now().strftime('%Y%m%d-%H%M%S-%f');out.mkdir(parents=True)
    report={'created_at':datetime.now().isoformat(),'production_modified':False}
    print('1/2 CausalMan saved-model evaluation',flush=True)
    report['pressfit']=evaluate_pressfit(a.pressfit_data,a.pressfit_model)
    (out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('2/2 MCT leave-tool-out evaluation (refits; may take time)',flush=True)
    report['mct']=evaluate_mct(a.mct_data,a.mct_model,a.quick)
    (out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False,default=float),encoding='utf-8')
    (out/'report.md').write_text(render(report),encoding='utf-8')
    print(render(report),flush=True);print('Saved:',out.resolve(),flush=True)

if __name__=='__main__':main()
