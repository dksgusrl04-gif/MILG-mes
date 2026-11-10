"""Train and optionally register the press pump benchmark in MILG Pilot."""
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
from sklearn.ensemble import IsolationForest
from sklearn.metrics import roc_auc_score

from .pilot import write_json
from .press_pump import CHANNELS, INTERVAL_MS, SIZE, window_features
from .train_process_packs import activate

PROCESS = "press_pump_kamp"
TARGET = "press_pump_anomaly_score"


def read_windows(raw, label):
    data = pd.read_csv(io.BytesIO(raw))
    if set(data["Equipment_state"]) != {label}:
        raise ValueError("Unexpected press pump label")
    data["TimeStamp"] = pd.to_datetime(data["TimeStamp"], format="mixed")
    data = data.sort_values("TimeStamp")
    data = data.drop_duplicates(subset=["TimeStamp", "Equipment_state", *CHANNELS])
    if data["TimeStamp"].duplicated().any():
        raise ValueError("Conflicting measurements at a press pump timestamp")
    interval = pd.Timedelta(milliseconds=INTERVAL_MS)
    blocks = data["TimeStamp"].diff().ne(interval).cumsum()
    rows = []
    for _, block in data.groupby(blocks):
        if len(block) >= SIZE:
            rows.append({**{key: block[key].iloc[:SIZE].tolist() for key in CHANNELS},
                         "sample_interval_ms": INTERVAL_MS})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("pack", "out", "state"):
        parser.add_argument(f"--{key}", type=Path, required=True)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    with ZipFile(args.pack) as pack:
        manifest = json.loads(pack.read("manifest.json"))
        blobs = {name: pack.read(name) for name in ("press_data_normal.csv", "outlier_data.csv")}
    for name, blob in blobs.items():
        if hashlib.sha256(blob).hexdigest() != manifest["sha256"][name]:
            raise ValueError(f"Source checksum mismatch: {name}")
    normal = read_windows(blobs["press_data_normal.csv"], 0)
    abnormal = read_windows(blobs["outlier_data.csv"], 1)
    x = pd.DataFrame(map(window_features, normal)).to_numpy()
    y = pd.DataFrame(map(window_features, abnormal)).to_numpy()
    a, b = int(.6 * len(x)), int(.8 * len(x))
    fit, validation, test = x[:a], x[a:b], x[b:]
    if min(len(fit), len(validation), len(test), len(y)) < 10:
        raise ValueError("Insufficient independent acquisition blocks")
    columns = list(window_features(normal[0]))
    center = fit.mean(axis=0)
    scale = fit.std(axis=0)
    scale[scale == 0] = 1

    def transform(values):
        return np.clip((values - center) / scale, -8, 8)

    model = IsolationForest(n_estimators=200, random_state=42).fit(transform(fit))

    def scores(values):
        return -model.score_samples(transform(values))

    def evaluate(score):
        cutoff = float(np.quantile(score(validation), .95, method="higher"))
        normal_score, bad_score = score(test), score(y)
        return {"threshold": cutoff, "auc": float(roc_auc_score(
                    [0] * len(test) + [1] * len(y), np.r_[normal_score, bad_score])),
                "recall": float(np.mean(bad_score > cutoff)),
                "false_positive_rate": float(np.mean(normal_score > cutoff))}

    result = evaluate(scores)
    baseline = evaluate(lambda values: np.abs(transform(values)).max(axis=1))
    out = args.out / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    report = {"source": manifest, "task": "pump anomaly score",
              "windows": {"train_normal": len(fit), "validation_normal": len(validation),
                          "test_normal": len(test), "test_abnormal": len(y)},
              "test": result, "baseline": baseline,
              "limits": "Normal and abnormal recordings each come from a different single day. "
                        "Holdout windows are acquisition blocks, not independent failure events."}
    write_json(out / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    path = out / "press_pump.joblib"
    joblib.dump({"model": model, "target": TARGET, "feature_recipe": "press_pump_window_v1",
                 "preprocessing": {"columns": columns, "mean": center.tolist(), "std": scale.tolist(),
                                   "imputation": center.tolist()}}, path)
    if args.install:
        if result["auc"] < .65:
            raise ValueError("Benchmark discrimination too low for pilot")
        sample = pd.DataFrame([normal[b]])
        release = activate(args.state.resolve(), out, {PROCESS: (path, {"test": result}, sample)},
                           {PROCESS: {"unit": "dimensionless", "purpose": "pump_anomaly_monitoring",
                                      "window_samples": SIZE, "sample_interval_ms": INTERVAL_MS,
                                      "evaluation": "single_normal_day_and_single_abnormal_day"}})
        print("Active release:", release)
        print("Example request:", Path(release) / "examples" / (PROCESS + ".json"))
    print("Saved:", out)


if __name__ == "__main__":
    main()
