"""INJ-01: 불량이 있는 작업일을 하나씩 평가로 빼서(나머지 날로 학습) 결과를 합친다.
   before = 중복 행 그대로(행 단위), after = LH·RH 합친 샷 단위.
사용: python inj_day_holdout.py   (milg_engine/experiments 에서)"""
import copy
import json
import os
import sys

import numpy as np
import pandas as pd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ENG = os.path.dirname(HERE)
sys.path.insert(0, ENG)
import engine  # noqa: E402

DAYS = ["2020-10-16", "2020-10-27", "2020-11-03"]   # 불량이 있는 날
base = yaml.safe_load(open(os.path.join(ENG, "scenarios", "inj_cycle_passfail.yaml"), encoding="utf-8"))
base["data_root"] = os.path.abspath(os.path.join(ENG, "scenarios", base.get("data_root", ".")))


def variant(mode):
    c = copy.deepcopy(base)
    if mode == "before":           # 예전 설정: 행 그대로 + filters
        u = c["unit"]
        c["filters"] = u.pop("pre_filters")
        u.pop("collapse")
        u["id"] = "_id"
    return c


summary = {}
for mode in ("before", "after"):
    preds = []
    for d in DAYS:
        c = variant(mode)
        c["scenario"] = f"INJ-01-{mode}-{d}"
        c["split"]["test_groups"] = [d]
        p = os.path.join(HERE, f"_tmp_inj_{mode}_{d}.yaml")
        yaml.safe_dump(c, open(p, "w", encoding="utf-8"), allow_unicode=True)
        r = engine.run(p, os.path.join(HERE, "_inj_holdout_out"), 1e9)
        pr = pd.read_csv(os.path.join(HERE, "_inj_holdout_out", c["scenario"], "predictions.csv"))
        t = pr[pr["split"] == "test"].copy()
        t["day"], t["model"] = d, r["best_model"]
        preds.append(t)
        print(mode, d, r["best_model"], {k: r["test"][k] for k in ("n", "n_ng", "recall", "precision", "false_positive_rate")}, flush=True)
    P = pd.concat(preds)
    y, yp = P["y_true"].astype(int), P["y_pred"].astype(int)
    tp, fp = int(((y == 1) & (yp == 1)).sum()), int(((y == 0) & (yp == 1)).sum())
    fn, tn = int(((y == 1) & (yp == 0)).sum()), int(((y == 0) & (yp == 0)).sum())
    from sklearn.metrics import average_precision_score
    summary[mode] = {"units": int(len(P)), "ng": int(y.sum()), "tp": tp, "fn": fn, "fp": fp, "tn": tn,
                     "recall": round(tp / max(1, tp + fn), 3), "precision": round(tp / max(1, tp + fp), 3),
                     "false_positive_rate": round(fp / max(1, fp + tn), 4),
                     "pr_auc_pooled": round(float(average_precision_score(y, P["score"])), 3),
                     "base_rate": round(float(y.mean()), 4)}
    print(mode, "합계", summary[mode], flush=True)
json.dump(summary, open(os.path.join(HERE, "inj_day_holdout.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
