"""Validate inspection-linked batches, optionally train on a dedicated local pilot state."""
import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np

from .pilot import PilotEngine, context_key, write_json

TARGETS = {"mct_stiehl": {"dimension_mm"}, "pressfit": {"insertion_depth_mm", "pullout_force_n"}}


def prepare(engine, source):
    records = []
    seen = set()
    scope = None
    for line_no, line in enumerate(source.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            process = item["process"]
            inspection = item["inspection"]
            target = inspection["target"]
            characteristic = inspection["characteristic"]
            if target not in TARGETS.get(process, set()) or not isinstance(characteristic, str) or not characteristic.strip():
                raise ValueError("Invalid process/inspection target or empty characteristic")
            if item.get("demo") is not False:
                raise ValueError("Real data requires demo=false; template examples cannot be imported")
            ctx = item["context"].copy()
            context_key(ctx)
            # Separate inspections on the same product and program.
            ctx["program"] = json.dumps([ctx["program"], target, characteristic], ensure_ascii=False)
            key = (process, context_key(ctx))
            if scope is not None and scope != key:
                raise ValueError("One process, machine, part, program and characteristic per batch")
            scope = key
            cycle, lot = item["cycle_id"], item["lot_id"]
            if not isinstance(cycle, str) or not 1 <= len(cycle.strip()) <= 160 or cycle in seen:
                raise ValueError("Missing or duplicate cycle_id")
            if not isinstance(lot, str) or not lot.strip():
                raise ValueError("Missing lot_id")
            seen.add(cycle)
            when = datetime.fromisoformat(item["recorded_at"])
            if when.utcoffset() is None:
                raise ValueError("recorded_at requires an explicit time zone")
            for name, limit in (("actor", 100), ("evidence", 1000)):
                value = inspection[name]
                if not isinstance(value, str) or not value.strip() or len(value) > limit:
                    raise ValueError(f"Invalid inspection {name}")
            value = inspection["measured"]
            low, high = inspection.get("lower"), inspection.get("upper")
            for number in (value, *[v for v in (low, high) if v is not None]):
                if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                    raise ValueError("Inspection values must be finite numbers")
            if low is None and high is None or low is not None and high is not None and low >= high:
                raise ValueError("Provide a valid lower and/or upper tolerance")
            outcome = "fail" if (low is not None and value < low) or (high is not None and value > high) else "pass"
            row = item["input"]
            if not isinstance(row, dict):
                raise ValueError("input must be an object")
            features = engine._feature_frame(process, [row])
            if not np.isfinite(features.to_numpy(float)).all():
                raise ValueError("Nonfinite features")
            event_id = hashlib.sha256(json.dumps([scope, cycle], ensure_ascii=False).encode()).hexdigest()
            payload = {"process": process, "cycle_id": event_id, "lot_id": lot, "context": ctx,
                       "rows": [row], "demo": False, "recorded_at": when.isoformat(),
                       "source_cycle_id": cycle, "inspection_measurement": {
                           "target": target, "characteristic": characteristic,
                           "measured": value, "lower": low, "upper": high}}
            records.append((when, payload, outcome, inspection))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"Line {line_no}: {exc}") from exc
    if not records:
        raise ValueError("Input batch is empty")
    records.sort(key=lambda record: record[0])
    if len({(r[1]["inspection_measurement"]["lower"], r[1]["inspection_measurement"]["upper"]) for r in records}) != 1:
        raise ValueError("Use one tolerance revision per batch")
    lots = list(dict.fromkeys(r[1]["lot_id"] for r in records))
    positions = {lot: index for index, lot in enumerate(lots)}
    order = [positions[r[1]["lot_id"]] for r in records]
    if order != sorted(order):
        raise ValueError("Lots overlap in time; resolve lot boundaries before training")
    a, b = max(1, int(.6 * len(lots))), max(2, int(.8 * len(lots)))
    splits = {}
    for name, group in zip(("train", "validation", "test"), (lots[:a], lots[a:b], lots[b:])):
        outcomes = [r[2] for r in records if r[1]["lot_id"] in group]
        splits[name] = {"lots": group, "pass": outcomes.count("pass"), "fail": outcomes.count("fail")}
    ready = (len(records) >= 100 and len(lots) >= 5
             and all(min(v["pass"], v["fail"]) >= minimum
                     for v, minimum in zip(splits.values(), (10, 5, 5))))
    return records, {"process": scope[0], "cycles": len(records), "lots": len(lots),
                     "splits": splits, "ready_for_field_training": ready}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "state", "report"):
        p.add_argument(f"--{name}", required=True, type=Path)
    p.add_argument("--train", action="store_true")
    args = p.parse_args()
    engine = PilotEngine(args.state)
    records, report = prepare(engine, args.input)
    if args.train and report["ready_for_field_training"]:
        first = records[0][1]
        with engine.db() as db:
            existing = db.execute("SELECT COUNT(*) FROM events WHERE scope=? AND process=?",
                                  (context_key(first["context"]), first["process"])).fetchone()[0]
        if existing:
            raise ValueError("Training scope already has events; use a fresh dedicated pilot state")
        for _, payload, outcome, inspection in records:
            engine.submit(payload)
            engine.feedback(payload["cycle_id"], outcome, inspection["actor"], inspection["evidence"])
        report["field_training"] = engine.train_field(first["process"], first["context"])
    write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
