"""Train and validate the final HRC model for steel tempering."""
import argparse
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupKFold, LeaveOneGroupOut

from .pilot import PilotEngine, write_json
from .train_process_packs import activate

PROCESS = "heat_tempering_hrc"
TARGET = "final_hardness_hrc"
MEMBER = "Tempering_data_for_carbon_and_low_alloy_steels_Kaggle_Raiipa.csv"
COMPOSITION = ("C", "Mn", "P", "S", "Si", "Ni", "Cr", "Mo", "V", "Al", "Cu")
RENAME = {
    **{f"{element} (%wt)": f"{element.lower()}_wt_pct" for element in COMPOSITION},
    "Tempering time (s)": "tempering_time_s",
    "Tempering temperature (\u00baC)": "tempering_temperature_c",
    "Initial hardness (HRC) - post quenching": "initial_hardness_hrc",
    "Final hardness (HRC) - post tempering": TARGET,
}
PROCESS_COLUMNS = ["tempering_time_s", "tempering_temperature_c"]


def fit_model(x, y):
    return ExtraTreesRegressor(n_estimators=96, min_samples_leaf=3, random_state=42).fit(x, y)


def evaluate(data, columns):
    x = data[columns].to_numpy(float)
    y = data[TARGET].to_numpy(float)
    summaries = {}
    for field, splitter in (("Source", LeaveOneGroupOut()), ("Steel type", GroupKFold(5))):
        groups = data[field].to_numpy()
        folds = []
        for train, test in splitter.split(x, y, groups):
            candidate = fit_model(x[train], y[train])
            process_only = fit_model(x[train, :2], y[train])
            median = DummyRegressor(strategy="median").fit(x[train], y[train])
            folds.append({
                "groups": sorted(set(groups[test])), "rows": len(test),
                "mae": mean_absolute_error(y[test], candidate.predict(x[test])),
                "process_only_mae": mean_absolute_error(y[test], process_only.predict(x[test, :2])),
                "median_mae": mean_absolute_error(y[test], median.predict(x[test])),
            })
        means = {key: float(np.mean([row[key] for row in folds]))
                 for key in ("mae", "process_only_mae", "median_mae")}
        summaries[field] = {"fold_mean": means, "folds": folds}
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pack", "state", "out"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    with ZipFile(args.pack) as z:
        raw = z.read(MEMBER)
    data = pd.read_csv(io.BytesIO(raw), na_values="?").rename(columns=RENAME)
    columns = PROCESS_COLUMNS + [f"{e.lower()}_wt_pct" for e in COMPOSITION]
    subsets = {"standard": (data, columns),
               "with_initial": (data.dropna(subset=["initial_hardness_hrc"]), columns + ["initial_hardness_hrc"])}
    out = args.out / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    report = {"source_member": MEMBER, "sha256": hashlib.sha256(raw).hexdigest(), "models": {}}
    passing = {}
    details = {}
    for name, (subset, features) in subsets.items():
        subset = subset.dropna(subset=["Source", "Steel type", TARGET, *features])
        subset = subset[np.isfinite(subset[[TARGET,*features]].to_numpy(float)).all(axis=1)]
        scores = evaluate(subset, features)
        passed = all(s["fold_mean"]["mae"] < min(s["fold_mean"]["process_only_mae"],
                                                s["fold_mean"]["median_mae"]) for s in scores.values())
        x = subset[features].to_numpy(float)
        bounds = {key: [float(x[:, i].min()), float(x[:, i].max())] for i, key in enumerate(features)}
        model = fit_model(x, subset[TARGET])
        path = out / f"{name}.joblib"
        joblib.dump({"model": model, "target": TARGET, "feature_recipe": "heat_tempering_hrc_v1",
                     "bounds": bounds, "preprocessing": {"columns": features, "mean": [0.] * len(features),
                         "std": [1.] * len(features), "imputation": [0.] * len(features),
                         "clip": [-1e12, 1e12]}}, path)
        report["models"][name] = {"rows": len(x), "scores": scores, "gate_passed": passed}
        print(name, len(x), {key: value["fold_mean"] for key, value in scores.items()}, "gate:", passed)
        if passed:
            key = PROCESS if name == "standard" else PROCESS + "_initial"
            passing[key] = (path, {"test": {k: v["fold_mean"] for k,v in scores.items()}},
                            subset[features].iloc[[0]])
            details[key] = {"unit": "HRC", "purpose": "steel_tempering_hardness_estimate",
                            "input_bounds": bounds, "evaluation": "separate_source_and_grade_holdouts"}
    write_json(out / "report.json", report)
    print("Saved:", out)
    if args.install and passing:
        release = activate(args.state.resolve(), out, passing, details)
        engine = PilotEngine(args.state.resolve())
        for key, (_, _, sample) in passing.items():
            result = engine.predict(key, sample.to_dict("records"))
            assert result["decision_status"] == "benchmark_estimate" and result["rows"][0]["alert"] is None
            print("Verified", key, result["rows"][0][TARGET], "HRC")
        print("Active release:", release)


if __name__ == "__main__":
    main()
