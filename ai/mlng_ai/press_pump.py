"""Window contract for the KAMP fine-blanking press pump."""
import numpy as np

CHANNELS = ("AI0_Vibration", "AI1_Vibration", "AI2_Current")
SIZE = 30
INTERVAL_MS = 100


def window_features(row):
    if row.get("sample_interval_ms") != INTERVAL_MS:
        raise ValueError("Press pump requires 100 ms sample intervals")
    result = {}
    for channel in CHANNELS:
        signal = np.asarray(row.get(channel), dtype=float)
        if signal.shape != (SIZE,) or not np.isfinite(signal).all():
            raise ValueError(f"{channel} requires {SIZE} consecutive finite samples")
        result[f"{channel}_mean"] = float(signal.mean())
        result[f"{channel}_std"] = float(signal.std())
        result[f"{channel}_peak"] = float(np.abs(signal).max())
    return result
