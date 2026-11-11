"""Validate, train and register a synthetic DDACS thickness surrogate."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import h5py
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

from .ddacs import RAW_COLUMNS, FEATURES, parameter_features
from .pilot import PilotEngine, write_json
from .train_process_packs import activate

PROCESS = "ddacs_thickness_sim"
TARGET = "ddacs_sim_min_thickness_mm"


def load_data(root):
    manifest = json.loads((root / "download_manifest.json").read_text(encoding="utf8"))
    if manifest.get("synthetic") is not True:
        raise ValueError("Synthetic source declaration missing")
    reference = pd.read_csv(root / "process_parameters.csv").set_index("index")
    if not reference.index.is_unique:
        raise ValueError("Duplicate simulation index")
    rows = []
    for file in manifest["files"]:
        if not isinstance(file, dict):
            raise ValueError("Download manifest must have full file checksum records")
        path = root / "rddac_selected" / file["file"]
        with path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != file["bytes"] or checksum != file["sha256"]:
            raise ValueError(f"Corrupted simulation file: {path.name}")
        with h5py.File(path) as h:
            sim_id = int(h.attrs["index"])
            expected = reference.loc[sim_id]
            actual = {"geometry": str(h.attrs["geometry"]),
                      **{name: float(h.attrs[name]) for name in RAW_COLUMNS}}
            if actual["geometry"] != expected.geometry:
                raise ValueError(f"Geometry mismatch: {sim_id}")
            if not np.allclose([actual[name] for name in RAW_COLUMNS],
                               expected[list(RAW_COLUMNS)].to_numpy(float), rtol=0, atol=1e-8):
                raise ValueError(f"Parameters mismatch: {sim_id}")
            field = h["OP10/blank/element_shell_thickness"]
            if field.ndim != 2 or field.shape[0] != 4:
                raise ValueError(f"Expected four OP10 blank states: {sim_id}")
            initial, final = field[0], field[-1]
            if not np.isfinite(final).all() or (final <= 0).any():
                raise ValueError(f"Invalid FEM thickness: {sim_id}")
            if not np.allclose(initial, actual["sheet_metal_thickness"], atol=1e-6):
                raise ValueError(f"Initial thickness mismatch: {sim_id}")
            rows.append({**actual, "id": sim_id, "split": expected["split"],
                         TARGET: float(final.min())})
    data = pd.DataFrame(rows)
    if data.id.duplicated().any() or data.duplicated(subset=["geometry", *RAW_COLUMNS]).any():
        raise ValueError("Repeated simulation or parameter combination")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("data", "state", "out"):
        parser.add_argument(f"--{key}", required=True, type=Path)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    data = load_data(args.data)
    x = pd.DataFrame(parameter_features(r) for r in data.to_dict("records"))[FEATURES]
    y = data[TARGET].to_numpy()
    split = {name: data.split.eq(name).to_numpy() for name in ("train", "val", "test")}
    if any(mask.sum() < minimum for mask, minimum in zip(split.values(), (40, 20, 20))):
        raise ValueError("Insufficient predefined train/validation/test simulations")
    train, val, test = (split[key] for key in ("train", "val", "test"))
    center = x.to_numpy()[train].mean(axis=0)
    scale = x.to_numpy()[train].std(axis=0)
    scale[scale == 0] = 1
    normalized = (x.to_numpy() - center) / scale
    models = {
        "trees": ExtraTreesRegressor(n_estimators=200, min_samples_leaf=2, random_state=42),
        "quadratic": make_pipeline(PolynomialFeatures(2, include_bias=False),
                                   StandardScaler(), Ridge(alpha=10)),
    }
    # Baseline learns one retention ratio per geometry, from training only.
    baseline = np.empty(len(data))
    for geometry in sorted(data.geometry.unique()):
        group = data.geometry.eq(geometry).to_numpy()
        thickness = data.sheet_metal_thickness.to_numpy()
        retention = float(np.median(y[train & group] / thickness[train & group]))
        baseline[group] = thickness[group] * retention
    predictions, validation = {}, {}
    for name, model in models.items():
        model.fit(normalized[train], y[train])
        predictions[name] = model.predict(normalized)
        validation[name] = float(mean_absolute_error(y[val], predictions[name][val]))
    selected = min(validation, key=validation.get)
    prediction = predictions[selected]
    def evaluate(mask):
        return {"rows": int(mask.sum()), "mae_mm": float(mean_absolute_error(y[mask], prediction[mask])),
                "ratio_baseline_mae_mm": float(mean_absolute_error(y[mask], baseline[mask])),
                "median_baseline_mae_mm": float(mean_absolute_error(y[mask], np.full(mask.sum(), np.median(y[train])))),
                "r2": float(r2_score(y[mask], prediction[mask]))}
    scores = {name: evaluate(split[name]) for name in ("val", "test")}
    passed = all(v["mae_mm"] < min(v["ratio_baseline_mae_mm"], v["median_baseline_mae_mm"]) for v in scores.values())
    out = args.out / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    bounds = {key: [float(data.loc[train,key].min()), float(data.loc[train,key].max())] for key in RAW_COLUMNS}
    geometries = data[["geometry", "curvature_radius", "bottom_radius", "wall_angle"]].drop_duplicates().to_dict("records")
    report = {"source": "https://doi.org/10.18419/DARUS-4801", "license": "CC BY 4.0",
              "synthetic": True, "selected": selected, "validation_candidates": validation,
              "validation": scores["val"], "test": scores["test"], "gate_passed": passed,
              "train_rows": int(train.sum()), "bounds": bounds,
              "limits": "RDDAC sub-study FEM parameter grid. OP10 minimum shell thickness after springback in mm. "
                        "Within-grid simulator surrogate; independent factory performance unmeasured."}
    write_json(out / "report.json", report)
    data.assign(prediction=prediction, baseline=baseline).to_csv(out / "predictions.csv", index=False)
    artifact = {"model": models[selected], "feature_recipe": "ddacs_parameters_v1",
                "target": TARGET, "synthetic": True, "bounds": bounds, "geometries": geometries,
                "preprocessing": {"columns": FEATURES, "mean": center.tolist(),
                                  "std": scale.tolist(), "imputation": center.tolist(),
                                  "clip": [-1e12, 1e12]}}
    path = out / "ddacs.joblib"
    joblib.dump(artifact, path)
    np.testing.assert_allclose(joblib.load(path)["model"].predict(normalized), prediction)
    print(json.dumps(report, indent=2))
    print("Saved:", out)
    if args.install and passed:
        sample = data.loc[test, ["geometry", *RAW_COLUMNS]].iloc[[0]]
        release = activate(args.state.resolve(), out, {PROCESS: (path, report, sample)},
                           {PROCESS: {"mode": "simulation_surrogate", "unit": "mm",
                                      "synthetic": True, "purpose": "FEM minimum sheet-thickness estimation",
                                      "source": report["source"], "input_bounds": bounds}})
        response = PilotEngine(args.state).predict(PROCESS, sample.to_dict("records"))
        np.testing.assert_allclose(response["rows"][0][TARGET], prediction[test][0])
        assert response["synthetic"] is True and response["rows"][0]["alert"] is None
        print("Verified active route:", PROCESS)
        print("Active release:", release)


if __name__ == "__main__":
    main()
