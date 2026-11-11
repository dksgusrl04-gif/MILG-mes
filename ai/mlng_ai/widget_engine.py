"""MILG classifier with training-only feature selection and serializable numeric contract."""
import numpy as np
import pandas as pd
from .local_inputs import fit_engine

class WidgetEngineModel:
    @staticmethod
    def expand(x):
        return np.column_stack([x,np.abs(x),x*x/8])

    def __init__(self,tau_delta=.5,two_sided=True):
        self.tau_delta=tau_delta
        self.two_sided=two_sided

    def expanded(self,x):
        return self.expand(x) if self.two_sided else x

    def fit(self,x,y):
        self.n_features_in_=x.shape[1]
        expanded=self.expanded(x)
        self.core=fit_engine('quality',expanded,np.asarray(y,float),
                             [f'input_{i}' for i in range(expanded.shape[1])],{'tau':(.3,self.tau_delta)})
        return self

    def predict_proba(self,x):
        x=np.asarray(x,float)
        if x.ndim!=2 or x.shape[1]!=self.n_features_in_ or not np.isfinite(x).all():
            raise ValueError('Invalid calibrated widget features')
        mu=self.core.predict('quality',self.expanded(x),np.ones(len(x)))[0]
        p=self.core.prob('quality',mu)
        return np.column_stack([1-p,p])


def select_inputs(train,y,max_features=24):
    """All ranking and availability checks use training rows exclusively."""
    records=[]
    for c in train:
        a=pd.to_numeric(train[c],errors='coerce').replace([np.inf,-np.inf],np.nan)
        if a.notna().mean()<=.5 or a.nunique()<2:
            continue
        v=a.fillna(a.median())
        scores=[]
        for candidate in (v,(v-v.median()).abs()):
            score=candidate.corr(pd.Series(np.asarray(y),index=train.index))
            scores.append(abs(float(score)) if np.isfinite(score) else 0.)
        records.append((max(scores),c))
    cols=[c for _,c in sorted(records,key=lambda t:(-t[0],t[1]))[:max_features]]
    if not cols:raise ValueError('No usable training signals')
    return cols
