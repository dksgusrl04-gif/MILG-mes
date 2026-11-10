import numpy as np
from mlng_ai.evaluate_processes import metrics, render

def test_confusion_and_ties():
    r=metrics([0,0,1,1],[.1,.7,.8,.2],.5)
    assert (r['tp'],r['fp'],r['tn'],r['fn'])==(1,1,1,1)
    assert r['recall']==.5 and r['precision']==.5
    assert metrics([0,1],[.5,.5],.5)['auc']==.5
    assert metrics([0,0],[0.,0.],.5)['recall'] is None

def test_report_distinguishes_synthetic_replay():
    r=metrics([0,1],[.1,.9],.5)
    text=render({'pressfit':{'model':r}})
    assert 'CausalMan' in text and 'TP 1' in text
    assert '기존 시험 구간' in text
