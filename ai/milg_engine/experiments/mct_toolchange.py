"""실험 1: 실데이터에서 공구 교체 시점 추정 (가짜 라벨).

가정: 같은 공구번호의 공구를 새것으로 바꾸면, 같은 동작(같은 위치 셀)에서의 전류가 계단처럼 떨어진다.
1) 구간마다 '같은 공구·프로그램·위치 셀의 전체 기간 중앙값' 대비 상대 편차(가중 중앙값)를 구한다 → 동작 차이를 지운 수준
2) 공구별로 시간순 수준에서, 앞 W구간 중앙값 대비 뒤 W구간 중앙값이 크게 떨어지는 지점을 변화점으로 잡는다
3) 그럴듯한지 확인: 변화점 직전에 설비가 오래 멈췄는가 / 진동도 같이 떨어졌는가 / 변화점 전 전류가 오르고 있었는가
   → 아무 지점이나 뽑았을 때(무작위 기준)와 비교
사용: python mct_toolchange.py <_rows 폴더> <결과 json>
"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from mct_methods import load_rows, cell_table, CELL_MM  # noqa: E402

W = 12            # 앞·뒤 비교 창 (구간 수)
MIN_SEGS = 60     # 이 정도는 써야 분석
K_SIGMA = 4.0     # 잡음 대비 몇 배 떨어져야 변화점
IDLE_MIN = 10     # '오래 멈춤' 기준 (분)


def seg_levels(r):
    r["cx"] = (r["x"] / CELL_MM).round().astype("Int64")
    r["cy"] = (r["y"] / CELL_MM).round().astype("Int64")
    r["cz"] = (r["z"] / CELL_MM).round().astype("Int64")
    r["cb"] = (r["b"] / CELL_MM).round().astype("Int64")
    cm = cell_table(r, ["cx", "cy", "cz", "cb"])
    cnt = cm.groupby("cell").size()
    cm = cm[cm["cell"].isin(cnt[cnt >= 5].index)]
    med = cm.groupby("cell")[["cur", "vib"]].transform("median")
    for c in ("cur", "vib"):
        cm[c + "_rel"] = cm[c] / med[c].replace(0, np.nan) - 1
    out = {}
    for c in ("cur_rel", "vib_rel"):
        t = cm[["seg", c, "w"]].dropna().sort_values(["seg", c])
        t["cw"] = t.groupby("seg")["w"].cumsum()
        t["tot"] = t.groupby("seg")["w"].transform("sum")
        out[c] = t[t["cw"] >= t["tot"] / 2].groupby("seg")[c].first()
    return pd.DataFrame(out)


def main(folder, out_json):
    r = load_rows(folder)
    g = r.groupby("seg")
    meta = pd.DataFrame({"t0": g["t"].min(), "t1": g["t"].max(), "tool": g["tool"].first(), "n": g.size()}).sort_values("t0")
    # 설비 전체 기준 직전 정지 시간 (앞 구간 끝 ~ 이 구간 시작)
    meta["idle_min"] = (meta["t0"] - meta["t1"].shift()).dt.total_seconds() / 60
    L = seg_levels(r)
    meta = meta.join(L, how="left")
    print(f"segments {len(meta):,}, level coverage {meta['cur_rel'].notna().mean():.1%}")

    events, all_pos = [], []
    for tool, d in meta.groupby("tool"):
        d = d.dropna(subset=["cur_rel"])
        if len(d) < MIN_SEGS:
            continue
        x = d["cur_rel"].to_numpy()
        v = d["vib_rel"].to_numpy()
        noise = np.median(np.abs(np.diff(x))) * 1.4826 / np.sqrt(2) + 1e-6
        se = noise * np.sqrt(np.pi / 2) * np.sqrt(2 / W)     # 두 창 중앙값 차이의 대략적 표준오차
        drops = np.full(len(x), np.nan)
        for i in range(W, len(x) - W):
            drops[i] = np.median(x[i:i + W]) - np.median(x[i - W:i])
        cand = [i for i in range(W, len(x) - W) if drops[i] <= -K_SIGMA * se]
        picked = []
        for i in sorted(cand, key=lambda j: drops[j]):          # 가장 큰 하락부터, W 안쪽 중복 제거
            if all(abs(i - p) >= W for p in picked):
                picked.append(i)
        idx = d.index.to_numpy()
        for i in range(W, len(x) - W):
            all_pos.append({"tool": tool, "pos": i, "is_cp": i in picked,
                            "idle_before": float(meta.loc[idx[i], "idle_min"]) if pd.notna(meta.loc[idx[i], "idle_min"]) else 0.0,
                            "vib_drop": float(np.median(v[i:i + W]) - np.median(v[i - W:i])) if np.isfinite(v[i - W:i + W]).all() else np.nan,
                            "pre_slope": float(np.polyfit(np.arange(W), x[i - W:i], 1)[0]),
                            "drop": float(drops[i]), "se": float(se)})
        for i in sorted(picked):
            events.append({"tool": int(tool), "t": str(meta.loc[idx[i], "t0"]), "drop_pct": round(float(drops[i]) * 100, 1),
                           "z": round(float(drops[i] / se), 1), "idle_before_min": round(float(meta.loc[idx[i], "idle_min"] or 0), 1),
                           "segs_since_prev_cp": None})
    A = pd.DataFrame(all_pos)
    cp, rnd = A[A["is_cp"]], A[~A["is_cp"]]

    def rate(df):
        return {"n": int(len(df)),
                "long_idle_before": round(float((df["idle_before"] >= IDLE_MIN).mean()), 3),
                "vib_also_drops": round(float((df["vib_drop"] < 0).mean()), 3),
                "current_rising_before": round(float((df["pre_slope"] > 0).mean()), 3)}
    summary = {"tools_analyzed": int(A["tool"].nunique()), "change_points": int(len(cp)),
               "per_tool": pd.Series([e["tool"] for e in events]).value_counts().to_dict(),
               "change_points_stats": rate(cp), "random_positions_stats": rate(rnd),
               "events": events, "params": {"W": W, "K_SIGMA": K_SIGMA, "IDLE_MIN": IDLE_MIN}}
    json.dump(summary, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    print(json.dumps({k: v for k, v in summary.items() if k != "events"}, ensure_ascii=False, default=str))
    for e in events[:40]:
        print(e)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
