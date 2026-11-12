"""실험 3: 실제 불량에 가까운 모양의 가짜 변화로 MCT-02 감도를 다시 잰다.

지금까지는 '평가 구간 신호 전체를 똑같이 f배'로 넣어 위치 셀 방법에 유리했을 수 있다.
여기서는 원시 행(row) 단계에서 넣고, 구간 통계·위치 셀을 처음부터 다시 계산한다.

R1 서서히 진행되는 마모 : 공구별 평가 구간 동안 전류가 1.0 → 1.2배로 선형 증가 (끝 20% 구간은 +16~20%)
R2 국부 변화(한 동작만)  : 구간마다 연속된 20% 행에서만 전류 1.5배 (특정 가공 위치의 이상)
R3 진동 버스트(충격)     : 구간마다 무작위 5% 행에서 진동 3배 (채터·칩 걸림)
R4 국부 + 작은 폭        : 구간마다 연속된 20% 행에서만 전류 1.2배

비교: M0(구간 통계만) vs M2(위치 셀 비교, 현재 MCT-02). 오경보는 원래 데이터 기준.
사용: python mct_realistic.py <_rows 폴더> <결과 json>
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mct_methods import load_rows, seg_stats, cell_table, cell_features, cell_features_tail, Scorer, CELL_MM  # noqa: E402

RNG = np.random.default_rng(7)


def add_cells(r):
    r["cur"] = r["cur"].astype("float64")
    r["vib"] = r["vib"].astype("float64")
    for a, c in (("x", "cx"), ("y", "cy"), ("z", "cz"), ("b", "cb")):
        r[c] = (r[a] / CELL_MM).round().astype("Int64")
    return r


def inject(r, test_segs, kind, seg_order):
    r = r.copy()
    m = r["seg"].isin(test_segs).to_numpy()
    if kind == "R1":   # 공구별 선형 증가
        meta = seg_order[seg_order.index.isin(test_segs)]
        prog = {}
        for tool, d in meta.groupby("tool"):
            n = len(d)
            for i, s in enumerate(d.index):
                prog[s] = i / max(1, n - 1)
        f = 1 + 0.2 * r.loc[m, "seg"].map(prog).to_numpy()
        r.loc[m, "cur"] = r.loc[m, "cur"].to_numpy() * f
        return r, {s: 1 + 0.2 * p for s, p in prog.items()}
    if kind in ("R2", "R4"):
        fac = 1.5 if kind == "R2" else 1.2
        pos = r.loc[m].groupby("seg").cumcount().to_numpy()
        n = r.loc[m].groupby("seg")["seg"].transform("size").to_numpy()
        start = r.loc[m, "seg"].map({s: RNG.uniform(0, 0.8) for s in test_segs}).to_numpy()
        frac = pos / n
        hit = (frac >= start) & (frac < start + 0.2)
        cur = r.loc[m, "cur"].to_numpy().copy()
        cur[hit] *= fac
        r.loc[m, "cur"] = cur
        return r, None
    if kind == "R3":
        vib = r.loc[m, "vib"].to_numpy().copy()
        hit = RNG.random(len(vib)) < 0.05
        vib[hit] *= 3
        r.loc[m, "vib"] = vib
        return r, None
    raise ValueError(kind)


def main(folder, out_json):
    t0 = time.time()
    r = add_cells(load_rows(folder))
    F_all, meta = seg_stats(r)
    meta = meta.sort_values("t0")
    order = meta.index.to_numpy()
    n = len(order)
    tr = np.zeros(n, bool)
    tr[: int(n * 0.67)] = True
    train_segs, test_segs = order[tr], order[~tr]
    ctx = pd.Series((meta["tool"].astype(int).astype(str) + "|" + meta["pgm"].astype(int).astype(str)).to_numpy())
    trm = pd.Series(tr)
    te = np.where(~tr)[0]

    cm_train = cell_table(r[r["seg"].isin(train_segs)], ["cx", "cy", "cz", "cb"])
    cm_test0 = cell_table(r[r["seg"].isin(test_segs)], ["cx", "cy", "cz", "cb"])
    print(f"prepared {time.time()-t0:.0f}s", flush=True)

    def feats(F_stats, cm_test, with_cells):
        F = F_stats.loc[order].reset_index(drop=True)
        if not with_cells:
            return F
        cm = pd.concat([cm_train, cm_test], ignore_index=True)
        cf = cell_features(cm, train_segs).reindex(order).fillna(0.0).reset_index(drop=True)
        out = [F, cf]
        if with_cells == "tail":
            ct = cell_features_tail(cm, train_segs).reindex(order).fillna(0.0).reset_index(drop=True)
            out.append(ct)
        return pd.concat(out, axis=1)

    res = {}
    base = {}
    for name, wc in (("M0", False), ("M2", True), ("M2T", "tail")):
        F0 = feats(F_all, cm_test0, wc)
        sc = Scorer(F0, ctx, trm)
        s_tr = sc.score(F0[tr], ctx[tr])
        tw, tn = np.quantile(s_tr, 0.95), np.quantile(s_tr, 0.99)
        s0 = sc.score(F0, ctx)
        base[name] = (sc, tw, tn)
        res[name] = {"false_alarm_warn_or_ng": round(float((s0[te] >= tw).mean()), 3),
                     "false_alarm_ng": round(float((s0[te] >= tn).mean()), 3)}
    for kind in ("R1", "R2", "R3", "R4"):
        ri, prog = inject(r, set(test_segs), kind, meta)
        Fi, _ = seg_stats(ri[ri["seg"].isin(test_segs)])
        Fi = pd.concat([F_all.drop(index=test_segs), Fi])
        cmi = cell_table(ri[ri["seg"].isin(test_segs)], ["cx", "cy", "cz", "cb"])
        for name, wc in (("M0", False), ("M2", True), ("M2T", "tail")):
            sc, tw, tn = base[name]
            F = feats(Fi, cmi, wc)
            s = sc.score(F.iloc[te], ctx.iloc[te])
            d = {"warn_or_ng": round(float((s >= tw).mean()), 3), "ng": round(float((s >= tn).mean()), 3)}
            if kind == "R1":   # 진행 단계별 검출
                fac = np.array([prog.get(sg, 1.0) for sg in test_segs])
                for lo, hi in ((1.0, 1.05), (1.05, 1.1), (1.1, 1.15), (1.15, 1.21)):
                    mm = (fac >= lo) & (fac < hi)
                    d[f"x{lo:.2f}-{hi:.2f}"] = round(float((s[mm] >= tw).mean()), 3)
            res[name][kind] = d
        print(kind, {k: res[k][kind] for k in ("M0", "M2", "M2T")}, f"{time.time()-t0:.0f}s", flush=True)
    json.dump(res, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
