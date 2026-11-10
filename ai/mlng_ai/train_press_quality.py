"""Train a press product-defect benchmark using official P2S splits."""
import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut

from .evaluate_processes import metrics
from .pilot import PilotEngine, write_json
from .press_quality import CHANNEL, force_features
from .train_process_packs import activate

PROCESS = "press_p2s_quality"


def read_split(root, variant, split):
    path = root / variant / f"{split}-00000-of-00001.parquet"
    data = pd.read_parquet(path, columns=[CHANNEL, "label", "speed"])
    hashes = {hashlib.sha256(np.asarray(row, dtype="<f8").tobytes()).digest() for row in data[CHANNEL]}
    if len(hashes) != len(data):
        raise ValueError(f"Duplicate force traces in {variant}/{split}")
    x = pd.DataFrame(force_features({CHANNEL: curve}) for curve in data[CHANNEL])
    return data, x, hashes


def classifier():
    return ExtraTreesClassifier(n_estimators=150, min_samples_leaf=4, random_state=42, n_jobs=2)


def score(y, predictions, threshold):
    return {**metrics(y, predictions, threshold), "auc": float(roc_auc_score(y, predictions))}


def train_variant(root, variant):
    tr, x, train_hashes = read_split(root, variant, "train")
    te, xt, test_hashes = read_split(root, variant, "test")
    if train_hashes & test_hashes:
        raise ValueError(f"Training and test force traces overlap: {variant}")
    model = classifier()
    y, yt = tr.label.to_numpy(), te.label.to_numpy()
    if variant == "Normal":
        if set(tr.speed) & set(te.speed):
            raise ValueError("Unexpected overlap of operating speeds in Normal split")
        validation = np.empty(len(x))
        for train, val in LeaveOneGroupOut().split(x, y, tr.speed):
            validation[val] = classifier().fit(x.to_numpy()[train], y[train]).predict_proba(x.to_numpy()[val])[:, 1]
        threshold = float(np.quantile(validation[y == 0], .95, method="higher"))
    else:
        threshold = .5
    x = x.to_numpy()
    model.fit(x, y)
    predictions = model.predict_proba(xt.to_numpy())[:, 1]
    rates = tr.groupby("speed")["label"].mean()
    speed_only = te.speed.map(rates).fillna(float(y.mean())).to_numpy()
    report = {"train_rows": len(tr), "test_rows": len(te), "test": score(yt, predictions, threshold),
              "speed_only_auc": float(roc_auc_score(yt, speed_only)),
              "by_speed": {str(speed): score(yt[mask], predictions[mask], threshold)
                           for speed in sorted(te.speed.unique()) for mask in [te.speed.eq(speed).to_numpy()]
                           if len(set(yt[mask])) == 2}}
    columns = list(xt)
    prep = {"columns": columns, "mean": [0.] * len(columns), "std": [1.] * len(columns),
            "imputation": [0.] * len(columns), "clip": [-1e12, 1e12]}
    artifact = {"model": model, "threshold": threshold, "preprocessing": prep,
                "target": "p2s_forming_defect", "feature_recipe": "press_p2s_force_v1"}
    return report, artifact, te[[CHANNEL]].iloc[[0]]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "out", "state"):
        p.add_argument(f"--{name}", required=True, type=Path)
    p.add_argument("--install", action="store_true")
    args = p.parse_args()
    normal, artifact, sample = train_variant(args.data, "Normal")
    decoy, _, _ = train_variant(args.data, "Decoy")
    test = normal["test"]
    passed = (test["auc"] >= .75 and test["auc"] > normal["speed_only_auc"]
              and test["recall"] >= .5 and test["false_positive_rate"] <= .2)
    out = args.out / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    report = {"source": "https://huggingface.co/datasets/AIML-TUDA/P2S", "license": "CC BY 4.0",
              "normal": normal, "decoy_stress_test_separate_fit": decoy, "gate_passed": passed,
              "limits": "Twelve published experiments from one press. Normal and Decoy reuse the same underlying data. "
                        "Mask and speed excluded from force-model inputs. Decoy threshold fixed at 0.5."}
    write_json(out / "report.json", report)
    path = out / "press_quality.joblib"
    joblib.dump(artifact, path)
    print("Normal:", test, "Decoy:", decoy["test"], "gate:", passed)
    print("Saved:", out)
    if args.install and passed:
        release = activate(args.state.resolve(), out, {PROCESS: (path, normal, sample)},
                           {PROCESS: {"purpose": "forming_defect_benchmark",
                                      "sensor": CHANNEL, "trace_points": 4096}})
        result = PilotEngine(args.state.resolve()).predict(PROCESS, sample.to_dict("records"))
        assert result["target"] == "p2s_forming_defect" and result["rows"][0]["alert"] is None
        print("Verified defect score:", result["rows"][0]["p"])
        print("Active release:", release)


if __name__ == "__main__":
    main()
