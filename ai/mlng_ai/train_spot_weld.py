"""Train independent spot-weld quality candidates from RSW Insights V3."""
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
import sklearn
from sklearn.base import clone
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit, cross_val_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

MEMBER = "01_CC_BY_4_0_RSW_Insights/Data_RSW.csv"
SOURCE = "https://data.mendeley.com/datasets/rwh8kjzdch/3"
RECIPE = ["Pressure (PSI)", "Welding Time (ms)", "Angle (Deg)"]
FIXED = RECIPE + ["Thickness A (mm)", "Thickness B (mm)"]
SIGNALS = ["Force (N)", "Current (A)"]
TARGETS = {"pull_strength_n": "PullTest (N)", "nugget_diameter_mm": "NuggetDiameter (mm)"}
SEED = 42


def load_samples(raw):
    frame = pd.read_csv(io.BytesIO(raw))
    fields = FIXED + SIGNALS + list(TARGETS.values())
    frame[fields] = frame[fields].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(frame[fields].to_numpy()).all() or frame["Sample ID"].isna().any():
        raise ValueError("Missing or non-finite measurements or sample IDs")
    if set(frame["Material"]) != {"AISI 1010 carbon steel"}:
        raise ValueError("This candidate expects only the published AISI 1010 material")
    samples = frame.groupby("Sample ID", sort=True)
    constant = FIXED + list(TARGETS.values())
    conflicts = samples[constant].nunique().gt(1).any(axis=1)
    table = samples[constant].first().join(samples[SIGNALS].mean().add_suffix(" mean"))
    table = table.join(samples[SIGNALS].max().add_suffix(" max"))
    return frame, table.loc[~conflicts], conflicts.index[conflicts].tolist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with ZipFile(args.pack) as archive:
        raw = archive.read(MEMBER)
    frame, samples, excluded = load_samples(raw)
    groups = samples.groupby(RECIPE, sort=True).ngroup()
    train, test = next(GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=SEED)
                       .split(samples, groups=groups))
    assert not set(groups.iloc[train]) & set(groups.iloc[test])
    features = [c for c in samples if c not in TARGETS.values()]
    X = samples[features]
    out = args.out / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    report = {
        "source": SOURCE, "license": "CC BY 4.0", "csv_sha256": hashlib.sha256(raw).hexdigest(),
        "sklearn": sklearn.__version__, "material": "AISI 1010 carbon steel",
        "raw_rows": len(frame), "samples": len(samples), "excluded_conflicting_ids": excluded,
        "train_samples": len(train), "test_samples": len(test),
        "train_recipes": int(groups.iloc[train].nunique()), "test_recipes": int(groups.iloc[test].nunique()),
        "features": features, "results": {},
    }
    held_out = samples.iloc[test][RECIPE].copy()
    for name, target in TARGETS.items():
        y = samples[target]
        models = {
            "median": DummyRegressor(strategy="median"),
            "ridge": make_pipeline(StandardScaler(), Ridge(alpha=10)),
            "random_forest": RandomForestRegressor(n_estimators=200, min_samples_leaf=5, random_state=SEED),
        }
        scores = {
            key: float(-cross_val_score(model, X.iloc[train], y.iloc[train], groups=groups.iloc[train],
                                        cv=GroupKFold(4), scoring="neg_mean_absolute_error",
                                        error_score="raise").mean())
            for key, model in models.items()
        }
        selected = min(("ridge", "random_forest"), key=scores.get)
        candidate = clone(models[selected]).fit(X.iloc[train], y.iloc[train])
        baseline = models["median"].fit(X.iloc[train], y.iloc[train])
        predicted = candidate.predict(X.iloc[test])
        actual = y.iloc[test]
        mae = float(mean_absolute_error(actual, predicted))
        baseline_predicted = baseline.predict(X.iloc[test])
        baseline_mae = float(mean_absolute_error(actual, baseline_predicted))
        beats_baseline = scores[selected] < scores["median"] and mae < baseline_mae
        report["results"][name] = {
            "candidate": selected, "validation_mae": scores, "test_mae": mae,
            "test_baseline_mae": baseline_mae, "test_r2": float(r2_score(actual, predicted)),
            "beats_baseline": beats_baseline,
        }
        held_out[name + "_actual"] = actual
        held_out[name + "_predicted"] = predicted
        held_out[name + "_baseline"] = baseline_predicted
        model_path = out / (name + ".joblib")
        joblib.dump({"model": candidate, "features": features, "target": target,
                     "source": SOURCE, "sklearn": sklearn.__version__}, model_path)
        restored = joblib.load(model_path)
        np.testing.assert_allclose(restored["model"].predict(X.iloc[test]), predicted)
        print(f"{name}: {selected}, test MAE {mae:.3f}, median {baseline_mae:.3f}, "
              f"beats baseline: {beats_baseline}")
    held_out.to_csv(out / "held_out_predictions.csv")
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
