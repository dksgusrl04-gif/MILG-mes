"""Shared feature contract for a P2S progressive press force trace."""
import numpy as np

CHANNEL = "dowel_deep_drawing_ow"


def force_features(row):
    signal = np.asarray(row.get(CHANNEL), dtype=float)
    if signal.shape != (4096,) or not np.isfinite(signal).all():
        raise ValueError("P2S requires a finite 4096-point deep-drawing force trace")
    blocks = signal.reshape(32, 128)
    values = {f"q{i}": float(value) for i, value in enumerate(np.quantile(
        signal, [.01, .05, .1, .25, .5, .75, .9, .95, .99]))}
    for i, block in enumerate(blocks):
        values.update({f"mean_{i}": float(block.mean()), f"std_{i}": float(block.std())})
    return values
