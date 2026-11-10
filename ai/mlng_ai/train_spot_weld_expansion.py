"""Evaluate and save separate MILG spot-weld expansion candidates."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .train_spot_weld import MEMBER, load_samples

AISI = "01_AISI1010/"
B1500 = "04_B1500HS_HC340_590DP/"
IMPACT = AISI + "impact_specimens.csv"
AISI_DIAMETER = AISI + "nugget_recipe_means.csv"
DP800 = "02_DP800/experimental_runs.csv"
DP600 = "03_DP600/measured_nugget_size.csv"
CCD = B1500 + "ccd_nugget_runs.csv"
REPEAT = B1500 + "recommended_recipe_validation.csv"
TASKS = [
    ("aisi1010_impact_j", IMPACT, ["current_ka", "time_cycles"], "impact_energy_j"),
    ("aisi1010_surface_diameter_mm", AISI_DIAMETER, ["current_ka", "time_cycles"],
     "mean_surface_nugget_diameter_mm"),
    ("dp800_lap_load_kn", DP800, ["current_ka", "electrode_pressure_mpa", "weld_time_s"],
     "lap_tensile_shear_failure_load_kn"),
    ("dp800_cross_load_kn", DP800, ["current_ka", "electrode_pressure_mpa", "weld_time_s"],
     "cross_tensile_failure_load_kn"),
    ("dp800_hardness_hv", DP800, ["current_ka", "electrode_pressure_mpa", "weld_time_s"],
     "nugget_zone_hardness_hv"),
    ("dp600_diameter_mm", DP600, ["current_ka", "time_cycles", "electrode_force_kn"],
     "measured_nugget_size_mm"),
    ("b1500_ccd_diameter_mm", CCD, ["pulse1_current_ka", "pulse1_cycles", "electrode_force_kn"],
     "nugget_diameter_mm"),
]


def ridge():
    return make_pipeline(StandardScaler(), Ridge(alpha=2))


def checked_data(frame, columns):
    data = frame[columns].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(data.to_numpy()).all():
        raise ValueError(f"Invalid measurement in {columns}")
    return data


def errors(actual, predicted, baseline, groups):
    loss = pd.DataFrame({
        "group": np.asarray(groups),
        "model": np.abs(np.asarray(actual) - predicted),
        "baseline": np.abs(np.asarray(actual) - baseline),
    })
    return loss.groupby("group")[["model", "baseline"]].mean().mean().to_dict()


def evaluate_task(frame, name, features, target, out):
    data = checked_data(frame, features + [target])
    x, y = data[features], data[target]
    groups = data.groupby(features).ngroup()
    predictions = np.empty(len(data))
    baselines = np.empty(len(data))
    model = ridge()
    for train, test in GroupKFold(min(5, groups.nunique())).split(x, y, groups):
        predictions[test] = clone(model).fit(x.iloc[train], y.iloc[train]).predict(x.iloc[test])
        baselines[test] = DummyRegressor(strategy="median").fit(
            x.iloc[train], y.iloc[train]).predict(x.iloc[test])
    scores = errors(y, predictions, baselines, groups)
    report = {"rows": len(y), "conditions": int(groups.nunique()), "condition_balanced_mae": scores}
    if "row_type" in frame:
        report["mae_by_row_type"] = {
            kind: errors(y[mask], predictions[mask], baselines[mask], groups[mask])
            for kind in frame["row_type"].unique()
            for mask in [frame["row_type"].eq(kind)]
        }
    candidate = model.fit(x, y)
    path = out / f"{name}.joblib"
    joblib.dump({"model": candidate, "features": features, "target": target, "status": "research"}, path)
    np.testing.assert_allclose(joblib.load(path)["model"].predict(x), candidate.predict(x))
    return report


def transfer_check(original, frames):
    # Amperage is the only shared input in all three tables. Original values
    # are measured means, whereas the two papers report settings.
    sources = {
        "original_aisi1010": original[["Current (A) mean", "NuggetDiameter (mm)"]]
        .rename(columns={"Current (A) mean": "current_ka", "NuggetDiameter (mm)": "diameter_mm"})
        .assign(current_ka=lambda d: d["current_ka"] / 1000),
        "new_aisi1010": frames[AISI_DIAMETER][["current_ka", "mean_surface_nugget_diameter_mm"]]
        .rename(columns={"mean_surface_nugget_diameter_mm": "diameter_mm"}),
        "dp600": frames[DP600][["current_ka", "measured_nugget_size_mm"]]
        .rename(columns={"measured_nugget_size_mm": "diameter_mm"}),
    }
    scores = {}
    for held_out, test in sources.items():
        parts = [data for source, data in sources.items() if source != held_out]
        train = pd.concat(parts)
        weights = np.concatenate([np.full(len(part), len(train) / (len(parts) * len(part))) for part in parts])
        x, y = train[["current_ka"]], train["diameter_mm"]
        model = ridge().fit(x, y, ridge__sample_weight=weights)
        base = DummyRegressor(strategy="median").fit(x, y, sample_weight=weights)
        xt, yt = test[["current_ka"]], test["diameter_mm"]
        scores[held_out] = {
            "rows": len(test), "mae_mm": mean_absolute_error(yt, model.predict(xt)),
            "baseline_mae_mm": mean_absolute_error(yt, base.predict(xt)),
            "outside_train_current_range_rows": int((~xt["current_ka"].between(
                x["current_ka"].min(), x["current_ka"].max())).sum()),
        }
    return scores


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("pack", "expansion", "out"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    args = parser.parse_args()
    with ZipFile(args.pack) as archive:
        original_raw = archive.read(MEMBER)
        _, original, _ = load_samples(original_raw)
    files = {task[1] for task in TASKS} | {REPEAT}
    with ZipFile(args.expansion) as archive:
        manifest = {entry["file"]: entry for entry in json.loads(archive.read("MANIFEST.json"))}
        frames = {}
        for name in sorted(files):
            raw = archive.read(name)
            if hashlib.sha256(raw).hexdigest() != manifest[name]["sha256"]:
                raise ValueError(f"Checksum mismatch: {name}")
            frames[name] = pd.read_csv(BytesIO(raw))
            if len(frames[name]) != manifest[name]["rows"]:
                raise ValueError(f"Row count mismatch: {name}")
    out = args.out / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    report = {
        "status": "research_candidates", "source_tables": [manifest[name] for name in sorted(files)],
        "original_csv_sha256": hashlib.sha256(original_raw).hexdigest(), "tasks": {},
        "transfer_limit": "Current-only comparison. Measured current vs settings; measurement methods differ.",
    }
    for name, file, features, target in TASKS:
        result = evaluate_task(frames[file], name, features, target, out)
        report["tasks"][name] = result
        scores = result["condition_balanced_mae"]
        print(f"{name}: {result['rows']} rows, {result['conditions']} conditions, "
              f"MAE {scores['model']:.3f}, median {scores['baseline']:.3f}")
    repeated = frames[REPEAT]
    repeat_features = ["pulse1_current_ka", "pulse2_current_ka", "electrode_force_kn",
                       "pulse1_cycles", "pulse2_cycles"]
    if len(repeated[repeat_features].drop_duplicates()) != 1:
        raise ValueError("Expected one recipe for the repeatability reference")
    targets = ["nugget_diameter_mm", "tensile_shear_force_kn"]
    values = checked_data(repeated, targets)
    report["single_recipe_reference"] = {
        "rows": len(repeated), "recipe": repeated[repeat_features].iloc[0].to_dict(),
        "targets": values.agg(["mean", "std", "min", "max"]).to_dict(),
    }
    report["leave_source_out_current_only"] = transfer_check(original, frames)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
