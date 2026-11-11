"""Recheck excluded public packs for independent classification/regression targets."""
import argparse
import hashlib
import importlib
import io
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut
from threadpoolctl import threadpool_limits

from .packs_binary import MilgBinaryModel
from .pilot import PilotEngine, write_json
from .strict_evaluation import TrainTransform
from .train_process_packs import activate

LASER = ["Mean SP [V]", "St Dev SP [V]", "Mean ST [V]", "St Dev ST [V]", "Mean SR [V]", "St Dev SR [V]"]
GMAW = ["Ta [px^2]", "Tb", "Tp", "Tv", "Tw", "Wf [m/min]", "Wi [A]", "Ws [mm/s]", "Wu [V]"]


class NumericDataUnpickler(pickle.Unpickler):
    ALLOWED = {
        ("pandas.core.frame", "DataFrame"), ("pandas.core.internals.managers", "BlockManager"),
        ("pandas._libs.internals", "_unpickle_block"), ("numpy.core.multiarray", "_reconstruct"),
        ("numpy._core.multiarray", "_reconstruct"), ("numpy", "ndarray"), ("numpy", "dtype"),
        ("builtins", "slice"), ("pandas.core.indexes.base", "_new_Index"),
        ("pandas.core.indexes.base", "Index"), ("pandas.core.indexes.range", "RangeIndex"),
        ("numpy.core.multiarray", "scalar"), ("numpy._core.multiarray", "scalar"),
        ("pandas._libs.arrays", "__pyx_unpickle_NDArrayBacked"),
        ("pandas.core.arrays.datetimes", "DatetimeArray"), ("pandas._libs.tslibs.timestamps", "_unpickle_timestamp")
    }

    def find_class(self, module, name):
        if (module, name) not in self.ALLOWED:
            raise ValueError(f"Unapproved serialized class: {module}.{name}")
        return getattr(importlib.import_module(module), name)


def laser_data(raw):
    path = next(raw.glob("Using photodiodes*.zip"))
    with ZipFile(path) as z:
        file = next(n for n in z.namelist() if n.endswith("Statistic featuress.xlsx"))
        content = z.read(file)
    if hashlib.md5(content).hexdigest() != "8e48790cc8c204a1740de2c3173b7cb1":
        raise ValueError("Laser workbook differs from Zenodo 6732794")
    frames = []
    for name in ("A", "B", "C"):
        d = pd.read_excel(io.BytesIO(content), sheet_name=name).dropna(subset=LASER+["Label", "Signal ID"])
        if not set(d.Label) <= {"LoC", "SW", "OP"}:
            raise ValueError("Unknown laser quality class")
        d["group"] = name
        d["failure"] = d.Label.ne("SW").astype(int)
        frames.append(d)
    all_data = pd.concat(frames, ignore_index=True)
    # A and B share four actual welds. Remove both copies before grouped evaluation.
    repeated = all_data["Signal ID"].duplicated(keep=False) | all_data.duplicated(subset=LASER, keep=False)
    all_data = all_data.loc[~repeated].reset_index(drop=True)
    all_data.attrs["duplicate_rows_removed"] = int(repeated.sum())
    return all_data


def tube_data(raw):
    path = raw / "TubeBEND.pkl"
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "md5").hexdigest() != "f407dbce2d13c351ff81baab2da3343b":
            raise ValueError("TubeBEND differs from Zenodo 16614082")
    with path.open("rb") as stream:
        data = NumericDataUnpickler(stream).load()
    rows = []
    for key, item in data.items():
        if int(key.split("_")[-1]) in (1, 48, 166):
            continue
        setup = item["bending_setups"].iloc[0]
        curves = item["process_parameters_loads_sensor"]
        arc = item["geometry_data_key_characteristics_arc"]
        if curves.empty or arc.empty:
            continue
        record = {"id": key, "tube_collapse_mm": float(arc["Collapse [mm]"].max())}
        for name in ("Outer-diameter", "Wall-thickness", "Target-angle", "Wiper-die shortening",
                     "Pressure-die lateral position", "Pressure-die distance", "Pressure-die boost",
                     "Mandrel position", "Mandrel retraction timing", "Collet boost", "Clamp-die lateral position"):
            record[name] = float(setup[name])
        for name in curves:
            if name == "Time_[s]":
                continue
            values = curves[name].to_numpy(float)
            if not np.isfinite(values).all():
                raise ValueError(f"Nonfinite tube force: {key}")
            record[name+"_mean"] = float(values.mean())
            record[name+"_std"] = float(values.std())
            record[name+"_max_abs"] = float(np.abs(values).max())
        record["group"] = json.dumps([record[n] for n in ("Outer-diameter", "Wall-thickness", "Target-angle", "Wiper-die shortening", "Pressure-die lateral position", "Pressure-die distance", "Pressure-die boost", "Mandrel position", "Mandrel retraction timing", "Collet boost", "Clamp-die lateral position")])
        rows.append(record)
    return pd.DataFrame(rows)


def gmaw_data(raw):
    path = raw / "GMAW_Bead_Geometry_Figshare.zip"
    frames = []
    with ZipFile(path) as z:
        for name in sorted(z.namelist()):
            if "processed_features_" not in name:
                continue
            d = pd.read_csv(z.open(name))
            d["group"] = name.split("/")[1]
            frames.append(d)
    return pd.concat(frames, ignore_index=True)


def gmaw_process_data(raw):
    frames=[]
    with ZipFile(raw/"GMAW_Process_Mendeley.zip") as z:
        for n in sorted(z.namelist()):
            if n.endswith("all.csv"):
                d=pd.read_csv(z.open(n))
                d["group"]=n.split("/")[0]
                frames.append(d)
    return pd.concat(frames,ignore_index=True)


def cmt_data(raw):
    """Independent post-deposition scanned surface relief; provisional metrology target."""
    root = raw / "cmt"
    cache = raw / "cmt_scanned_relief.csv"
    if cache.exists():
        return pd.read_csv(cache)
    order = pd.read_csv(next(root.rglob("Experiment_Order_140_cordons.csv")), sep=";", decimal=",", encoding="cp1252")
    order.columns = ["bead_id", "sequence", "plate", "holder", "position", "condition", "wire_m_min", "travel_mm_s"]
    rows = []
    for i, rec in order.iterrows():
        matches = list(root.rglob(f"RAM_WAAM_Mono_{int(rec.bead_id)}.csv"))
        if not matches:
            continue
        heights = []
        with matches[0].open(encoding="cp1252") as f:
            header = f.readline()
        separator = ";" if ";" in header else ","
        decimal = "," if separator == ";" else "."
        for chunk in pd.read_csv(matches[0], chunksize=400, encoding="cp1252", sep=separator, decimal=decimal):
            # Scanner no-return sentinel is -99.9999. Sample complete profiles uniformly.
            a = chunk.iloc[::10, 1:].to_numpy(float)
            a[a <= -99] = np.nan
            valid = (np.isfinite(a[:,100:200]).sum(axis=1)>=50) & (np.isfinite(a[:,600:700]).sum(axis=1)>=50) & (np.isfinite(a[:,300:500]).sum(axis=1)>=100)
            a = a[valid]
            if not len(a):
                continue
            left = np.nanmedian(a[:, 100:200], axis=1)
            right = np.nanmedian(a[:, 600:700], axis=1)
            x = np.arange(300,500)
            plane = left[:,None] + (right-left)[:,None] * ((x-150)/500)
            relief = np.nanquantile(a[:,300:500]-plane, .98, axis=1)
            heights.extend(relief[np.isfinite(relief) & (relief>.5)].tolist())
        if len(heights)<10:
            continue
        rows.append({"id":int(rec.bead_id), "group":str(int(rec.plate)),
                     "wire_m_min":float(rec.wire_m_min), "travel_mm_s":float(rec.travel_mm_s),
                     "holder":float(rec.holder), "position":float(rec.position),
                     "cmt_scanned_relief_proxy_mm":float(np.median(heights))})
        if len(rows)%20==0:
            print("CMT scanned beads:",len(rows),flush=True)
    d=pd.DataFrame(rows)
    d.to_csv(cache,index=False)
    return d


def regression(data, features, target, key, out):
    if len(features) != len(set(features)) or target in features:
        raise ValueError("Invalid regression input contract")
    data = data.dropna(subset=[*features, target, "group"])
    if not np.isfinite(data[features+[target]].to_numpy(float)).all():
        raise ValueError("Nonfinite regression data")
    x, y = data[features], data[target].to_numpy(float)
    predictions, baselines = np.empty(len(y)), np.empty(len(y))
    folds = []
    for train, test in LeaveOneGroupOut().split(x, y, data.group):
        prep = TrainTransform().fit(x.iloc[train])
        model = Ridge(alpha=10).fit(prep.transform(x.iloc[train]), y[train])
        predictions[test] = model.predict(prep.transform(x.iloc[test]))
        baselines[test] = np.median(y[train])
        folds.append({"group": str(data.group.iloc[test[0]]), "rows": len(test),
                      "mae": float(mean_absolute_error(y[test], predictions[test])),
                      "baseline_mae": float(mean_absolute_error(y[test], baselines[test]))})
    mean = float(np.mean([f["mae"] for f in folds]))
    baseline = float(np.mean([f["baseline_mae"] for f in folds]))
    passed = len(folds) >= 3 and len(y) >= 60 and mean < .9*baseline and all(
        f["mae"] <= 1.1*f["baseline_mae"] for f in folds)
    result = {"task": "regression", "rows": len(y), "groups": len(folds),
              "macro_mae": mean, "macro_baseline_mae": baseline, "folds": folds,
              "gate_passed": passed, "target": target, "gate_policy": "At least 60 rows and 3 independent groups; macro MAE at least 10% below training-median baseline, with no group MAE more than 10% worse than baseline."}
    prep = TrainTransform().fit(x)
    model = Ridge(alpha=10).fit(prep.transform(x), y)
    path = out / (key+".joblib")
    joblib.dump({"model": model, "target": target, "preprocessing": prep.export(),
                 "feature_recipe": "extra_numeric_v1"}, path)
    np.testing.assert_allclose(joblib.load(path)["model"].predict(prep.transform(x)), model.predict(prep.transform(x)))
    data.assign(held_out_prediction=predictions, median_baseline=baselines).to_csv(out/(key+"_predictions.csv"), index=False)
    return result, path, x.iloc[[0]]


def classify_laser(data, out):
    x, y = data[LASER], data.failure.to_numpy(float)
    scores, comparison = np.empty(len(y)), np.empty(len(y))
    folds = []
    for train, test in LeaveOneGroupOut().split(x, y, data.group):
        prep = TrainTransform().fit(x.iloc[train])
        a, b = prep.transform(x.iloc[train]), prep.transform(x.iloc[test])
        model = MilgBinaryModel().fit(a, y[train])
        scores[test] = model.predict_proba(b)[:, 1]
        boost = HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=7, min_samples_leaf=5,
                                              random_state=42).fit(a, y[train])
        comparison[test] = boost.predict_proba(b)[:, 1]
        folds.append({"group": str(data.group.iloc[test[0]]), "rows": len(test),
                      "auc": float(roc_auc_score(y[test], scores[test]))})
    result = {"task": "classification", "rows": len(y), "groups": 3, "positives": int(y.sum()), "duplicate_rows_removed": data.attrs.get("duplicate_rows_removed",0),
              "auc": float(roc_auc_score(y, scores)), "boost_auc": float(roc_auc_score(y, comparison)),
              "folds": folds, "target": "laser_tab_defect"}
    # One entire acquisition dataset is held out in each fold.
    result["gate_passed"] = result["auc"] >= .75 and min(f["auc"] for f in folds) >= .65
    prep = TrainTransform().fit(x)
    model = MilgBinaryModel().fit(prep.transform(x), y)
    threshold = float(np.quantile(scores[y == 0], .95, method="higher"))
    path = out / "laser_tab.joblib"
    joblib.dump({"model": model, "target": "laser_tab_defect", "threshold": threshold,
                 "preprocessing": prep.export(), "feature_recipe": "extra_numeric_v1"}, path)
    result["threshold_note"] = "Calibrated from out-of-fold normal scores; final field alarm requires new validation."
    return result, path, x.iloc[[0]]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("raw", "out", "state"):
        p.add_argument(f"--{name}", required=True, type=Path)
    p.add_argument("--install", action="store_true")
    args = p.parse_args()
    out = args.out / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    results, artifacts, capability = {}, {}, {}
    print("Reports:", out, flush=True)
    with threadpool_limits(limits=2):
        data = laser_data(args.raw)
        r, path, sample = classify_laser(data, out)
        results["laser_tab"] = r
        if r["gate_passed"]:
            artifacts["laser_tab_quality"] = (path, {"test": r}, sample)
        weld = gmaw_data(args.raw)
        for key, target in (("gmaw_pen", "Pen [mm]"), ("gmaw_height", "BH [mm]"), ("gmaw_width", "BW [mm]")):
            route = {"gmaw_pen": "gmaw_penetration_mm", "gmaw_height": "gmaw_height_mm", "gmaw_width": "gmaw_width_mm"}[key]
            renamed = weld.rename(columns={target:route})
            r,path,sample=regression(renamed,GMAW,route,key,out)
            results[key]=r
            if r["gate_passed"]:artifacts[key]=(path,{"test":r},sample)
        tube = tube_data(args.raw)
        features = [c for c in tube if c not in ("id", "group", "tube_collapse_mm")]
        r,path,sample=regression(tube,features,"tube_collapse_mm","tube_collapse",out)
        results["tube_collapse"]=r
        if r["gate_passed"]:artifacts["tube_collapse"]=(path,{"test":r},sample)
    process_data = gmaw_process_data(args.raw)
    with threadpool_limits(limits=2):
        for target in ("high","width","depth"):
            r,path,sample=regression(process_data,["tension","velocity","weld_velocity","wire_l","volume"],
                                     target,"gmaw_process_"+target,out)
            r["hold_reason"]="Only TWO acquisition experiments; cannot meet minimum three independent groups. Geometry is photograph-derived, not destructive joint-strength inspection."
            results["gmaw_process_"+target]=r
    cmt = cmt_data(args.raw)
    with threadpool_limits(limits=2):
        r,path,sample=regression(cmt,["wire_m_min","travel_mm_s","holder","position"],
                                 "cmt_scanned_relief_proxy_mm","cmt_relief",out)
    r["numerical_gate_passed"] = r["gate_passed"]
    r["gate_passed"] = False
    r["hold_reason"] = "Post-weld scanned relief is a provisional geometric proxy; reference-plane and bead ROI need independent metrology validation. No defect labels."
    results["cmt_relief"] = r
    write_json(out/"report.json",results)
    sources = {
      "laser_tab": {"url":"https://zenodo.org/records/6732794", "license":"CC-BY-4.0", "scope":"138 unique welds after removing all 8 shared records; six published photodiode statistics only"},
      "gmaw": {"url":"https://figshare.com/articles/dataset/28062602", "license":"MIT", "scope":"2593 spatial observations from FOUR independent weld runs, not 2593 independent welds"},
      "gmaw_process": {"url":"https://data.mendeley.com/datasets/2nyjpb89bf/1", "license":"CC-BY-4.0", "scope":"Two acquisition experiments with photograph-derived bead geometry; exclude all geometry from inputs"},
      "tube": {"url":"https://zenodo.org/records/16614082", "license":"CC-BY-4.0", "scope":"315 successful trials, 100 exact nominal setup groups; 3 failed trials excluded so this is NOT weld/assembly failure detection"},
      "cmt": {"url":"https://doi.org/10.57745/EFYQSN", "license":"Etalab Open License 2.0", "scope":"Provisional post-deposition scanned surface-relief regression, not joint-strength or defect detection"}
    }
    write_json(out/"sources.json",sources)
    files = [p for p in args.raw.iterdir() if p.is_file() and p.suffix in (".zip",".7z",".pkl")]
    checksums = {}
    for file in files:
        with file.open("rb") as stream:
            checksums[file.name] = hashlib.file_digest(stream,"sha256").hexdigest()
    write_json(out/"input-sha256.json",checksums)
    write_json(out/"deployment.json",{"installed":[],
                                    "shared_production_coefficients_updated":False,
                                    "public_website_updated":False})
    for key,r in results.items():
        print(key,r.get("auc",r.get("macro_mae")),"baseline",r.get("macro_baseline_mae"),"gate",r["gate_passed"],flush=True)
    if args.install and artifacts:
        for key in artifacts:
            capability[key]={"purpose":"public grouped-quality benchmark","unit":"score" if key=="laser_tab_quality" else "mm"}
        release=activate(args.state.resolve(),out,artifacts,capability)
        engine=PilotEngine(args.state.resolve())
        for key,(_,_,sample) in artifacts.items():
            result=engine.predict(key,sample.to_dict("records"))
            assert len(result["rows"])==1 and result["rows"][0]["alert"] is None
        write_json(out/"deployment.json",{"installed":sorted(artifacts),"release":release,
                                        "shared_production_coefficients_updated":False, "public_website_updated":False})
        print("Registered:",list(artifacts))
        print("Active release:",release)
    print("Saved:",out)


if __name__=="__main__":
    main()
