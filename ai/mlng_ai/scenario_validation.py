"""Group-safe, timestamp-safe scenario validation."""
import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

class ChronologicalSplit:
    def __init__(self,times,n_splits=4):
        self.times=pd.Series(np.asarray(times))
        self.n_splits=n_splits
    def split(self,X,y=None,groups=None):
        if len(X)!=len(self.times) or self.times.isna().any():raise ValueError('Invalid CV timestamps')
        unique=np.sort(self.times.unique())
        if len(unique)<3:raise ValueError('Temporal CV needs at least three distinct training times')
        for tr,va in TimeSeriesSplit(n_splits=min(self.n_splits,len(unique)-1)).split(unique):
            a=np.flatnonzero(self.times.isin(unique[tr]));b=np.flatnonzero(self.times.isin(unique[va]))
            assert self.times.iloc[a].max()<self.times.iloc[b].min()
            yield a,b
