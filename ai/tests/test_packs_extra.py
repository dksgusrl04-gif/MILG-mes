"""Regression candidates must not masquerade as pass/fail quality models."""
import io
import pickle
import subprocess
import sys
import joblib
import numpy as np
import pandas as pd
import pytest
from mlng_ai.packs_binary import MilgBinaryModel
from mlng_ai.packs_extra import NumericDataUnpickler, regression
from mlng_ai.routing import Router
from mlng_ai.strict_evaluation import TrainTransform


def test_untrusted_pickle_class_rejected():
    with pytest.raises(ValueError, match="Unapproved serialized class"):
        NumericDataUnpickler(io.BytesIO(pickle.dumps(Exception))).load()


def test_saved_binary_loads_in_fresh_process(tmp_path):
    x=np.arange(40,dtype=float).reshape(20,2)/20
    model=MilgBinaryModel().fit(x,(x[:,0]>.9).astype(float))
    path=tmp_path/"model.joblib"
    joblib.dump(model,path)
    subprocess.run([sys.executable,"-c",
        "import joblib,sys,numpy as np; m=joblib.load(sys.argv[1]); assert m.predict_proba(np.zeros((1,2))).shape==(1,2)",str(path)],check=True)


def test_grouped_regression_contract_and_router(tmp_path):
    x=np.tile(np.linspace(0,10,30),3)
    d=pd.DataFrame({"signal":x,"measurement":2*x+1,"group":np.repeat(["A","B","C"],30)})
    with pytest.raises(ValueError, match="Invalid regression"):
        regression(d,["measurement"],"measurement","bad",tmp_path)
    d=d.rename(columns={"measurement":"tube_collapse_mm"})
    result,path,sample=regression(d,["signal"],"tube_collapse_mm","good",tmp_path)
    assert result["gate_passed"] and result["groups"]==3
    assert len(pd.read_csv(tmp_path/"good_predictions.csv"))==90
    router=Router(tmp_path/"state")
    router.register("tube",path,"specialist","test")
    row=router.predict("tube",sample)["rows"][0]
    assert "tube_collapse_mm" in row and "p" not in row
    with pytest.raises(ValueError):
        router.predict("tube",pd.DataFrame({"signal":[float("nan")]}))
