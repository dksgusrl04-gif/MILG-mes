"""Train an isolated synthetic press-fit benchmark from split Zenodo ZIP files.
No extraction, pickle loading or production model replacement.
"""
import argparse
import bisect
import json
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from .model import CumulativeModel

class JoinedZip:
    def __init__(self, folder):
        paths = [Path(folder)/f'data.zip.{i:03d}' for i in (1,2,3)]
        if not all(p.is_file() for p in paths):
            raise FileNotFoundError('All three data.zip.001/002/003 files are required')
        self.files = [p.open('rb') for p in paths]
        self.offsets = [0]
        for p in paths: self.offsets.append(self.offsets[-1]+p.stat().st_size)
        self.position = 0
    def tell(self): return self.position
    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position+offset if whence == 1 else self.offsets[-1]+offset
        if self.position < 0: raise ValueError('Negative archive position')
        return self.position
    def seekable(self): return True
    def read(self, n=-1):
        if n < 0: n = self.offsets[-1]-self.position
        parts = []
        while n > 0 and self.position < self.offsets[-1]:
            i = bisect.bisect_right(self.offsets,self.position)-1
            f = self.files[i]; f.seek(self.position-self.offsets[i])
            b = f.read(min(n,self.offsets[i+1]-self.position))
            if not b: raise EOFError('Truncated split archive')
            parts.append(b); n -= len(b); self.position += len(b)
        return b''.join(parts)
    def close(self):
        for f in self.files: f.close()

def train(folder, out, max_rows=100000):
    joined = JoinedZip(folder)
    try:
        with zipfile.ZipFile(joined) as z:
            with z.open('dataset0/dataset0_partially_observable.csv') as f:
                df = pd.read_csv(f,nrows=max_rows)
            with z.open('dataset0/interventional_table.csv') as f:
                interventions = pd.read_csv(f,nrows=max_rows)
    finally: joined.close()
    if len(df) != len(interventions): raise ValueError('Intervention table row alignment mismatch')
    df = df.loc[interventions.eq(0).all(axis=1)].copy()
    target = 'Sec_C2_Machine1_ProcessResult'
    if not set(df[target].unique()) <= {0.,1.}: raise ValueError('Unexpected target encoding')
    # Explicit whitelist: never use MpGood / ProcessResult flags as predictors.
    raw = [f'PF_M1_T{t}_{v}' for t in (1,2) for v in ('s0','Force','sgrad','Fmax','smax')]
    X = df[raw].copy()
    for c in raw:
        lo, hi = c+'_LTL', c+'_UTL'
        if lo in df and hi in df:
            span = df[hi]-df[lo]
            X[c+'_relative_to_tolerance'] = (df[c]-(df[hi]+df[lo])/2)/span.where(span > 0)
    valid = np.isfinite(X).all(axis=1) & df['batch_ID_num'].notna()
    df, X = df.loc[valid], X.loc[valid]
    batches = sorted(df.batch_ID_num.unique())
    if len(batches) < 5: raise ValueError('Too few batches; increase --max-rows')
    test = df.batch_ID_num.isin(batches[max(1,int(len(batches)*.8)):]).to_numpy()
    y = 1-df[target].to_numpy(float)
    if len(np.unique(y[~test])) < 2 or len(np.unique(y[test])) < 2:
        raise ValueError('Both classes required in each split; increase --max-rows')
    mean, std = X.loc[~test].mean(), X.loc[~test].std().replace(0,1)
    xx = ((X-mean)/std).clip(-8,8).to_numpy()
    model = CumulativeModel(list(X.columns)); model.add_process('causalman_pressfit',(y[~test].sum()+.5)/((~test).sum()+1))
    indices = np.flatnonzero(~test)
    for part in np.array_split(indices,8):
        if len(part): model.update([('causalman_pressfit',xx[part],y[part],np.ones(len(part)))])
    normal = ~test & (y == 0)
    threshold = float(np.quantile(model.predict('causalman_pressfit',xx[normal],np.ones(normal.sum()))[0],.95))
    scores = model.predict('causalman_pressfit',xx[test],np.ones(test.sum()))[0]
    yy = y[test]; pred = scores > threshold; pos = int(yy.sum()); neg = len(yy)-pos
    auc = float((rankdata(scores)[yy==1].sum()-pos*(pos+1)/2)/(pos*neg))
    out = Path(out); out.mkdir(parents=True,exist_ok=True)
    model.save(out/'model.json')
    recipe = {'columns':list(X.columns),'raw_columns':raw,'mean':mean.to_dict(),'std':std.to_dict(),
              'clip':[-8,8], 'threshold':threshold,'target':target,'positive_class':'ProcessResult == 0',
              'source':'https://zenodo.org/records/14697668','synthetic':True,'license':'CC-BY-4.0',
              'phase':'post-operation quality classification','engineering_control':False}
    (out/'preprocessing.json').write_text(json.dumps(recipe,indent=2),encoding='utf-8')
    report = {'source':recipe['source'],'synthetic':True,'requested_rows':max_rows,
              'observational_valid_rows':len(df),'train_rows':int((~test).sum()),'test_rows':int(test.sum()),
              'train_batches':len(batches)-len(set(df.loc[test,'batch_ID_num'])),
              'test_batches':len(set(df.loc[test,'batch_ID_num'])), 'auc':auc,
              'recall':float(pred[yy==1].mean()),'false_positive_rate':float(pred[yy==0].mean()),
              'test_failures':pos,'threshold':threshold,
              'limits':'Synthetic held-out batch benchmark; scalar post-operation measurements, not force-displacement waveforms or causal effect estimates. Source license: CC-BY-4.0; retain attribution.'}
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2)); print('Saved:',out.resolve())

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('folder');p.add_argument('--out',required=True);p.add_argument('--max-rows',type=int,default=100000)
    a=p.parse_args()
    if a.max_rows < 1000: p.error('--max-rows must be >= 1000')
    train(a.folder,a.out,a.max_rows)
