"""Protect simulation routing metadata and the DDACS parameter scope."""
import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor

from mlng_ai.ddacs import FEATURES, RAW_COLUMNS, parameter_features
from mlng_ai.routing import Router


def example():
    return dict(geometry="concave", curvature_radius=50, bottom_radius=5, wall_angle=10,
                material_scaling_factor=1.075, sheet_metal_thickness=1.,
                friction_coefficient=.1, blankholder_force=250000)


def test_synthetic_route_scope_and_metadata(tmp_path):
    row = example()
    x = np.array([[parameter_features(row)[key] for key in FEATURES]])
    path = tmp_path / "candidate.joblib"
    bounds = {key: [row[key], row[key]] for key in RAW_COLUMNS}
    artifact = {
        "model": DummyRegressor().fit(x, [.9]), "target": "ddacs_sim_min_thickness_mm",
        "feature_recipe": "ddacs_parameters_v1", "synthetic": True, "bounds": bounds,
        "geometries": [{key: row[key] for key in ("geometry", "curvature_radius", "bottom_radius", "wall_angle")}],
        "preprocessing": {"columns": FEATURES, "mean": [0.] * len(FEATURES),
                          "std": [1.] * len(FEATURES), "imputation": [0.] * len(FEATURES),
                          "clip": [-1e12, 1e12]},
    }
    joblib.dump(artifact, path)
    router = Router(tmp_path / "state")
    router.register("ddacs_thickness_sim", path, "specialist", "test")
    result = router.predict("ddacs_thickness_sim", pd.DataFrame([row]))
    assert result["synthetic"] is True
    assert result["rows"][0]["ddacs_sim_min_thickness_mm"] == pytest.approx(.9)
    assert "p" not in result["rows"][0]
    with pytest.raises(ValueError, match="parameter range"):
        router.predict("ddacs_thickness_sim", pd.DataFrame([{**row, "blankholder_force": 999999}]))
    with pytest.raises(ValueError, match="geometry configuration"):
        router.predict("ddacs_thickness_sim", pd.DataFrame([{**row, "geometry": "convex"}]))
    with pytest.raises(ValueError, match="finite"):
        router.predict("ddacs_thickness_sim", pd.DataFrame([{**row, "friction_coefficient": None}]))
