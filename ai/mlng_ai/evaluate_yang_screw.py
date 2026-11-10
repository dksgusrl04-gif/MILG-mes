"""Offline ranking benchmark for Yang Zhenye's partially labeled tightening data."""
import argparse
import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

MEMBERS = [f"D{i}.json" for i in range(1, 9)]
SOURCE = "https://zenodo.org/records/18007039"


def features(record):
    a, t = (np.asarray(record[k], dtype=float) for k in ("angle", "torque"))
    if a.ndim != 1 or a.size < 8 or a.shape != t.shape or not np.isfinite([a, t]).all():
        return None
    peak = np.abs(t).max()
    if peak <= 0:
        return None
    normalized = t / peak
    progress = np.linspace(0, 1, len(a))
    shape = np.interp(np.linspace(0, 1, 17), progress, normalized)
    return np.r_[shape, np.diff(a).min() / max(np.ptp(a), 1e-9),
                 np.abs(np.diff(normalized)).max()]


def read_archive(path):
    groups = {}
    for name in MEMBERS:
        rows, labels, identities = [], [], set()
        invalid = 0
        with subprocess.Popen(["tar.exe", "-xOf", str(path), name], stdout=subprocess.PIPE) as proc:
            for line in proc.stdout:
                if not line.strip():
                    continue
                record = json.loads(line)
                identity = record["resultId"]
                if identity in identities:
                    raise ValueError(f"Duplicate result ID in {name}")
                identities.add(identity)
                row = features(record)
                if row is None:
                    invalid += 1
                    continue
                rows.append(row)
                labels.append(record.get("cat") is not None)
            if proc.wait():
                raise ValueError(f"Cannot read {name}")
        groups[name] = (np.asarray(rows), np.asarray(labels), invalid)
        print(name, "curves:", len(rows), "marked:", sum(labels), "invalid:", invalid, flush=True)
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with args.archive.open("rb") as f:
        digest = hashlib.file_digest(f, "md5").hexdigest()
    if digest != "396ac2fc0a786ac5dcb3a53e8e87647a":
        raise ValueError("Archive checksum does not match the published research source")
    groups = read_archive(args.archive)
    results = {}
    rng = np.random.default_rng(42)
    for name, (test, marked, invalid) in groups.items():
        training = []
        for other, (data, annotated, _) in groups.items():
            if name != other:
                background = data[~annotated]
                training.append(background[rng.choice(len(background), min(3000, len(background)), replace=False)])
        train = np.concatenate(training)
        scaler = RobustScaler().fit(train)
        model = IsolationForest(n_estimators=100, random_state=42).fit(scaler.transform(train))
        scores = -model.score_samples(scaler.transform(test))
        count = math.ceil(.05 * len(test))
        selected = np.argsort(-scores, kind="stable")[:count]
        results[name] = {"valid": len(test), "invalid": invalid, "marked": int(marked.sum()),
                         "top_5pct_rows": count, "marked_in_top_5pct": int(marked[selected].sum())}
    out = args.out / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    result = {"source": SOURCE, "md5": digest, "results": results,
              "purpose": "Research only; leave one acquisition group out.",
              "limits": "Unmarked curves are unknown and used only as unlabeled training background. "
                        "Top-5-percent overlap counts only annotated anomalies. Not a production recall or FPR."}
    (out / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("Saved:", out)


if __name__ == "__main__":
    main()
