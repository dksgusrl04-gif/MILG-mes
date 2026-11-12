"""실험 2: 실제 불량이 있는 공개 데이터로 MILG 이상 탐지 방식을 검증한다.

방식은 MCT-02와 같다: 기준은 '양품'만으로 잡고(같은 맥락끼리 robust z → 희소도 → 상위 k 평균),
임계값은 기준 점수 분포의 95%(WARN)·99%(NG). 평가 대상(공구/실험/케이스)은 기준에서 통째로 뺀다.

A. HMoTP 2024  : 공구 3개×절삭 100회, 실측 마모. 불량 = 마모 ≥150, 정상 = 마모 <100. 공구 하나씩 빼서 평가
B. NASA milling: 16케이스 167회, 플랭크 마모 VB(mm). 불량 = VB ≥0.3 (ISO 3685 공구수명 기준), 정상 = VB <0.15
C. CNC milling : 25실험, 공구 worn/unworn. 단위 = 실험×가공단계. 실험 하나씩 빼서 평가
D. ProBayes V2 : 사출 564사이클, 수동 검사 OK/NOK. 설비 측정값(QUA_·TCE/TCN 평균)만 사용. 실험조건 하나씩 빼서 평가
사용: python public_validation.py <milg 데이터 루트> <결과 json>
"""
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mct_methods import Scorer  # noqa: E402

Q_W, Q_N = 0.95, 0.99


def evaluate(F, ctx, is_base, is_test, y_bad, y_ok, k=3, min_n=10):
    """is_base: 기준(양품) 마스크, is_test: 평가 마스크, y_bad/y_ok: 평가 대상 중 불량/정상"""
    F = F.reset_index(drop=True)
    ctx = pd.Series(np.asarray(ctx), index=F.index).astype(str)
    base = pd.Series(np.asarray(is_base), index=F.index)
    sc = Scorer(F, ctx, base, k=k, min_n=min_n)
    s_base = sc.score(F[base], ctx[base])
    tw, tn = np.quantile(s_base, Q_W), np.quantile(s_base, Q_N)
    s = sc.score(F, ctx)
    return s, tw, tn


def summarize(s, tw, tn, bad, ok):
    bad, ok = np.asarray(bad, bool), np.asarray(ok, bool)
    out = {"n_bad": int(bad.sum()), "n_ok": int(ok.sum())}
    if bad.any() and ok.any():
        m = bad | ok
        out["auc"] = round(float(roc_auc_score(bad[m], s[m])), 3)
    out["detect_warn_or_ng"] = round(float((s[bad] >= tw).mean()), 3) if bad.any() else None
    out["detect_ng"] = round(float((s[bad] >= tn).mean()), 3) if bad.any() else None
    out["false_alarm_warn_or_ng"] = round(float((s[ok] >= tw).mean()), 3) if ok.any() else None
    out["false_alarm_ng"] = round(float((s[ok] >= tn).mean()), 3) if ok.any() else None
    return out


def pooled(parts):
    S = np.concatenate([p[0] for p in parts])
    TW = np.concatenate([np.full(len(p[0]), p[1]) for p in parts])
    TN = np.concatenate([np.full(len(p[0]), p[2]) for p in parts])
    B = np.concatenate([p[3] for p in parts]).astype(bool)
    O = np.concatenate([p[4] for p in parts]).astype(bool)
    out = {"n_bad": int(B.sum()), "n_ok": int(O.sum())}
    m = B | O
    if B.any() and O.any():
        out["auc"] = round(float(roc_auc_score(B[m], S[m])), 3)
    out["detect_warn_or_ng"] = round(float((S[B] >= TW[B]).mean()), 3)
    out["detect_ng"] = round(float((S[B] >= TN[B]).mean()), 3)
    out["false_alarm_warn_or_ng"] = round(float((S[O] >= TW[O]).mean()), 3)
    out["false_alarm_ng"] = round(float((S[O] >= TN[O]).mean()), 3)
    return out


# ---------------------------------------------------------------- A. HMoTP
def hmotp(root):
    store = pd.read_pickle(os.path.join(root, "milg_engine/outputs/_cache/MCT-01__files.pkl"))
    rows = []
    for k, v in store.items():
        if k == "__cfg__":
            continue
        m = re.search(r"CuttingSignals(T\d+)/T\d+_(\d+)\.csv", k)
        rows.append({"tool": m.group(1), "cut": int(m.group(2)), **v})
    df = pd.DataFrame(rows)
    hm = os.path.join(root, "MCT/HMoTP 2024-20261012T030348Z-1-001 (1)/HMoTP 2024")
    wear = {t: pd.read_csv(os.path.join(hm, f"ToolWear{t}.csv"))["Mean"].to_numpy() for t in df["tool"].unique()}
    df["wear"] = [wear[t][c - 1] for t, c in zip(df["tool"], df["cut"])]
    feats = [c for c in df.columns if "__" in c]
    parts, per = [], {}
    for t in sorted(df["tool"].unique()):
        base = (df["tool"] != t) & (df["wear"] < 100)
        test = df["tool"] == t
        s, tw, tn = evaluate(df[feats], ["all"] * len(df), base, test, None, None, min_n=5)
        bad = (test & (df["wear"] >= 150)).to_numpy()
        ok = (test & (df["wear"] < 100)).to_numpy()
        per[t] = summarize(s, tw, tn, bad, ok)
        parts.append((s[test.to_numpy()], tw, tn, bad[test.to_numpy()], ok[test.to_numpy()]))
        # 처음 경보가 난 시점의 마모량 (연속 3회 WARN 이상)
        st = s[test.to_numpy()]
        wt = df.loc[test, "wear"].to_numpy()
        order = np.argsort(df.loc[test, "cut"].to_numpy())
        flag = (st[order] >= tw).astype(int)
        run = np.convolve(flag, np.ones(3, int), "valid")
        first = int(np.argmax(run >= 3)) if (run >= 3).any() else None
        per[t]["first_sustained_alarm_wear"] = round(float(wt[order][first]), 1) if first is not None else None
    return {"per_tool": per, "pooled": pooled(parts), "rule": "불량=마모≥150, 정상=마모<100, 기준=다른 공구의 마모<100 절삭"}


# ---------------------------------------------------------------- B. NASA milling
def nasa(root):
    m = pd.read_csv(os.path.join(root, "MCT/mill.csv")).dropna(subset=["VB"])
    feats = ["smcAC", "smcDC", "vib_table", "vib_spindle", "AE_table", "AE_spindle"]
    m = m[(m[feats] != 0).all(axis=1)].reset_index(drop=True)          # 센서 값이 0인 잘못된 행 제외
    ctx = m["DOC"].astype(str) + "|" + m["feed"].astype(str) + "|" + m["material"].astype(str)
    parts = []
    for c in sorted(m["case"].unique()):
        base = (m["case"] != c) & (m["VB"] < 0.15)
        test = (m["case"] == c).to_numpy()
        s, tw, tn = evaluate(m[feats], ctx, base, test, None, None, k=2, min_n=3)
        bad = (test & (m["VB"] >= 0.3)).to_numpy()
        ok = (test & (m["VB"] < 0.15)).to_numpy()
        parts.append((s[test], tw, tn, bad[test], ok[test]))
    return {"pooled": pooled(parts), "runs": int(len(m)),
            "rule": "불량=VB≥0.3mm, 정상=VB<0.15mm, 맥락=절삭깊이·이송·소재, 케이스 하나씩 빼서 평가"}


# ---------------------------------------------------------------- C. CNC milling (worn/unworn)
def cnc(root):
    meta = pd.read_csv(os.path.join(root, "MCT/train.csv"))
    d = os.path.join(root, "MCT/CNC Virtual Data set _v2-20261109T022240Z-1-001/CNC Virtual Data set _v2")
    chans = ["X1_OutputCurrent", "Y1_OutputCurrent", "Z1_OutputCurrent", "S1_OutputCurrent", "X1_OutputPower",
             "Y1_OutputPower", "S1_OutputPower", "X1_CurrentFeedback", "Y1_CurrentFeedback", "S1_CurrentFeedback"]
    rows = []
    for f in sorted(glob.glob(os.path.join(d, "experiment_*.csv"))):
        no = int(re.search(r"(\d+)", os.path.basename(f)).group(1))
        x = pd.read_csv(f)
        x = x.rename(columns=lambda c: c.replace("X_", "X1_").replace("Y_", "Y1_").replace("Z_", "Z1_").replace("S_", "S1_"))
        x = x[x["Machining_Process"].astype(str).str.startswith("Layer")]
        for stage, g in x.groupby("Machining_Process"):
            r = {"exp": no, "stage": stage}
            for c in chans:
                if c in g:
                    v = pd.to_numeric(g[c], errors="coerce")
                    r[c + "__mean"], r[c + "__std"], r[c + "__max"] = v.mean(), v.std(), v.abs().max()
            rows.append(r)
    U = pd.DataFrame(rows).merge(meta.rename(columns={"No": "exp"}), on="exp")
    feats = [c for c in U.columns if "__" in c]
    U = U.dropna(subset=feats, how="all")
    ctx = U["stage"].astype(str) + "|f" + U["feedrate"].astype(str)
    exp_scores = []
    for e in sorted(U["exp"].unique()):
        base = (U["exp"] != e) & (U["tool_condition"] == "unworn")
        test = (U["exp"] == e).to_numpy()
        s, tw, tn = evaluate(U[feats].fillna(0), ctx, base, test, None, None, k=3, min_n=2)
        exp_scores.append({"exp": int(e), "worn": U.loc[test, "tool_condition"].iloc[0] == "worn",
                           "mean_score": float(np.mean(s[test])), "share_warn": float((s[test] >= tw).mean())})
    E = pd.DataFrame(exp_scores)
    return {"experiments": int(len(E)), "worn": int(E["worn"].sum()),
            "auc_mean_score": round(float(roc_auc_score(E["worn"], E["mean_score"])), 3),
            "auc_share_warn": round(float(roc_auc_score(E["worn"], E["share_warn"])), 3),
            "worn_median_share_warn": round(float(E.loc[E.worn, "share_warn"].median()), 3),
            "unworn_median_share_warn": round(float(E.loc[~E.worn, "share_warn"].median()), 3),
            "rule": "단위=실험×가공단계(Layer), 맥락=가공단계·이송속도, 기준=다른 실험의 unworn, 실험 단위 AUC"}


# ---------------------------------------------------------------- D. ProBayes
def probayes(root):
    p = pd.read_parquet(os.path.join(root, "사출/MILG_01_Injection/ProBayes_V2/dataset_V2.parquet"))
    feats = [c for c in p.columns if (c.startswith("QUA_") or (c.startswith(("TCE_", "TCN_")) and c.endswith("Mean")))
             and pd.api.types.is_numeric_dtype(p[c]) and p[c].nunique() > 1 and "Set" not in c]
    F = p[feats].astype(float)
    parts, per_exp = [], {}
    for e in sorted(p["MET_ExperimentNumber"].unique()):
        base = (p["MET_ExperimentNumber"] != e) & (p["LBL_NOK"] == 0)
        test = (p["MET_ExperimentNumber"] == e).to_numpy()
        s, tw, tn = evaluate(F.fillna(F.median()), ["all"] * len(p), base, test, None, None, k=3, min_n=10)
        bad = (test & (p["LBL_NOK"] == 1)).to_numpy()
        ok = (test & (p["LBL_NOK"] == 0)).to_numpy()
        parts.append((s[test], tw, tn, bad[test], ok[test]))
        per_exp[e] = {"nok": int(bad.sum()), "ok": int(ok.sum()), "flag_warn": round(float((s[test] >= tw).mean()), 2)}
    return {"pooled": pooled(parts), "features": len(feats), "per_experiment": per_exp,
            "rule": "설비 측정값만(QUA_·TCE/TCN 평균), 기준=다른 실험조건의 OK 사이클, 실험조건 하나씩 빼서 평가"}


if __name__ == "__main__":
    root, out = sys.argv[1], sys.argv[2]
    R = {}
    for name, fn in [("A_HMoTP", hmotp), ("B_NASA_mill", nasa), ("C_CNC_mill", cnc), ("D_ProBayes", probayes)]:
        try:
            R[name] = fn(root)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            R[name] = {"error": f"{type(e).__name__}: {e}"}
        print(name, json.dumps({k: v for k, v in R[name].items() if k != "per_experiment"}, ensure_ascii=False), flush=True)
    json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------- A2. HMoTP, 같은 공구의 첫 N회를 기준으로
def hmotp_self(root, n_first=10):
    store = pd.read_pickle(os.path.join(root, "milg_engine/outputs/_cache/MCT-01__files.pkl"))
    rows = []
    for k, v in store.items():
        if k == "__cfg__":
            continue
        m = re.search(r"CuttingSignals(T\d+)/T\d+_(\d+)\.csv", k)
        rows.append({"tool": m.group(1), "cut": int(m.group(2)), **v})
    df = pd.DataFrame(rows)
    hm = os.path.join(root, "MCT/HMoTP 2024-20261012T030348Z-1-001 (1)/HMoTP 2024")
    wear = {t: pd.read_csv(os.path.join(hm, f"ToolWear{t}.csv"))["Mean"].to_numpy() for t in df["tool"].unique()}
    df["wear"] = [wear[t][c - 1] for t, c in zip(df["tool"], df["cut"])]
    feats = [c for c in df.columns if "__" in c]
    parts, per = [], {}
    for t in sorted(df["tool"].unique()):
        d = df[df["tool"] == t].sort_values("cut").reset_index(drop=True)
        base = d["cut"] <= n_first
        s, tw, tn = evaluate(d[feats], ["all"] * len(d), base, ~base, None, None, min_n=5)
        test = (~base).to_numpy()
        bad = (test & (d["wear"] >= 150)).to_numpy()
        ok = (test & (d["wear"] < 100)).to_numpy()
        per[t] = summarize(s, tw, tn, bad, ok)
        flag = (s >= tw).astype(int) * test
        run = np.convolve(flag, np.ones(3, int), "valid")
        first = int(np.argmax(run >= 3)) if (run >= 3).any() else None
        per[t]["first_sustained_alarm_wear"] = round(float(d["wear"][first]), 1) if first is not None else None
        per[t]["baseline_wear_max"] = round(float(d.loc[base, "wear"].max()), 1)
        parts.append((s[test], tw, tn, bad[test], ok[test]))
    return {"per_tool": per, "pooled": pooled(parts), "rule": f"기준=같은 공구 첫 {n_first}회, 불량=마모≥150, 정상=마모<100"}


# ---------------------------------------------------------------- A3. 자기 기준 점수 + 다른 공구로 맞춘 임계값
def hmotp_self_calibrated(root, n_first=10, wear_cut=125):
    store = pd.read_pickle(os.path.join(root, "milg_engine/outputs/_cache/MCT-01__files.pkl"))
    rows = []
    for k, v in store.items():
        if k == "__cfg__":
            continue
        m = re.search(r"CuttingSignals(T\d+)/T\d+_(\d+)\.csv", k)
        rows.append({"tool": m.group(1), "cut": int(m.group(2)), **v})
    df = pd.DataFrame(rows)
    hm = os.path.join(root, "MCT/HMoTP 2024-20261012T030348Z-1-001 (1)/HMoTP 2024")
    wear = {t: pd.read_csv(os.path.join(hm, f"ToolWear{t}.csv"))["Mean"].to_numpy() for t in df["tool"].unique()}
    df["wear"] = [wear[t][c - 1] for t, c in zip(df["tool"], df["cut"])]
    feats = [c for c in df.columns if "__" in c]
    S = {}
    for t in sorted(df["tool"].unique()):
        d = df[df["tool"] == t].sort_values("cut").reset_index(drop=True)
        base = d["cut"] <= n_first
        s, _, _ = evaluate(d[feats], ["all"] * len(d), base, ~base, None, None, min_n=5)
        d["score"] = pd.Series(s).rolling(3, min_periods=1).median().to_numpy()   # 3회 이동 중앙값으로 튀는 값 완화
        S[t] = d
    per, parts = {}, []
    for t, d in S.items():
        others = [S[o] for o in S if o != t]
        thr = float(np.median([np.interp(wear_cut, o["wear"].to_numpy(), o["score"].to_numpy()) for o in others]))
        test = (d["cut"] > n_first).to_numpy()
        s = d["score"].to_numpy()
        bad = test & (d["wear"] >= 150).to_numpy()
        ok = test & (d["wear"] < 100).to_numpy()
        r = summarize(s, thr, thr, bad, ok)
        hit = np.where(test & (s >= thr))[0]
        r["first_alarm_wear"] = round(float(d["wear"][hit[0]]), 1) if len(hit) else None
        r["threshold"] = round(thr, 3)
        per[t] = r
        parts.append((s[test], thr, thr, bad[test], ok[test]))
    return {"per_tool": per, "pooled": pooled(parts),
            "rule": f"점수=같은 공구 첫 {n_first}회 대비 이탈(3회 이동 중앙값), 임계값=다른 공구들이 마모 {wear_cut}일 때 점수의 중앙값"}
