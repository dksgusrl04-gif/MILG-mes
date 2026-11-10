"""Evaluate spot weld category prediction on a condition-separated test."""
import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.model_selection import GroupShuffleSplit

from .evaluate_processes import metrics
from .pilot import PilotEngine, write_json
from .strict_evaluation import threshold_on_validation
from .train_process_packs import activate
from .train_spot_weld import MEMBER, RECIPE, SOURCE, TARGETS, load_samples

PROCESS = "spot_weld_rsw_quality"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("pack", "out", "state"):
        p.add_argument(f"--{name}", required=True, type=Path)
    p.add_argument("--install", action="store_true")
    args = p.parse_args()
    with ZipFile(args.pack) as z:
        raw = z.read(MEMBER)
    frame, samples, excluded = load_samples(raw)
    category = frame.groupby("Sample ID")["Category"]
    if (category.nunique(dropna=False) != 1).any():
        raise ValueError("Conflicting quality category")
    category = category.first().loc[samples.index].str.lower()
    if set(category) != {"good", "bad", "explode"}:
        raise ValueError("Unknown quality categories")
    y = category.ne("good").to_numpy(int)
    x = samples.drop(columns=list(TARGETS.values()))
    groups = samples.groupby(RECIPE).ngroup()
    rest, test = next(GroupShuffleSplit(n_splits=1, test_size=.25, random_state=42).split(x, y, groups))
    fit_rel, val_rel = next(GroupShuffleSplit(n_splits=1, test_size=.27, random_state=42)
                            .split(x.iloc[rest], y[rest], groups.iloc[rest]))
    train, val = rest[fit_rel], rest[val_rel]
    if any(len(set(y[part])) != 2 for part in (train, val, test)):
        raise ValueError("Both classes are required in each condition split")
    model = ExtraTreesClassifier(n_estimators=200, min_samples_leaf=4, random_state=42)
    model.fit(x.iloc[train].to_numpy(), y[train])
    predict = lambda idx: model.predict_proba(x.iloc[idx].to_numpy())[:, 1]
    cut = threshold_on_validation(y[val], predict(val), .10)
    validation = metrics(y[val], predict(val), cut)
    result = metrics(y[test], predict(test), cut)
    passed = result["auc"] >= .75 and result["recall"] >= .5 and result["false_positive_rate"] <= .2
    out = args.out / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    report = {"source": SOURCE, "csv_sha256": hashlib.sha256(raw).hexdigest(), "excluded": excluded,
              "train_rows": len(train), "validation": validation, "test": result, "gate_passed": passed,
              "test_category_counts": category.iloc[test].value_counts().to_dict(),
              "target": "published_category_bad_or_explode", "field_validated": False}
    write_json(out / "report.json", report)
    path = out / "spot_weld_quality.joblib"
    n = x.shape[1]
    joblib.dump({"model": model, "threshold": cut, "target": report["target"],
                 "feature_recipe": "spot_weld_rsw_summary_v1",
                 "preprocessing": {"columns": list(x), "mean": [0.] * n, "std": [1.] * n,
                                   "imputation": [0.] * n, "clip": [-1e12, 1e12]}}, path)
    print("Test:", result, "gate:", passed)
    print("Saved:", out)
    if args.install and passed:
        sample = x.iloc[test[:1]]
        release = activate(args.state.resolve(), out, {PROCESS: (path, report, sample)},
                           {PROCESS: {"material": "AISI 1010", "purpose": report["target"]}})
        response = PilotEngine(args.state.resolve()).predict(PROCESS, sample.to_dict("records"))
        assert response["target"] == report["target"] and response["rows"][0]["alert"] is None
        print("Active release:", release)


if __name__ == "__main__":
    main()
