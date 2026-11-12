"""MCT 실데이터(전부 양품)로 선행연구 방법들을 같은 조건에서 비교한다.

M0  현재 엔진: 구간 통계(평균·표준편차·최대) → 공구·프로그램 맥락별 정규화 → 희소도 상위 k 평균, 전체 임계값
M1  M0 + 하위 공정별 임계값 (KIT, Production Engineering 2022)
M2  M0 + 위치 셀 기준 비교 (Netzer 등, J. Machine Engineering 2021: 이송축 위치로 가공 구간 분할)
M3  M0 + G코드(G00/G01/G02) 구간 기준 비교 (NC 코드 기반 분할의 대용)
M4  M0 + 엔벨로프 (정상 프로파일의 점별 중앙값·퍼짐, semi-supervised envelope 2020)

평가: 앞 67% 기간 = 기준(양품), 뒤 33% = 평가(양품).
  오경보 = 평가 구간 원래 신호에서 WARN/NG 비율
  검출   = 평가 구간 신호를 일부러 f배 했을 때 WARN/NG 비율
사용: python mct_methods.py <_rows 폴더> <결과 json>
"""
import glob
import json
import os
import sys
import time

import numpy as np
import pandas as pd

CH = ["cur", "load", "vib", "ycur"]
CH_KO = {"cur": "3상 전류", "load": "스핀들 부하", "vib": "진동", "ycur": "Y축 전류"}
INJ = [("cur", 1.05), ("cur", 1.1), ("cur", 1.2), ("cur", 1.5),
       ("vib", 1.1), ("vib", 1.2), ("vib", 1.5), ("vib", 2.0),
       ("load", 1.5), ("load", 2.0), ("ycur", 1.2), ("ycur", 1.5)]
CELL_MM = 5.0
MIN_CELL_TRAIN = 5
NPTS = 32


# ------------------------------------------------------------------ data
def load_rows(folder):
    parts, last = [], None
    for f in sorted(glob.glob(os.path.join(folder, "*.pkl"))):
        d = pd.read_pickle(f).sort_values("t", kind="stable")
        if last is not None:
            d = d[d["t"] > last]          # 주 단위 파일 경계 중복 제거
        last = d["t"].max()
        parts.append(d)
    r = pd.concat(parts, ignore_index=True)
    dt = r["t"].diff().dt.total_seconds()
    new = (r["tool"] != r["tool"].shift()) | (r["pgm"] != r["pgm"].shift()) | (dt > 5) | dt.isna()
    r["seg"] = new.cumsum() - 1
    n = r.groupby("seg").size()
    keep = n[n >= 20].index
    r = r[r["seg"].isin(keep)].copy()
    r["seg"] = r["seg"].map({s: i for i, s in enumerate(keep)})
    return r


# ------------------------------------------------------------------ generic scorer (엔진과 같은 방식)
class Scorer:
    def __init__(self, F, ctx, tr, k=3, shrink=0.5, min_n=10):
        self.k = k
        Ft, ct = F[tr], ctx[tr]
        med_g = Ft.groupby(ct).median()
        mad_g = (Ft - med_g.reindex(ct).to_numpy()).abs().groupby(ct).median() * 1.4826
        cnt = ct.value_counts()
        ok = cnt[cnt >= min_n].index
        self.med_all, self.mad_all = Ft.median(), (Ft - Ft.median()).abs().median() * 1.4826
        rel = (mad_g / med_g.abs().replace(0, np.nan)).loc[ok]
        self.rel_pool = np.nan_to_num(rel.median().to_numpy())
        self.med_g, self.mad_g, self.ok, self.shrink = med_g, mad_g, set(ok), shrink
        z = self._z(F[tr], ctx[tr])
        self.ref = np.sort(z, axis=0)
        self.zmax = np.maximum(self.ref[-1], 1e-9)

    def _z(self, F, ctx):
        known = ctx.isin(self.ok).to_numpy()
        med = np.array(self.med_g.reindex(ctx).to_numpy(), float)
        mad = np.array(self.mad_g.reindex(ctx).to_numpy(), float)
        med[~known], mad[~known] = self.med_all.to_numpy(), self.mad_all.to_numpy()
        pooled = self.shrink * self.rel_pool[None, :] * np.abs(np.nan_to_num(med))
        den = np.sqrt(np.nan_to_num(mad) ** 2 + pooled ** 2)
        den = np.maximum(den, np.maximum(np.abs(self.med_all.to_numpy()) * 0.01, 1e-6)[None, :])
        return np.abs(np.nan_to_num((F.to_numpy(float) - med) / den))

    def score(self, F, ctx):
        z = self._z(F, ctx)
        n = self.ref.shape[0]
        rr = np.empty_like(z)
        for j in range(z.shape[1]):
            ge = n - np.searchsorted(self.ref[:, j], z[:, j], side="left")
            rr[:, j] = -np.log10((ge + 1) / (n + 1))
            over = z[:, j] > self.zmax[j]
            rr[over, j] += np.log10(z[over, j] / self.zmax[j]) * 3
        return np.sort(rr, axis=1)[:, -self.k:].mean(axis=1)


# ------------------------------------------------------------------ representations
def seg_stats(r):
    g = r.groupby("seg")
    F = pd.concat({f"{c}__{s}": getattr(g[c], s)() for c in CH for s in ("mean", "std", "max")}, axis=1)
    meta = pd.DataFrame({"t0": g["t"].min(), "tool": g["tool"].first(), "pgm": g["pgm"].first(),
                         "x0": g["x"].first(), "y0": g["y"].first(), "z0": g["z"].first(), "b0": g["b"].first(),
                         "n": g.size()})
    return F, meta


def scale_stats(F, rows, ch, f):
    F = F.copy()
    cols = [c for c in F.columns if c.startswith(ch + "__")]
    F.iloc[rows, [F.columns.get_loc(c) for c in cols]] *= f
    return F


def cell_table(r, key_cols):
    """(구간, 셀)별 채널 평균. 셀 키 = 공구·프로그램 + key_cols"""
    k = r[["tool", "pgm"]].astype(int).astype(str).agg("|".join, axis=1)
    for c in key_cols:
        k = k + "|" + r[c].astype(str)
    t = r.assign(cell=k).groupby(["seg", "cell"])
    cm = t[CH].mean()
    cm["w"] = t.size()
    return cm.reset_index()


def cell_features(cm, tr_segs, inj=None):
    """셀 기준 대비 상대 편차의 구간별 가중 중앙값 → 채널별 특성 4개 (+ 커버리지)"""
    cm = cm.copy()
    if inj:
        ch, f, segs = inj
        m = cm["seg"].isin(segs)
        cm.loc[m, ch] *= f
    base = cm[cm["seg"].isin(tr_segs)]
    cnt = base.groupby("cell").size()
    good = cnt[cnt >= MIN_CELL_TRAIN].index
    med = base.groupby("cell")[CH].median()
    mad = (base[CH] - med.reindex(base["cell"]).to_numpy()).abs().groupby(base["cell"]).median() * 1.4826
    rel_pool = (mad / med.abs().replace(0, np.nan)).loc[good].median()
    cm = cm[cm["cell"].isin(good)]
    M, D = med.reindex(cm["cell"]).to_numpy(), mad.reindex(cm["cell"]).to_numpy()
    den = np.sqrt(D ** 2 + (0.5 * rel_pool.to_numpy()[None, :] * np.abs(M)) ** 2) + 1e-6
    dev = (cm[CH].to_numpy() - M) / den
    dd = pd.DataFrame(dev, columns=[f"cell_{c}" for c in CH])
    dd["seg"], dd["w"] = cm["seg"].to_numpy(), cm["w"].to_numpy()

    def wmed(g):
        out = {}
        for c in dd.columns[:4]:
            o = np.argsort(g[c].to_numpy())
            cw = np.cumsum(g["w"].to_numpy()[o])
            out[c] = g[c].to_numpy()[o][np.searchsorted(cw, cw[-1] / 2)]
        return pd.Series(out)
    return dd.groupby("seg").apply(wmed, include_groups=False)


def profiles(r):
    """구간별 채널 프로파일을 NPTS점으로 리샘플"""
    out = {}
    for s, g in r.groupby("seg"):
        x = np.linspace(0, 1, len(g))
        xi = np.linspace(0, 1, NPTS)
        out[s] = np.stack([np.interp(xi, x, g[c].to_numpy(float)) for c in CH])
    return out


def envelope_features(P, meta, tr_segs, inj=None):
    key = (meta["tool"].astype(int).astype(str) + "|" + meta["pgm"].astype(int).astype(str) + "|" +
           (meta["x0"] / CELL_MM).round().astype(int).astype(str) + "|" +
           (meta["y0"] / CELL_MM).round().astype(int).astype(str) + "|" +
           (meta["z0"] / CELL_MM).round().astype(int).astype(str))
    segs = meta.index.to_numpy()
    A = np.stack([P[s] for s in segs])                      # (seg, ch, pts)
    if inj:
        ch, f, isegs = inj
        A = A.copy()
        A[np.isin(segs, isegs), CH.index(ch)] *= f
    trm = np.isin(segs, tr_segs)
    feats = np.zeros((len(segs), len(CH)))
    for kk, idx in pd.Series(np.arange(len(segs))).groupby(key.to_numpy()).groups.items():
        idx = np.asarray(idx)
        ti = idx[trm[idx]]
        if len(ti) < MIN_CELL_TRAIN:
            continue
        med = np.median(A[ti], axis=0)
        mad = np.median(np.abs(A[ti] - med), axis=0) * 1.4826
        den = np.sqrt(mad ** 2 + (0.05 * np.abs(med)) ** 2) + 1e-6
        feats[idx] = np.median((A[idx] - med) / den, axis=2)   # 점별 편차의 중앙값 (부호 유지)
    return pd.DataFrame(feats, index=segs, columns=[f"env_{c}" for c in CH])


# ------------------------------------------------------------------ evaluation
def evaluate(name, build_F, ctx, tr, te_rows, test_segs, per_ctx_thr=False):
    F0 = build_F(None)
    sc = Scorer(F0, ctx, tr)
    s_tr, s_all = sc.score(F0[tr], ctx[tr]), sc.score(F0, ctx)
    if per_ctx_thr:   # 하위 공정별 임계값 (기준 30개 이상일 때만, 아니면 전체)
        thr_w = pd.Series(s_tr).groupby(ctx[tr].to_numpy()).quantile(0.95)
        thr_n = pd.Series(s_tr).groupby(ctx[tr].to_numpy()).quantile(0.99)
        cnt = ctx[tr].value_counts()
        gw, gn = np.quantile(s_tr, 0.95), np.quantile(s_tr, 0.99)
        tw = ctx.map(lambda c: thr_w.get(c, gw) if cnt.get(c, 0) >= 30 else gw).to_numpy()
        tn = ctx.map(lambda c: thr_n.get(c, gn) if cnt.get(c, 0) >= 30 else gn).to_numpy()
    else:
        tw = np.full(len(ctx), np.quantile(s_tr, 0.95))
        tn = np.full(len(ctx), np.quantile(s_tr, 0.99))
    te = te_rows
    res = {"method": name, "n_features": int(F0.shape[1]),
           "fa_warn": float((s_all[te] >= tw[te]).mean()), "fa_ng": float((s_all[te] >= tn[te]).mean()), "detect": {}}
    for ch, f in INJ:
        Fi = build_F((ch, f, test_segs))
        s = sc.score(Fi.iloc[te], ctx.iloc[te])
        res["detect"][f"{ch}x{f}"] = {"warn_or_ng": float((s >= tw[te]).mean()), "ng": float((s >= tn[te]).mean())}
    return res


def main(folder, out_json):
    t0 = time.time()
    r = load_rows(folder)
    F_stats, meta = seg_stats(r)
    meta = meta.sort_values("t0")
    order = meta.index.to_numpy()
    F_stats = F_stats.loc[order]
    n = len(order)
    tr = np.zeros(n, bool)
    tr[: int(n * 0.67)] = True
    te_rows = np.where(~tr)[0]
    train_segs, test_segs = order[tr], order[~tr]
    ctx = (meta["tool"].astype(int).astype(str) + "|" + meta["pgm"].astype(int).astype(str))
    ctx = pd.Series(ctx.to_numpy(), index=range(n))
    trm = pd.Series(tr, index=range(n))
    print(f"rows {len(r):,}  segments {n:,}  train {tr.sum():,}  test {(~tr).sum():,}  ({time.time()-t0:.0f}s)")

    def stats_F(inj):
        F = F_stats.reset_index(drop=True)
        if inj:
            F = scale_stats(F, te_rows, inj[0], inj[1])
        return F

    # 위치 셀, G코드 셀
    r["cx"] = (r["x"] / CELL_MM).round().astype("Int64")
    r["cy"] = (r["y"] / CELL_MM).round().astype("Int64")
    r["cz"] = (r["z"] / CELL_MM).round().astype("Int64")
    r["cb"] = (r["b"] / CELL_MM).round().astype("Int64")
    cm_pos = cell_table(r, ["cx", "cy", "cz", "cb"])
    cm_g = cell_table(r, ["g"])
    print(f"cells built ({time.time()-t0:.0f}s)")

    def with_cells(cm):
        def build(inj):
            cf = cell_features(cm, train_segs, inj).reindex(order).fillna(0.0).reset_index(drop=True)
            return pd.concat([stats_F(inj), cf], axis=1)
        return build

    P = profiles(r)
    meta_o = meta.loc[order]
    print(f"profiles built ({time.time()-t0:.0f}s)")

    def with_env(inj):
        ef = envelope_features(P, meta_o, train_segs, inj).reindex(order).reset_index(drop=True)
        return pd.concat([stats_F(inj), ef], axis=1)

    # 커버리지 (기준 셀·엔벨로프가 있는 평가 구간 비율)
    cov_pos = cell_features(cm_pos, train_segs).reindex(test_segs).notna().all(axis=1).mean()
    cov_g = cell_features(cm_g, train_segs).reindex(test_segs).notna().all(axis=1).mean()
    ef0 = envelope_features(P, meta_o, train_segs)
    cov_env = (ef0.loc[test_segs].abs().sum(axis=1) > 0).mean()

    results = [
        evaluate("M0 현재 엔진", stats_F, ctx, trm, te_rows, test_segs),
        evaluate("M1 하위공정별 임계값", stats_F, ctx, trm, te_rows, test_segs, per_ctx_thr=True),
        evaluate("M2 위치 셀 비교", with_cells(cm_pos), ctx, trm, te_rows, test_segs),
        evaluate("M3 G코드 구간 비교", with_cells(cm_g), ctx, trm, te_rows, test_segs),
        evaluate("M4 엔벨로프", with_env, ctx, trm, te_rows, test_segs),
        evaluate("M2+M1 위치 셀 + 하위공정 임계값", with_cells(cm_pos), ctx, trm, te_rows, test_segs, per_ctx_thr=True),
    ]
    summary = {"segments": int(n), "train": int(tr.sum()), "test": int((~tr).sum()),
               "coverage": {"position_cells": float(cov_pos), "gcode_cells": float(cov_g), "envelope": float(cov_env)},
               "results": results, "elapsed_sec": round(time.time() - t0, 1)}
    json.dump(summary, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(summary["coverage"]))
    for x in results:
        d = x["detect"]
        print(f"{x['method']:<28} 오경보 W{x['fa_warn']:.1%} N{x['fa_ng']:.1%} | " +
              " ".join(f"{k}:{v['warn_or_ng']:.0%}/{v['ng']:.0%}" for k, v in d.items()))
    print(f"done {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])


def cell_features_tail(cm, tr_segs, inj=None, q=0.9):
    """위치 셀 편차의 '구간 안 상위 분위수'(기본 90%) — 일부 동작에서만 생긴 변화를 잡기 위한 특성.
    cell_features(가중 중앙값)와 같은 기준·분모를 쓴다."""
    cm = cm.copy()
    if inj:
        ch, f, segs = inj
        cm.loc[cm["seg"].isin(segs), ch] *= f
    base = cm[cm["seg"].isin(tr_segs)]
    cnt = base.groupby("cell").size()
    good = cnt[cnt >= MIN_CELL_TRAIN].index
    med = base.groupby("cell")[CH].median()
    mad = (base[CH] - med.reindex(base["cell"]).to_numpy()).abs().groupby(base["cell"]).median() * 1.4826
    rel_pool = (mad / med.abs().replace(0, np.nan)).loc[good].median()
    cm = cm[cm["cell"].isin(good)]
    M, D = med.reindex(cm["cell"]).to_numpy(), mad.reindex(cm["cell"]).to_numpy()
    den = np.sqrt(D ** 2 + (0.5 * rel_pool.to_numpy()[None, :] * np.abs(M)) ** 2) + 1e-6
    dev = pd.DataFrame((cm[CH].to_numpy() - M) / den, columns=[f"celltail_{c}" for c in CH])
    dev["seg"] = cm["seg"].to_numpy()
    return dev.groupby("seg").quantile(q)
