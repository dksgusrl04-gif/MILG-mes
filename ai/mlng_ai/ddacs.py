"""DDACS simulation parameter contract; dimensions are millimetres and newtons."""
import numpy as np

RAW_COLUMNS = ("curvature_radius", "bottom_radius", "wall_angle", "material_scaling_factor",
               "sheet_metal_thickness", "friction_coefficient", "blankholder_force")
FEATURES = [*RAW_COLUMNS, "geometry_concave", "geometry_convex"]


def parameter_features(row):
    geometry = row.get("geometry")
    if geometry not in ("concave", "convex"):
        raise ValueError("DDACS sub-study supports concave or convex geometry")
    values = np.array([row.get(key, np.nan) for key in RAW_COLUMNS], dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("DDACS requires all finite simulation parameters")
    result = dict(zip(RAW_COLUMNS, values))
    result.update(geometry_concave=float(geometry == "concave"),
                  geometry_convex=float(geometry == "convex"))
    return result
