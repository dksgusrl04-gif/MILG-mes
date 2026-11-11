"""Protect causality, held-out label isolation and real widget model registration."""
import json
import pickle
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import pytest
from mlng_ai.datasets import Table
from mlng_ai.local_inputs import local_frame,LocalScaler
from mlng_ai.mct import _fold_data
from mlng_ai.widget_engine import WidgetEngineModel
from mlng_ai.strict_evaluation import TrainTransform
from mlng_ai.routing import Router
from mlng_ai.train_widget_engine import calibration_partition


def test_future_cycles_cannot_change_past_local_features():
    df=pd.DataFrame({'tool_id':['A','B','A','B','A','B']*3,'spindle_current':np.arange(1,19,dtype=float)})
    for stop in range(1,len(df)+1):
        pd.testing.assert_frame_equal(local_frame(df.iloc[:stop]),local_frame(df).iloc[:stop])


def test_test_labels_cannot_change_training_or_test_features():
    df=pd.DataFrame({'tool_id':['A']*80+['B']*80,'spindle_current [A]':np.tile(np.arange(80,dtype=float),2),'tool_ng':([0]*40+[1]*40)*2})
    meta={'tool_ng':{'label':'불량'},'spindle_current [A]':{'label':'주축 전류','unit':'A'}}
    def encode(d):return _fold_data(Table('demo','MCT',d,meta,{},'',{},'row','test',entity='tool_id'),'B','sensor')
    before=encode(df);df.loc[df.tool_id=='B','tool_ng']=1-df.loc[df.tool_id=='B','tool_ng'];after=encode(df)
    np.testing.assert_allclose(before[0],after[0]);np.testing.assert_allclose(before[2],after[2])


def test_scaler_uses_only_training_missingness_and_statistics():
    train=pd.DataFrame({'a':[1.,np.nan,3.],'missing':[np.nan]*3})
    scaler=LocalScaler().fit(local_frame(train,entity=None))
    assert scaler.cols==['a']
    np.testing.assert_allclose(scaler.transform(pd.DataFrame({'a':[np.nan]})),[[0.]])
    assert local_frame(pd.DataFrame({'a':[1.,np.nan]}),entity=None).a.isna().iloc[1]


def test_calibration_selection_never_reads_held_out_labels():
    groups=np.repeat(list('ABCDEFGH'),20);y=np.tile([0,1]*10,8).astype(float)
    first=calibration_partition(groups,y,'A');y[groups=='A']=0;second=calibration_partition(groups,y,'A')
    for a,b in zip(first,second):np.testing.assert_array_equal(a,b)
    assert not (first[0]&first[1]).any() and not (first[1]&first[2]).any()


def test_unapproved_widget_model_is_rejected_and_approved_model_routes(tmp_path):
    d=pd.DataFrame({'measured_force':np.linspace(-2,2,80)})
    prep=TrainTransform().fit(d);model=WidgetEngineModel().fit(prep.transform(d),(d.measured_force>0).to_numpy(float))
    artifact={'model':model,'target':'injection_nok','threshold':.5,'preprocessing':prep.export(),'feature_recipe':'widget_numeric_v1','evaluation_gate':False}
    path=tmp_path/'candidate.joblib';joblib.dump(artifact,path);router=Router(tmp_path/'state')
    with pytest.raises(ValueError,match='did not pass'):router.register('inj_quality',path,'specialist','test')
    artifact['evaluation_gate']=True;joblib.dump(artifact,path);router.register('inj_quality',path,'specialist','test')
    result=router.predict('inj_quality',d.iloc[[0]])
    assert len(result['rows'])==1 and 0<=result['rows'][0]['p']<=1
    with pytest.raises(ValueError):router.predict('inj_quality',pd.DataFrame({'wrong':[1.]}))
