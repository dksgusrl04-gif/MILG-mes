import inspect
import numpy as np
import pandas as pd
import pytest
from mlng_ai.strict_evaluation import TrainTransform,threshold_on_validation,partitions,fit_candidates

def test_preprocessing_is_frozen_before_test():
    train=pd.DataFrame({'force':[1.,2.,3.],'current':[1.,np.nan,3.]})
    t=TrainTransform().fit(train);before=t.export()
    t.transform(pd.DataFrame({'force':[1e9],'current':[np.nan]}))
    assert t.export()==before and t.median[1]==2
    assert train.current.isna().sum()==1

def test_threshold_cannot_read_test_outcomes():
    assert 'y_test' not in inspect.signature(fit_candidates).parameters
    t=threshold_on_validation(np.array([0,0,0,1]),np.array([1,2,3,100]),.05)
    assert t==3
    with pytest.raises(ValueError):threshold_on_validation([1,1],[.1,.2],.05)

def test_groups_are_disjoint_and_repeatable():
    g=np.repeat(['A','B','C','D','E'],5)
    tr,v,te=partitions(g,'A')
    assert not (tr&v).any() and not (tr&te).any() and not (v&te).any()
    assert (tr|v|te).all() and set(g[te])=={'A'}
    assert set(g[tr]).isdisjoint(set(g[v]))
    assert np.array_equal(v,partitions(g,'A')[1])

def test_candidate_selection_only_validation():
    rng=np.random.default_rng(1)
    x=pd.DataFrame({'force':rng.normal(size=80)})
    y=(x.force.to_numpy()>0).astype(int)
    result,selected,prep=fit_candidates(x[:40],y[:40],x[40:60],y[40:60],x[60:])
    assert selected in result
    assert all(len(r['test_scores'])==20 for r in result.values())
    assert all('test' not in r for r in result.values())
