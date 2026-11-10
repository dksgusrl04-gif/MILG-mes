"""Install the evaluated AISI 1010 surface-diameter model in the local pilot."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO

import joblib
import numpy as np
import pandas as pd

from .pilot import PilotEngine
from .train_process_packs import activate
from .train_spot_weld_expansion import AISI_DIAMETER

PROCESS = "spot_weld_aisi1010"
TARGET = "spot_weld_nugget_diameter_mm"
COLUMNS = ["current_ka", "time_cycles"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("state", "expansion", "run"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    metrics = report["tasks"]["aisi1010_surface_diameter_mm"]["condition_balanced_mae"]
    if not 0 <= metrics["model"] < metrics["baseline"]:
        raise ValueError("AISI 1010 candidate did not beat its grouped median baseline")
    with ZipFile(args.expansion) as pack:
        raw = pack.read(AISI_DIAMETER)
    source = next(entry for entry in report["source_tables"] if entry["file"] == AISI_DIAMETER)
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("Evaluation and expansion data do not match")
    data = pd.read_csv(BytesIO(raw))
    sample = data[COLUMNS].iloc[[0]]
    candidate = joblib.load(run / "aisi1010_surface_diameter_mm.joblib")
    if candidate["features"] != COLUMNS or candidate["target"] != "mean_surface_nugget_diameter_mm":
        raise ValueError("Candidate input or output does not match this pilot route")
    model = candidate["model"]
    scaler = model.named_steps["standardscaler"]
    artifact = {
        "model": model.named_steps["ridge"], "target": TARGET,
        "feature_recipe": "spot_weld_aisi1010_v1",
        "supported_settings": data[COLUMNS].drop_duplicates().values.tolist(),
        "preprocessing": {"columns": COLUMNS, "imputation": scaler.mean_.tolist(),
                          "mean": scaler.mean_.tolist(), "std": scaler.scale_.tolist()},
    }
    expected = float(model.predict(sample)[0])
    np.testing.assert_allclose(
        artifact["model"].predict(scaler.transform(data[COLUMNS])), model.predict(data[COLUMNS]))
    path = run / "pilot_spot_weld_aisi1010.joblib"
    joblib.dump(artifact, path)
    release = activate(args.state.resolve(), run, {PROCESS: (path, {"test": metrics}, sample)}, {
        PROCESS: {"unit": "mm", "material": "AISI 1010", "electrode_force_kn_approx": 4,
                  "supported_settings": artifact["supported_settings"],
                  "target_scope": "recipe_mean_surface_nugget_diameter",
                  "evaluation": "within_source_grouped_cv_on_nine_recipe_means"},
    })
    pilot = PilotEngine(args.state.resolve())
    result = pilot.predict(PROCESS, sample.to_dict("records"))
    np.testing.assert_allclose(result["rows"][0][TARGET], expected)
    if result["rows"][0]["alert"] is not None or result["decision_status"] != "benchmark_estimate":
        raise RuntimeError("Unexpected spot weld pilot decision policy")
    for invalid in ({"current_ka": 30, "time_cycles": 4}, {"current_ka": 3}):
        try:
            pilot.predict(PROCESS, [invalid])
        except ValueError:
            pass
        else:
            raise RuntimeError("Spot weld pilot accepted unsupported inputs")
    print(f"Active pilot release: {release}")
    print(f"Verified {PROCESS}: {expected:.3f} mm")
    print(f"Example request: {Path(release) / 'examples' / (PROCESS + '.json')}")


if __name__ == "__main__":
    main()
