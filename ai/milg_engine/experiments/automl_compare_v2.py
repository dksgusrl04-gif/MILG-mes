"""AutoML 실험: 지도학습 시나리오를 기본 후보 vs 하이퍼파라미터 탐색(n_iter=12)으로 비교.
사용: python automl_compare.py   (milg_engine 폴더 기준)"""
import copy, json, os, sys
import yaml
HERE = os.path.dirname(os.path.abspath(__file__))
ENG = os.path.dirname(HERE)
sys.path.insert(0, ENG)
import engine  # noqa: E402

SCN = sys.argv[1:] or ["inj_cycle_passfail", "inj_quality_grade", "press_insertion_offset", "ht_lot_defect", "mct_tool_wear"]
KEYS = ["recall", "precision", "false_positive_rate", "pr_auc", "accuracy", "macro_f1", "mae", "r2"]
rows = []
for name in SCN:
    src = os.path.join(ENG, "scenarios", name + ".yaml")
    cfg = yaml.safe_load(open(src, encoding="utf-8"))
    cfg["data_root"] = os.path.abspath(os.path.join(ENG, "scenarios", cfg.get("data_root", ".")))
    for mode in ("base", "search"):
        c = copy.deepcopy(cfg)
        if mode == "search":
            c.setdefault("model", {})["search"] = {"n_iter": 12}
        p = os.path.join(HERE, f"_tmp_{name}_{mode}.yaml")
        yaml.safe_dump(c, open(p, "w", encoding="utf-8"), allow_unicode=True)
        out = os.path.join(HERE, "_automl_out_" + mode)
        os.makedirs(os.path.join(out, "_cache"), exist_ok=True)
        # 기존 특성 캐시 재사용
        for f in os.listdir(os.path.join(ENG, "outputs", "_cache")):
            if f.startswith(cfg["scenario"] + "__") and "partial" not in f:
                dst = os.path.join(out, "_cache", f)
                if not os.path.exists(dst):
                    import shutil; shutil.copy(os.path.join(ENG, "outputs", "_cache", f), dst)
        r = engine.run(p, out, 1e9)
        t = r["test"]
        row = {"scenario": r["scenario"], "mode": mode, "model": r["best_model"], "cv_best": r["cv_scores"][r["best_model"]],
               **{k: t[k] for k in KEYS if k in t}, "params": r.get("best_params")}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False, default=str), flush=True)
with open(os.path.join(HERE, "automl_compare.jsonl"), "a", encoding="utf-8") as fh:
    for row in rows:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
