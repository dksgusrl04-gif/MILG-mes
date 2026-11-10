"""Audit and train research candidates from the local CiP-DMD and RDDAC samples."""
import argparse
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import h5py
import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .pilot import write_json

MCT_TARGET = "groove_depth_mm"
PRESS_TARGET = "op10_scan_height_span_mm"


def stats(values, channels):
    return {f"{name}_{key}": float(value) for name, a in zip(channels, values.T)
            for key, value in (("mean", a.mean()), ("std", a.std()), ("peak_abs", np.abs(a).max()))}


def cip_rows(root):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = root / entry["file"]
        if path.stat().st_size != entry["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"CiP integrity mismatch: {path}")
    q = pd.read_csv(root / "quality_data.csv", sep=";", dtype={"part_id": str}).drop_duplicates()
    ambiguous = sorted(q.loc[q.part_id.duplicated(keep=False), "part_id"].unique())
    q = q.loc[~q.part_id.isin(ambiguous)].set_index("part_id")
    channels = ["aaCurr5", "aaCurr6", "aaTorque5", "aaTorque6", "actSpeed1"]
    rows = []
    for folder in sorted((root / "process_samples").iterdir()):
        if not folder.is_dir():
            continue
        part_id = folder.name.split("_")[0]
        if part_id in ambiguous:
            continue
        features = {}
        for face in ("frontside", "backside"):
            with h5py.File(folder / f"{face}_internal_machine_signals.h5") as h:
                dataset = h["data"]
                header = list(dataset.attrs["column_names"])
                values = dataset[:][:, [header.index(name) for name in channels]]
            if len(values) < 8 or not np.isfinite(values).all():
                raise ValueError(f"Missing or invalid {face} machining signals: {part_id}")
            features.update(stats(values, [f"{face}_{name}" for name in channels]))
        target = float(q.loc[part_id, "groove_depth"])
        if not np.isfinite(target):
            raise ValueError(f"Missing groove-depth inspection: {part_id}")
        rows.append({"id": part_id, "group": part_id[:-2], MCT_TARGET: target,
                     **features})
    return pd.DataFrame(rows), ambiguous


def press_rows(root):
    manifest = json.loads((root / "source.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        with (root / name).open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != expected:
                raise ValueError(f"RDDAC integrity mismatch: {name}")
    recipes = pd.read_csv(root / "process_parameters.csv").set_index("index")
    if not recipes.index.is_unique:
        raise ValueError("Repeated RDDAC metadata index")
    rows = []
    with ZipFile(root / "sample.zip") as z:
        for item in z.infolist():
            if not item.filename.endswith(".h5"):
                continue
            with h5py.File(io.BytesIO(z.read(item))) as h:
                sample_id = int(h.attrs["id"])
                expected = recipes.loc[sample_id]
                for key in ("experiment_id", "geometry", "category", "blankholder_force", "oil_type"):
                    if h.attrs[key] != expected[key]:
                        raise ValueError(f"Mismatch for RDDAC sample {sample_id}: {key}")
                f = h["force"]
                cols = list(f.attrs["columns"])
                units = dict(zip(cols, f.attrs["units"]))
                channels = ["total_force", "punch_pos", "punch_temp"]
                if [units[c] for c in channels] != ["kN", "mm", "degC"]:
                    raise ValueError("Unexpected force trace units")
                trace = f["data"][:][:, [cols.index(c) for c in channels]]
                # Raw scan zero means missing. Span is a proxy, not a drawing dimension.
                scan = h["pointcloud/op10/z"][:].reshape(2000, 3200)[::8, ::8]
                valid = scan[np.isfinite(scan) & (scan != 0)]
                if len(valid) < 1000 or len(trace) < 8 or not np.isfinite(trace).all():
                    raise ValueError(f"Incomplete measurements: {sample_id}")
                span = float(np.diff(np.quantile(valid, [.05, .95]))[0] * .007755)
                rows.append({"id": sample_id, "group": str(expected.geometry),
                             "category": int(expected.category), PRESS_TARGET: span,
                             **stats(trace, channels)})
    return pd.DataFrame(rows)


def train(rows, target, folder):
    if rows.id.duplicated().any():
        raise ValueError("Repeated inspection ID")
    features = [c for c in rows if c not in {"id", "group", "category", target}]
    x, y = rows[features], rows[target]
    model = make_pipeline(StandardScaler(), Ridge(alpha=10))
    prediction, median = np.empty(len(y)), np.empty(len(y))
    groups = rows["group"]
    for fit, test in LeaveOneGroupOut().split(x, y, groups):
        prediction[test] = model.fit(x.iloc[fit], y.iloc[fit]).predict(x.iloc[test])
        median[test] = DummyRegressor(strategy="median").fit(x.iloc[fit], y.iloc[fit]).predict(x.iloc[test])
    model.fit(x, y)
    folder.mkdir(parents=True)
    artifact = folder / "candidate.joblib"
    joblib.dump({"model": model, "features": features, "target": target, "status": "research_only"}, artifact)
    np.testing.assert_allclose(joblib.load(artifact)["model"].predict(x), model.predict(x))
    rows.assign(held_out_prediction=prediction, held_out_median=median).to_csv(folder / "evaluation.csv", index=False)
    result = {"rows": len(x), "groups": int(groups.nunique()), "target": target,
              "mae_mm": float(mean_absolute_error(y, prediction)),
              "median_mae_mm": float(mean_absolute_error(y, median)),
              "observed_min_mm": float(y.min()), "observed_max_mm": float(y.max()),
              "installed": False}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cip", "rddac", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    out = args.out / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    mct, ambiguous = cip_rows(args.cip)
    press = press_rows(args.rddac)
    mct_report = train(mct, MCT_TARGET, out / "mct")
    mct_report.update({"out_of_spec": int((~mct[MCT_TARGET].between(.75, .85)).sum()),
                       "limits_mm": [.75, .85], "ambiguous_inspection_ids_excluded": ambiguous,
                       "feature_scope": "Whole front/back machining logs; ring-groove interval too short",
                       "gate": "Research subset. No out-of-spec specimens for classification if out_of_spec is zero."})
    press_report = train(press, PRESS_TARGET, out / "press")
    press_report.update({"categories": int(press.category.nunique()),
                         "gate": "One sample per condition. Raw scan span is not a dimension tolerance or defect label."})
    report = {"mct": mct_report, "press": press_report}
    write_json(out / "report.json", report)
    print(json.dumps(report, indent=2))
    print("Saved:", out)


if __name__ == "__main__":
    main()
