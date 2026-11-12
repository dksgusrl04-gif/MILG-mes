"""MILG Engine — 공정에 상관없이 하나로 돌아가는 엔진.

공정마다 달라지는 것은 scenarios/*.yaml(시나리오 템플릿)뿐이고,
이 파일은 템플릿을 읽어 아래 순서를 똑같이 수행한다.

    1. unit     판정 단위 만들기 (행 / 키별 집계 / 파일 1개)
    2. label    정답 붙이기 (컬럼 / 경로 / 라벨표 조인 / 라벨표 행 번호)
    3. features 특성 고르기 (숫자 컬럼 자동 + 제외 목록)
    4. split    평가 분할 (random / group / time)
    5. model    후보 모델 교차검증 → 최고 모델 재학습
    6. decision 판정 규칙 (재현율 목표 임계값 / 관리 한계)
    7. output   MES로 넘길 공통 스키마 + 리포트

사용법:
    python engine.py scenarios/inj_cycle_passfail.yaml
    python engine.py scenarios/*.yaml --max-seconds 150   # 파일형 단위는 캐시로 이어서 처리
"""
from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import re
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
import yaml
from sklearn.base import clone
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import (HistGradientBoostingClassifier, HistGradientBoostingRegressor,
                              RandomForestClassifier, RandomForestRegressor)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (accuracy_score, average_precision_score, confusion_matrix, f1_score,
                             mean_absolute_error, mean_squared_error, precision_score, r2_score,
                             recall_score, roc_auc_score)
from sklearn.model_selection import GroupKFold, StratifiedKFold, KFold, TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ENGINE_VERSION = "0.4.0"    # 0.3.0: MILG 판정 엔진(mlng_ai)을 모델 후보 «milg» 로 연결 · 후보별 평가 구간 비교 · 원인 후보 컬럼
ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ENGINE_DIR))
sys.path.insert(0, ENGINE_DIR)
import schema  # noqa: E402  시나리오 YAML 스키마 검증
import adapters  # noqa: E402  장비별 파일 읽기 플러그인

try:                # MILG 판정 엔진 (같은 저장소의 mlng_ai). 없으면 후보에서 빠진다
    from milg_engine.milg_core import MilgEngineClassifier, MilgNormalScorer  # noqa: E402
except Exception as _e:      # noqa: BLE001
    MilgEngineClassifier, MilgNormalScorer, _MILG_ERR = None, None, f"{type(_e).__name__}: {_e}"


class ConfigError(Exception):
    """시나리오 설정 오류. 실행 전에 모두 모아서 알려준다."""

    def __init__(self, path, errors, warnings=()):
        self.path, self.errors, self.warnings = path, list(errors), list(warnings)
        super().__init__(f"{len(self.errors)}개 설정 오류")


def load_scenario(path):
    """YAML을 읽고 스키마를 검증한다. 오류가 있으면 ConfigError, 경고는 돌려준다."""
    try:
        cfg = yaml.safe_load(open(path, encoding="utf-8"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"{mark.line + 1}번째 줄 {mark.column + 1}번째 칸" if mark else "위치 모름"
        raise ConfigError(path, [f"(YAML 문법) {where}: {getattr(e, 'problem', e)} — 들여쓰기·콜론·따옴표를 확인하세요"])
    P = schema.validate_config(cfg)
    if P.errors:
        raise ConfigError(path, P.errors, P.warnings)
    return cfg, P.warnings


class Incomplete(Exception):
    """시간 예산 안에 특성 추출을 다 못 끝냄 — 다시 실행하면 캐시에서 이어감."""


# ---------------------------------------------------------------- io helpers
def read_any(path, **kw):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path, **{k: v for k, v in kw.items() if k in ("sheet_name", "usecols")})
    enc = kw.pop("encoding", "auto")
    encs = ["utf-8-sig", "cp949"] if enc == "auto" else [enc]
    last = None
    for e in encs:
        try:
            return pd.read_csv(path, encoding=e, low_memory=False, **kw)
        except UnicodeDecodeError as err:
            last = err
    raise last


def csv_kwargs(spec):
    kw = {}
    for k in ("sep", "encoding", "header", "names", "usecols", "skiprows"):
        if k in spec:
            kw[k] = spec[k]
    return kw


def resolve(base, p):
    return p if os.path.isabs(p) else os.path.join(base, p)


# ---------------------------------------------------------------- 1. unit
STAT_FUNCS = {
    "mean": lambda a: np.nanmean(a, axis=0),
    "std": lambda a: np.nanstd(a, axis=0),
    "min": lambda a: np.nanmin(a, axis=0),
    "max": lambda a: np.nanmax(a, axis=0),
    "p2p": lambda a: np.nanmax(a, axis=0) - np.nanmin(a, axis=0),
    "rms": lambda a: np.sqrt(np.nanmean(a ** 2, axis=0)),
    "last": lambda a: a[-1],
    "first": lambda a: a[0],
    "q05": lambda a: np.nanquantile(a, 0.05, axis=0),
    "q95": lambda a: np.nanquantile(a, 0.95, axis=0),
    "kurt": lambda a: pd.DataFrame(a).kurt().to_numpy(),
    "skew": lambda a: pd.DataFrame(a).skew().to_numpy(),
    "len": None,  # 길이(샘플 수) 1개 값
}


def signal_features(sig: pd.DataFrame, stats, segments=1):
    """시계열 1개(행=시간, 열=채널) → 특성 1행. segments>1이면 구간별로도 뽑는다."""
    sig = sig.apply(pd.to_numeric, errors="coerce")
    out = {"len": float(len(sig))} if "len" in stats else {}
    parts = [("", sig.to_numpy(dtype=float))]
    if segments > 1:
        parts += [(f"s{i+1}_", c) for i, c in enumerate(np.array_split(sig.to_numpy(dtype=float), segments))]
    for prefix, arr in parts:
        if len(arr) == 0:
            continue
        for s in stats:
            if s == "len":
                continue
            vals = STAT_FUNCS[s](arr)
            for c, v in zip(sig.columns, vals):
                out[f"{prefix}{c}__{s}"] = float(v)
    return out


def collapse_rows(df, col):
    """여러 행 → 판정 단위 1개로 합치기.
    예: 2캐비티 금형은 한 샷에 LH·RH 두 부품이 나와 공정값이 같은 행이 여러 번 기록된다.
    dedupe로 완전 중복 행을 지우고, key(예: TimeStamp)별로 첫 값을 쓰되,
    any에 적은 컬럼은 묶음 안에 해당 값이 하나라도 있으면 그 값으로 둔다(예: 한쪽이라도 불량이면 불량 샷)."""
    n0 = len(df)
    if col.get("dedupe"):
        df = df.drop_duplicates(subset=col["dedupe"])
    n1 = len(df)
    key = col["key"] if isinstance(col["key"], list) else [col["key"]]
    g = df.groupby(key, sort=False, dropna=False)
    out = g.first().reset_index()
    for c, vals in (col.get("any") or {}).items():
        vals = [str(v) for v in vals]
        flag = g[c].agg(lambda x: x.astype(str).isin(vals).any()).reset_index(drop=True)
        out[c] = np.where(flag.to_numpy(), vals[0], out[c].astype(str))
    out["rows_merged"] = g.size().to_numpy()
    out.attrs["collapse"] = {"rows_in": int(n0), "after_dedupe": int(n1), "units": int(len(out))}
    return out


def wide_series_features(df, groups, stats, segments=1, trim_zeros=True):
    """한 행에 시계열 하나가 가로로 펼쳐진 표(예: 'Current T-0 … T-999') → 행마다 통계 특성.
    trim_zeros면 뒤쪽 0 채움(padding)을 실제 길이 밖으로 보고 뺀다."""
    out = {}
    for name, pat in groups.items():
        cols = [c for c in df.columns if fnmatch.fnmatch(str(c), pat)]
        if not cols:
            continue
        cols = sorted(cols, key=lambda c: int(re.findall(r"(\d+)$", str(c))[0]) if re.findall(r"(\d+)$", str(c)) else 0)
        A = df[cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        if trim_zeros:
            nz = A != 0
            last = np.where(nz.any(1), A.shape[1] - np.argmax(nz[:, ::-1], axis=1), 0)
            A = np.where(np.arange(A.shape[1])[None, :] < last[:, None], A, np.nan)
        L = np.sum(~np.isnan(A), axis=1)
        out[f"{name}__len"] = L.astype(float)
        parts = [("", A)]
        if segments > 1:   # 실제 길이 기준 앞·중간·뒤 구간
            for si in range(segments):
                B = np.full_like(A, np.nan)
                for r in range(A.shape[0]):
                    lo, hi = int(L[r] * si / segments), int(L[r] * (si + 1) / segments)
                    B[r, lo:hi] = A[r, lo:hi]
                parts.append((f"s{si+1}_", B))
        with np.errstate(all="ignore"):
            for pre, M in parts:
                calc = {"mean": lambda m: np.nanmean(m, 1), "std": lambda m: np.nanstd(m, 1),
                        "min": lambda m: np.nanmin(m, 1), "max": lambda m: np.nanmax(m, 1),
                        "p2p": lambda m: np.nanmax(m, 1) - np.nanmin(m, 1),
                        "rms": lambda m: np.sqrt(np.nanmean(m ** 2, 1)),
                        "q95": lambda m: np.nanquantile(m, 0.95, axis=1), "q05": lambda m: np.nanquantile(m, 0.05, axis=1),
                        "auc": lambda m: np.nansum(m, 1)}
                for st_ in stats:
                    if st_ in calc:
                        out[f"{pre}{name}__{st_}"] = calc[st_](M)
    feats = pd.DataFrame(out, index=df.index)
    wide_cols = [c for c in df.columns if any(fnmatch.fnmatch(str(c), p) for p in groups.values())]
    return pd.concat([df.drop(columns=wide_cols), feats], axis=1)


def unit_table(u, base):
    df = read_any(resolve(base, u["path"]), **csv_kwargs(u))
    for m in u.get("merge") or []:   # 같은 키로 나뉘어 저장된 파일 합치기 (예: 전류·전압·가압력 파일)
        other = read_any(resolve(base, m["path"]), **csv_kwargs(m))
        on = m["keys"] if isinstance(m["keys"], list) else [m["keys"]]
        # 같은 키가 여러 번 나오면(같은 차체·위치·날짜에 두 번 용접 등) n번째끼리 짝지어 행이 불어나지 않게 한다
        df = df.assign(__occ=df.groupby(on).cumcount())
        other = other.assign(__occ=other.groupby(on).cumcount())
        n0 = len(df)
        df = df.merge(other, on=on + ["__occ"], how="inner", suffixes=("", m.get("suffix", "_m"))).drop(columns="__occ")
        if len(df) != n0:
            print(f"  [merge] {os.path.basename(m['path'])}: {n0}행 → {len(df)}행 (키가 안 맞는 행 제외)")
    if u.get("wide_series"):
        ws = u["wide_series"]
        df = wide_series_features(df, ws["groups"], ws.get("stats", ["mean", "std", "max", "min", "rms"]),
                                  int(ws.get("segments", 1)), bool(ws.get("trim_zeros", True)))
    if u.get("collapse"):
        df = apply_filters(df, u.get("pre_filters"))
        df = collapse_rows(df, u["collapse"])
        df["unit_id"] = df[u["collapse"]["key"] if isinstance(u["collapse"]["key"], str) else u["collapse"]["key"][0]].astype(str)
        return df
    df["unit_id"] = df[u["id"]].astype(str) if u.get("id") else df.index.astype(str)
    return df


def unit_grouped_table(u, base, cache_dir, sid, deadline):
    """초 단위 같은 원천 데이터를 키(예: 배정번호)별로 집계. 큰 파일은 청크로 처리하고,
    시간 예산을 넘기면 중간 집계를 저장했다가 다음 실행에서 이어간다."""
    path = resolve(base, u["path"])
    st = os.stat(path)
    cache = os.path.join(cache_dir, f"{sid}__grouped.pkl")
    part = os.path.join(cache_dir, f"{sid}__grouped_partial.pkl")
    sig = f"{path}|{st.st_size}|{int(st.st_mtime)}|{json.dumps(u, sort_keys=True, ensure_ascii=False)}"
    if os.path.exists(cache):
        obj = pd.read_pickle(cache)
        if obj.get("sig") == sig:
            return obj["df"]
    key = u["key"]
    stats = u.get("stats", ["mean", "std", "min", "max"])
    csize = u.get("chunksize", 200_000)
    acc, done = None, 0
    if os.path.exists(part):
        obj = pd.read_pickle(part)
        if obj.get("sig") == sig:
            acc, done = obj["acc"], obj["done"]
    reader = None
    for enc in (["utf-8-sig", "cp949"] if u.get("encoding", "auto") == "auto" else [u["encoding"]]):
        try:
            skip = range(1, done * csize + 1) if done else None
            reader = pd.read_csv(path, encoding=enc, chunksize=csize, low_memory=False, skiprows=skip)
            first = next(reader, None)
            break
        except UnicodeDecodeError:
            reader = None
    if reader is None:
        raise RuntimeError(f"인코딩을 읽지 못함: {path}")
    drop = set(u.get("drop_columns", []))

    def agg(chunk):
        num = chunk.drop(columns=[c for c in chunk.columns if c in drop or c == key])
        num = num.apply(pd.to_numeric, errors="coerce")
        g = num.groupby(chunk[key])
        parts = {"n": g.count(), "s": g.sum(), "ss": (num ** 2).groupby(chunk[key]).sum(),
                 "mn": g.min(), "mx": g.max()}
        return parts

    def merge(a, b):
        if a is None:
            return b
        return {"n": a["n"].add(b["n"], fill_value=0), "s": a["s"].add(b["s"], fill_value=0),
                "ss": a["ss"].add(b["ss"], fill_value=0),
                "mn": pd.concat([a["mn"], b["mn"]]).groupby(level=0).min(),
                "mx": pd.concat([a["mx"], b["mx"]]).groupby(level=0).max()}

    chunks = [] if first is None else [first]
    import itertools
    for ch in itertools.chain(chunks, reader):
        acc = merge(acc, agg(ch))
        done += 1
        if time.time() > deadline:
            pd.to_pickle({"sig": sig, "acc": acc, "done": done}, part)
            raise Incomplete(f"청크 {done}개({done * csize:,}행)까지 집계. 같은 명령을 다시 실행하면 이어서 처리합니다.")
    n = acc["n"].replace(0, np.nan)
    mean = acc["s"] / n
    var = (acc["ss"] / n - mean ** 2).clip(lower=0)
    pieces = {"mean": mean, "std": np.sqrt(var), "min": acc["mn"], "max": acc["mx"]}
    cols = []
    for s in stats:
        p = pieces[s].copy()
        p.columns = [f"{c}__{s}" for c in p.columns]
        cols.append(p)
    out = pd.concat(cols, axis=1)
    out["n_samples"] = acc["n"].max(axis=1)
    out.index.name = key
    out = out.reset_index()
    out["unit_id"] = out[key].astype(str)
    pd.to_pickle({"sig": sig, "df": out}, cache)
    return out


def unit_files(u, base, cache_dir, sid, deadline):
    """파일 1개 = 판정 단위 1개 (사이클·삽입·절삭 1회). 경로 정규식의 이름 그룹이 메타 컬럼이 된다."""
    root = resolve(base, u["root"])
    pattern = u.get("glob", "**/*.csv")
    files = sorted(glob.glob(os.path.join(root, pattern), recursive=True))
    rx = re.compile(u["path_regex"]) if u.get("path_regex") else None
    excl = u.get("exclude_glob", [])
    stats = u.get("stats", ["mean", "std", "min", "max", "rms"])
    segments = int(u.get("segments", 1))
    kw = csv_kwargs(u)
    cache = os.path.join(cache_dir, f"{sid}__files.pkl")
    store = pd.read_pickle(cache) if os.path.exists(cache) else {}
    cfg_sig = json.dumps({k: u.get(k) for k in ("stats", "segments", "names", "header", "usecols", "sep",
                                                "reader", "segment_column", "channels")}, sort_keys=True)
    if store.get("__cfg__") != cfg_sig:
        store = {"__cfg__": cfg_sig}
    rows, todo, fresh = [], 0, 0
    for f in files:
        rel = os.path.relpath(f, root).replace("\\", "/")
        if any(fnmatch.fnmatch(rel, e) for e in excl):
            continue
        meta = {}
        if rx:
            m = rx.search(rel)
            if not m:
                continue
            meta = m.groupdict()
        st = os.stat(f)
        k = f"{rel}|{st.st_size}|{int(st.st_mtime)}"
        if k not in store:
            if time.time() > deadline:
                todo += 1
                continue
            sig = adapters.read(u["reader"], f) if u.get("reader") else read_any(f, **kw)
            segc = u.get("segment_column")
            chans = u.get("channels") or [c for c in sig.columns if c != segc]
            if segc:
                # 단계별 특성: 체결의 '찾기·나사 성형·예비 체결·본 체결'처럼 의미 있는 단계마다 따로 요약
                feats = signal_features(sig[chans], stats, segments)
                for val, part in sig.groupby(segc):
                    feats.update({f"{segc}{val}_{k2}": v2 for k2, v2 in signal_features(part[chans], stats, 1).items()})
                store[k] = feats
            else:
                store[k] = signal_features(sig[chans], stats, segments)
            fresh += 1
            if fresh % 300 == 0:      # 중간 저장: 실행이 끊겨도 다음 실행에서 이어감
                pd.to_pickle(store, cache)
        rows.append({"unit_id": rel, "_path": rel, **meta, **store[k]})
    pd.to_pickle(store, cache)
    if todo:
        raise Incomplete(f"{todo}개 파일 남음 (완료 {len(rows)}). 같은 명령을 다시 실행하면 이어서 처리합니다.")
    if not rows:
        raise RuntimeError(f"파일을 찾지 못함: {root}/{pattern}")
    return pd.DataFrame(rows)


def _segment_file(path, u, cache_dir, sid, deadline):
    """설비 로그 1개 → 연속 구간별 집계. 청크 단위로 이어서 처리할 수 있다."""
    st = os.stat(path)
    tag = re.sub(r"[^0-9A-Za-z_-]", "_", os.path.basename(path))
    done_p = os.path.join(cache_dir, f"{sid}__seg__{tag}.pkl")
    part_p = os.path.join(cache_dir, f"{sid}__seg__{tag}__partial.pkl")
    sig = f"{st.st_size}|{int(st.st_mtime)}|{json.dumps(u, sort_keys=True, ensure_ascii=False)}"
    if os.path.exists(done_p):
        o = pd.read_pickle(done_p)
        if o.get("sig") == sig:
            return o["df"], o.get("cells")
    keys, tcol, ch_cols = list(u["segment_by"]), u["time"], list(u["channels"])
    cl = u.get("cells")                         # 위치 셀: 구간 안을 이송축 위치 격자로 나눠 셀별 평균 저장
    flt = u.get("row_filter")
    gap = float(u.get("max_gap_sec", 5))
    csize = int(u.get("chunksize", 200_000))
    state = {"done": 0, "parts": [], "carry": None, "next_id": 0, "cells": []}
    if os.path.exists(part_p):
        o = pd.read_pickle(part_p)
        if o.get("sig") == sig:
            state = o["state"]
    use = list(dict.fromkeys(keys + [tcol] + ch_cols + list(u.get("first_columns", [])) +
                             (list(cl["columns"]) + list(cl.get("channels", ch_cols)) if cl else []) +
                             ([flt["column"]] if flt else [])))
    skip = range(1, state["done"] * csize + 1) if state["done"] else None
    reader = pd.read_csv(path, usecols=use, chunksize=csize, encoding=u.get("encoding", "utf-8-sig"),
                         low_memory=False, skiprows=skip)
    for ch in reader:
        state["done"] += 1
        if flt:
            ch = ch[ch[flt["column"]].astype(str).isin([str(v) for v in flt["in"]])]
        if len(ch):
            t = pd.to_datetime(ch[tcol], errors="coerce")
            kv = ch[keys].astype(str).agg("|".join, axis=1)
            prev_k = kv.shift()
            prev_t = t.shift()
            if state["carry"] is not None:
                prev_k.iloc[0], prev_t.iloc[0] = state["carry"]
            new = (kv != prev_k) | ((t - prev_t).dt.total_seconds() > gap) | prev_t.isna()
            if state["carry"] is not None and not new.iloc[0]:
                base_id = state["next_id"] - 1      # 앞 청크의 마지막 구간을 이어 붙임
                sid_ = base_id + new.cumsum()
            else:
                sid_ = state["next_id"] - 1 + new.cumsum()
            state["next_id"] = int(sid_.iloc[-1]) + 1
            state["carry"] = (kv.iloc[-1], t.iloc[-1])
            x = ch[ch_cols].apply(pd.to_numeric, errors="coerce")
            g = x.groupby(sid_.to_numpy())
            p = pd.concat({"n": g.count(), "s": g.sum(), "ss": (x ** 2).groupby(sid_.to_numpy()).sum(),
                           "mn": g.min(), "mx": g.max()}, axis=1)
            meta = pd.DataFrame({"t0": t.groupby(sid_.to_numpy()).min(), "t1": t.groupby(sid_.to_numpy()).max(),
                                 "rows": g.size()})
            for k in keys + list(u.get("first_columns", [])):
                meta[k] = ch[k].groupby(sid_.to_numpy()).first()
            state["parts"].append((p, meta))
            if cl:
                step = float(cl.get("step", 5))
                ck = None
                for c in cl["columns"]:
                    q = (pd.to_numeric(ch[c], errors="coerce") / step).round().astype("Int64").astype(str)
                    ck = q if ck is None else ck + "_" + q
                cc = list(cl.get("channels", ch_cols))
                xv = ch[cc].apply(pd.to_numeric, errors="coerce")
                gg = xv.groupby([sid_.to_numpy(), ck.to_numpy()])
                cs = gg.sum()
                cs["_w"] = gg.size()
                state["cells"].append(cs)
        if time.time() > deadline:
            pd.to_pickle({"sig": sig, "state": state}, part_p)
            raise Incomplete(f"{os.path.basename(path)}: 청크 {state['done']}개까지 처리. 다시 실행하면 이어서 처리합니다.")
    if not state["parts"]:
        out = pd.DataFrame()
    else:
        P = pd.concat([p for p, _ in state["parts"]])
        M = pd.concat([m for _, m in state["parts"]])
        P = pd.concat({"n": P["n"].groupby(level=0).sum(), "s": P["s"].groupby(level=0).sum(),
                       "ss": P["ss"].groupby(level=0).sum(), "mn": P["mn"].groupby(level=0).min(),
                       "mx": P["mx"].groupby(level=0).max()}, axis=1)
        M = M.groupby(level=0).agg({"t0": "min", "t1": "max", "rows": "sum",
                                    **{k: "first" for k in keys + list(u.get("first_columns", []))}})
        n = P["n"].replace(0, np.nan)
        mean = P["s"] / n
        std = np.sqrt((P["ss"] / n - mean ** 2).clip(lower=0))
        pieces = {"mean": mean, "std": std, "min": P["mn"], "max": P["mx"], "p2p": P["mx"] - P["mn"]}
        cols = []
        for s in u.get("stats", ["mean", "std", "max"]):
            q = pieces[s].copy()
            q.columns = [f"{c}__{s}" for c in q.columns]
            cols.append(q)
        out = pd.concat([M] + cols, axis=1)
        out["duration_sec"] = (out["t1"] - out["t0"]).dt.total_seconds()
        out = out[out["rows"] >= int(u.get("min_rows", 20))]
        out["source_file"] = os.path.basename(path)
        out["_seg_local"] = out.index
    cells = None
    if cl and state["cells"]:
        C = pd.concat(state["cells"]).groupby(level=[0, 1]).sum()
        w = C.pop("_w")
        C = C.div(w, axis=0)
        C["_w"] = w
        C.index.names = ["_seg_local", "cell"]
        C = C.reset_index()
        C = C[C["_seg_local"].isin(out["_seg_local"])] if len(out) else C.iloc[:0]
        C["source_file"] = os.path.basename(path)
        cells = C
    pd.to_pickle({"sig": sig, "df": out, "cells": cells}, done_p)
    return out, cells


_SIDE_TABLES = {}   # 단위 표 옆에 따라다니는 보조 표(위치 셀 등). attrs에 DataFrame을 넣으면 concat이 깨져서 따로 둔다.


def unit_segments(u, base, cache_dir, sid, deadline):
    """여러 설비 로그 파일 → '같은 키(예: 공구번호)로 끊김 없이 이어진 구간' 1개 = 단위 1개."""
    files = sorted(glob.glob(resolve(base, u["paths"])))
    if not files:
        raise RuntimeError(f"파일을 찾지 못함: {u['paths']}")
    res = [_segment_file(f, u, cache_dir, sid, deadline) for f in files]
    dfs = [r[0] if isinstance(r, tuple) else r for r in res]
    cls = [r[1] for r in res if isinstance(r, tuple) and r[1] is not None]
    df = pd.concat([d for d in dfs if len(d)], ignore_index=True).sort_values("t0")
    # 주 단위 파일이 경계일에 겹치면 같은 구간이 두 번 잡힌다 → 키+시작시각이 같으면 하나만 남김
    df = df.drop_duplicates(subset=list(u["segment_by"]) + ["t0"], keep="first").reset_index(drop=True)
    df["unit_id"] = df["source_file"].str.replace(".csv", "", regex=False) + "#" + df.index.astype(str)
    df["t0"] = df["t0"].astype(str)
    df["t1"] = df["t1"].astype(str)
    if cls:
        C = pd.concat(cls, ignore_index=True)
        C = C.merge(df[["source_file", "_seg_local", "unit_id"]], on=["source_file", "_seg_local"], how="inner")
        _SIDE_TABLES[sid] = C.drop(columns=["source_file", "_seg_local"])
    return df


def unit_windows(u, base):
    """끊김 없이 흐르는 센서 스트림(예: 10Hz 진동·전류) → 고정 길이 창(window) 1개 = 단위 1개.
    사이클 표시가 없는 연속 데이터에 쓴다. 파일 이름은 source_file 컬럼으로 남는다(라벨이 파일로 나뉜 경우)."""
    files = sorted(glob.glob(resolve(base, u["paths"])))
    if not files:
        raise RuntimeError(f"파일을 찾지 못함: {u['paths']}")
    n = int(u["rows"])
    step = int(u.get("step_rows", n))
    stats = u.get("stats", ["mean", "std", "min", "max", "rms", "p2p"])
    rows = []
    for f in files:
        d = read_any(f, **csv_kwargs(u))
        chans = u.get("channels") or [c for c in d.select_dtypes("number").columns]
        tcol = u.get("time")
        for i, start in enumerate(range(0, len(d) - n + 1, step)):
            w = d.iloc[start:start + n]
            r = {"unit_id": f"{os.path.basename(f)}#{i}", "source_file": os.path.basename(f), "window_index": i}
            if tcol:
                r["t0"] = str(w[tcol].iloc[0])
            for c in u.get("first_columns") or []:
                r[c] = w[c].iloc[0]
            r.update(signal_features(w[chans], stats, int(u.get("segments", 1))))
            rows.append(r)
    return pd.DataFrame(rows)


def attach_tables(df, u, base):
    """단위 표에 다른 표의 컬럼을 붙인다 (예: 파일 이름으로 labels.csv의 정답·시각·기존 판정을 연결)."""
    for a in u.get("attach") or []:
        t = read_any(resolve(base, a["path"]), **csv_kwargs(a))
        uk, tk = a["unit_key"], a.get("table_key", a["unit_key"])
        cols = [c for c in (a.get("columns") or t.columns) if c != tk]
        t = t[[tk] + cols].drop_duplicates(subset=[tk])
        t[tk] = t[tk].astype(str)
        df = df.assign(**{"__ak": df[uk].astype(str)}).merge(t.rename(columns={tk: "__ak"}), on="__ak", how="left").drop(columns="__ak")
        miss = int(df[cols[0]].isna().sum()) if cols else 0
        if miss:
            print(f"  [attach] {os.path.basename(a['path'])}: {miss}개 단위가 짝을 못 찾음")
    return df


def build_units(cfg, base, cache_dir, deadline):
    df = _build_units(cfg, base, cache_dir, deadline)
    if cfg["unit"].get("attach"):
        df = attach_tables(df, cfg["unit"], base)
    return df


def _build_units(cfg, base, cache_dir, deadline):
    u = cfg["unit"]
    t = u["type"]
    if t == "segments":
        return unit_segments(u, base, cache_dir, cfg["scenario"], deadline)
    if t == "table":
        return unit_table(u, base)
    if t == "grouped_table":
        return unit_grouped_table(u, base, cache_dir, cfg["scenario"], deadline)
    if t == "files":
        return unit_files(u, base, cache_dir, cfg["scenario"], deadline)
    if t == "windows":
        return unit_windows(u, base)
    raise ValueError(f"unknown unit.type: {t}")


# ---------------------------------------------------------------- 0. 데이터 수집 진단 (DMME: 수집 단계 점검)
ERR_COL = re.compile(r"(Ret|Err|Error|ErrCode)$")


def _audit_source(u, base):
    t = u["type"]
    if t in ("table", "grouped_table"):
        return resolve(base, u["path"])
    if t in ("segments", "windows"):
        fs = sorted(glob.glob(resolve(base, u["paths"])))
        return fs[0] if fs else None
    if t == "files":
        fs = sorted(glob.glob(os.path.join(resolve(base, u["root"]), u.get("glob", "**/*.csv")), recursive=True))
        return fs[0] if fs else None
    return None


def data_audit(cfg, units, base):
    """모델을 돌리기 전에 '데이터가 제대로 수집됐는가'를 자동 점검한다.
    원천 파일 일부와 단위 표를 보고: 값이 하나뿐인 컬럼, 수집 오류 코드가 계속 찍히는 컬럼,
    해상도가 거친 신호, 중복 단위, 결측이 많은 컬럼을 찾는다."""
    u = cfg["unit"]
    ac = cfg.get("audit", {}) or {}
    findings = []
    src = _audit_source(u, base)
    raw_info = {}
    if src and os.path.exists(src):
        kw = csv_kwargs(u)
        kw.pop("usecols", None)
        try:
            if u.get("reader"):            # 장비 전용 형식(JSON 등)은 읽기 플러그인으로
                raw = adapters.read(u["reader"], src)
            elif src.lower().endswith((".xlsx", ".xls")):
                raw = read_any(src)
            else:
                raw = read_any(src, nrows=int(ac.get("raw_rows", 50000)), **kw)
        except TypeError:
            raw = read_any(src, **kw)
        flt = u.get("row_filter")
        if flt and flt["column"] in raw.columns:
            raw = raw[raw[flt["column"]].astype(str).isin([str(v) for v in flt["in"]])]
        raw_info = {"file": os.path.basename(src), "rows_checked": int(len(raw)), "columns": int(raw.shape[1])}
        watch = set(u.get("channels", [])) | set(ac.get("watch", []))
        for c in raw.columns:
            col = raw[c]
            nun = col.nunique(dropna=True)
            if len(raw) and col.isna().mean() > 0.5:
                findings.append({"level": "주의", "column": c, "issue": f"결측 {col.isna().mean():.0%}"})
            if nun == 1 and len(raw) > 100:
                v = col.dropna().iloc[0]
                if ERR_COL.search(str(c)) and str(v) not in ("0", "0.0", "OK", "ok", "True"):
                    findings.append({"level": "심각", "column": c,
                                     "issue": f"수집 결과 코드가 항상 {v} → 이 값을 읽는 수집이 실패하고 있을 가능성"})
                elif c in watch or ac.get("report_constant", True):
                    findings.append({"level": "주의" if c in watch else "참고", "column": c,
                                     "issue": f"값이 항상 {v} (정보 없음)"})
            elif c in watch and pd.api.types.is_numeric_dtype(col):
                v = col.dropna()
                small = float(v.abs().median()) < float(ac.get("low_res_median", 10)) if len(v) else False
                if len(v) and np.allclose(v, v.round()) and (nun <= int(ac.get("low_res_levels", 16)) or small):
                    findings.append({"level": "주의", "column": c,
                                     "issue": f"정수로만 기록 ({nun}단계, {v.min():g}~{v.max():g}, 중앙값 {v.median():g}) → 작은 변화를 못 볼 수 있음"})
    # 단위 표 점검
    num = units.select_dtypes("number")
    if len(num.columns):
        dup = int(num.duplicated().sum())
        if dup:
            findings.append({"level": "주의", "column": "(단위 전체)", "issue": f"숫자 값이 완전히 같은 단위 {dup}개 (중복 수집 의심)"})
    order = {"심각": 0, "주의": 1, "참고": 2}
    findings.sort(key=lambda f: order[f["level"]])
    return {"raw": raw_info, "n_units": int(len(units)), "findings": findings[: int(ac.get("max_findings", 40))],
            "counts": {k: sum(f["level"] == k for f in findings) for k in order}}


def audit_md(a):
    if not a:
        return []
    L = ["", "## 데이터 수집 진단", "",
         f"원천 `{a['raw'].get('file', '-')}` 앞 {a['raw'].get('rows_checked', 0):,}행, 단위 {a['n_units']:,}개 점검 · "
         f"심각 {a['counts']['심각']} / 주의 {a['counts']['주의']} / 참고 {a['counts']['참고']}", ""]
    shown = [f for f in a["findings"] if f["level"] != "참고"][:15]
    if shown:
        L += ["| 수준 | 컬럼 | 내용 |", "|---|---|---|"] + [f"| {f['level']} | {f['column']} | {f['issue']} |" for f in shown]
    else:
        L += ["심각·주의 항목 없음"]
    return L


# ---------------------------------------------------------------- filters
def apply_filters(df, filters):
    for f in filters or []:
        c = f["column"]
        if "eq" in f:
            df = df[df[c].astype(str) == str(f["eq"])]
        if "in" in f:
            df = df[df[c].astype(str).isin([str(x) for x in f["in"]])]
        if "not_in" in f:
            df = df[~df[c].astype(str).isin([str(x) for x in f["not_in"]])]
        if f.get("notna"):
            df = df[df[c].notna()]
    return df


# ---------------------------------------------------------------- 2. label
def attach_label(df, cfg, base):
    lab = cfg["label"]
    src = lab["source"]
    if src == "column":
        df["__y_raw"] = df[lab["column"]]
    elif src == "path_group":
        df["__y_raw"] = df[lab["group"]]
    elif src == "join":
        t = read_any(resolve(base, lab["path"]), **csv_kwargs(lab))
        t = apply_filters(t, lab.get("filters"))
        lk, uk = lab["label_key"], lab.get("unit_key", lab["label_key"])
        t = t.copy()
        t["__k"] = t[lk].astype(str)
        if lab.get("expr"):
            t["__y_raw"] = t.eval(lab["expr"])
        else:
            t["__y_raw"] = t[lab["column"]]
        keep = ["__k", "__y_raw"] + [c for c in lab.get("carry_columns", []) if c in t.columns]
        t = t.drop_duplicates(subset=["__k"])   # 한 단위에 여러 행이 있는 표(시편별 반복 측정 등)는 첫 행만
        df = df.assign(__k=df[uk].astype(str)).merge(t[keep], on="__k", how="inner").drop(columns="__k")
    elif src == "lookup_by_index":
        # 예: 경로의 {tool}, {cut} → ToolWear{tool}.csv 의 (cut-1)번째 행
        cache = {}
        vals = []
        for _, r in df.iterrows():
            p = resolve(base, lab["path_template"].format(**r.to_dict()))
            if p not in cache:
                cache[p] = read_any(p, **csv_kwargs(lab))
            i = int(r[lab["index_group"]]) - int(lab.get("index_base", 1))
            tbl = cache[p]
            vals.append(tbl[lab["column"]].iloc[i] if 0 <= i < len(tbl) else np.nan)
        df["__y_raw"] = vals
    else:
        raise ValueError(f"unknown label.source: {src}")

    y = df["__y_raw"]
    task = cfg["task"]
    if task == "binary":
        if "threshold" in lab:  # 연속값 → 이진 (예: 불량률 > x)
            op, v = lab["threshold"]["op"], lab["threshold"]["value"]
            yy = pd.to_numeric(y, errors="coerce")
            df["__y"] = {">": yy > v, ">=": yy >= v, "<": yy < v, "<=": yy <= v}[op].astype(int)
            df.loc[yy.isna(), "__y"] = np.nan
        elif "negative" in lab:  # 정상값만 지정 → 나머지는 모두 NG
            neg = [str(p) for p in lab["negative"]]
            df["__y"] = (~y.astype(str).isin(neg)).astype(int)
            df.loc[y.isna(), "__y"] = np.nan
        else:
            pos = [str(p) for p in lab["positive"]]
            df["__y"] = y.astype(str).isin(pos).astype(int)
            df.loc[y.isna(), "__y"] = np.nan
    elif task == "multiclass":
        m = lab.get("map")
        df["__y"] = y.map(m) if m else y.astype(str)
    else:
        df["__y"] = pd.to_numeric(y, errors="coerce")
    return df[df["__y"].notna()].reset_index(drop=True)


# ---------------------------------------------------------------- 3. features
def pick_features(df, cfg, fit_mask=None):
    f = cfg.get("features", {}) or {}
    reserved = {"unit_id", "_path", "__y", "__y_raw"}
    excl = set(f.get("exclude", [])) | reserved
    anomaly=cfg.get("anomaly",{}) or {}
    excl |= set(anomaly.get("context",[])) | set(anomaly.get("context_fallback",[]))
    evaluation=anomaly.get("label_eval",{}) or {}
    excl.add(evaluation.get("column",""))
    excl.add((evaluation.get("compare",{}) or {}).get("column",""))
    lab = cfg.get("label") or {"source": "none"}
    excl |= {lab.get("column", "")} if lab["source"] == "column" else set()
    split = cfg.get("split", {})
    for k in ("column", "group"):
        if split.get(k):
            excl.add(split[k])
    if f.get("include") and f["include"] != "auto":
        cols = [c for c in df.columns if any(fnmatch.fnmatch(c, p) for p in f["include"])]
    else:
        cols = list(df.columns)
    cols = [c for c in cols if c not in excl and not any(fnmatch.fnmatch(c, p) for p in f.get("exclude_glob", []))]
    X = df[cols].apply(pd.to_numeric, errors="coerce")
    max_nan = f.get("max_missing", 0.5)
    X = X.replace([np.inf,-np.inf],np.nan)
    fitted = X if fit_mask is None else X.loc[fit_mask]
    cols = fitted.columns[(fitted.isna().mean() <= max_nan) & (fitted.nunique(dropna=True)>1)]
    X = X.loc[:,cols]
    return X


# ---------------------------------------------------------------- 4. split
def make_split(df, cfg):
    s = cfg.get("split", {"method": "random"})
    m = s.get("method", "random")
    ts = s.get("test_size", 0.25)
    rng = np.random.RandomState(s.get("seed", 42))
    n = len(df)
    groups = None
    if m == "random":
        idx = rng.permutation(n)
        if cfg["task"] not in ("regression", "anomaly"):  # 층화
            test = []
            for _, g in df.groupby("__y"):
                gi = rng.permutation(g.index.to_numpy())
                test += list(gi[: max(1, int(round(len(gi) * ts)))])
            test_mask = df.index.isin(test)
        else:
            test_mask = np.isin(np.arange(n), idx[: int(n * ts)])
    elif m == "group":
        groups = df[s["group"]].astype(str)
        if s.get("test_groups"):
            test_mask = groups.isin([str(g) for g in s["test_groups"]]).to_numpy()
        else:
            ug = rng.permutation(groups.unique())
            k = max(1, int(round(len(ug) * ts)))
            test_mask = groups.isin(ug[:k]).to_numpy()
    elif m == "time":
        t = pd.to_datetime(df[s["column"]], errors="coerce") if s.get("datetime", True) else df[s["column"]]
        if t.isna().any():
            raise ValueError("Time split requires a valid timestamp for every unit")
        unique=np.sort(t.unique())
        if len(unique)<2:raise ValueError("Time split requires at least two distinct timestamps")
        cut=max(1,min(len(unique)-1,int(len(unique)*(1-ts))))
        test_mask=t>=unique[cut]
        groups=pd.Series(t.to_numpy(),index=df.index)
    else:
        raise ValueError(f"unknown split.method: {m}")
    return np.asarray(test_mask), groups


def inner_cv(cfg, y, groups, n_splits=4):
    m = cfg.get("split", {}).get("method", "random")
    if m == "group":
        if groups is None or groups.nunique()<2:
            raise ValueError("Group validation requires at least two independent training groups")
        return GroupKFold(n_splits=min(n_splits, groups.nunique()))
    if m == "time":
        from mlng_ai.scenario_validation import ChronologicalSplit
        if groups is None:raise ValueError("Temporal validation requires explicit timestamps")
        return ChronologicalSplit(groups,n_splits=n_splits)
    if cfg["task"] != "regression":
        k = int(min(n_splits, pd.Series(y).value_counts().min()))
        return StratifiedKFold(n_splits=max(2, k), shuffle=True, random_state=0)
    return KFold(n_splits=n_splits, shuffle=True, random_state=0)


# ---------------------------------------------------------------- 5. model
def candidates(task, names):
    imp = SimpleImputer(strategy="median")
    C = {
        "binary": {
            "baseline": DummyClassifier(strategy="prior"),
            "logistic": make_pipeline(imp, StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced")),
            "random_forest": make_pipeline(imp, RandomForestClassifier(n_estimators=300, min_samples_leaf=2, class_weight="balanced_subsample", n_jobs=-1, random_state=0)),
            "hgb": HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, class_weight="balanced", random_state=0),
        },
        "regression": {
            "baseline": DummyRegressor(),
            "ridge": make_pipeline(imp, StandardScaler(), Ridge(alpha=1.0)),
            "random_forest": make_pipeline(imp, RandomForestRegressor(n_estimators=300, min_samples_leaf=2, n_jobs=-1, random_state=0)),
            "hgb": HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, random_state=0),
        },
    }
    C["multiclass"] = {k: v for k, v in C["binary"].items()}
    if MilgEngineClassifier is not None:       # MILG 판정 엔진은 이진 판정 전용
        C["binary"]["milg"] = MilgEngineClassifier()
    elif names and "milg" in names:
        raise RuntimeError(f"model.candidates 의 milg 를 쓸 수 없습니다 (mlng_ai 를 불러오지 못함: {_MILG_ERR})")
    pool = C[task]
    names = names or list(pool)
    if "baseline" not in names:
        names = ["baseline"] + list(names)
    return {n: pool[n] for n in names}


PARAM_SPACE = {
    "hgb": {"learning_rate": [0.03, 0.05, 0.1], "max_iter": [200, 400], "max_leaf_nodes": [15, 31, 63],
            "min_samples_leaf": [5, 10, 20, 40], "l2_regularization": [0.0, 0.1, 1.0]},
    "random_forest": {"n_estimators": [300], "max_depth": [None, 8, 16], "min_samples_leaf": [1, 2, 5],
                      "max_features": ["sqrt", 0.5, 1.0]},
    "logistic": {"C": [0.03, 0.1, 0.3, 1.0, 3.0, 10.0]},
    "ridge": {"alpha": [0.1, 0.3, 1.0, 3.0, 10.0, 30.0]},
}


def search_candidates(cands, n_iter, seed=0):
    """AutoML(하이퍼파라미터 무작위 탐색): 기본 후보마다 설정을 바꾼 변형을 추가한다."""
    rng = np.random.RandomState(seed)
    out = dict(cands)
    names = [n for n in cands if n in PARAM_SPACE]
    for i in range(n_iter):
        if not names:
            break
        n = names[i % len(names)]
        m = clone(cands[n])
        est = m.steps[-1][1] if hasattr(m, "steps") else m
        params = {k: v[rng.randint(len(v))] for k, v in PARAM_SPACE[n].items()}
        est.set_params(**params)
        out[f"{n}#{i+1}"] = m
    return out


def score_fn(task, y, pred, proba):
    if task == "binary":
        if len(np.unique(y)) < 2:
            return np.nan
        return average_precision_score(y, proba)  # 불량 탐지 = PR-AUC
    if task == "multiclass":
        return f1_score(y, pred, average="macro")
    return -mean_absolute_error(y, pred)


def pos_proba(m, X):
    """불량(1) 확률. 학습 구간에 한 클래스만 있으면 그 클래스로 고정."""
    cls = list(m.classes_)
    p = m.predict_proba(X)
    return p[:, cls.index(1)] if 1 in cls else np.zeros(len(X))


def oof_predict(model, X, y, cv, groups, task):
    pred = np.empty(len(y), dtype=object if task == "multiclass" else float)
    proba = np.full(len(y), np.nan)
    splits = cv.split(X, y, groups) if isinstance(cv, GroupKFold) else cv.split(X, y)
    covered = np.zeros(len(y), bool)
    for tr, va in splits:
        if task != "regression" and y.iloc[tr].nunique() < 2:
            # 시간순 분할 초반 폴드처럼 학습 구간에 불량이 하나도 없으면 의미 있는 예측이 아니다
            # → 이 폴드는 모델 비교·임계값 결정에서 뺀다 (넣으면 확률 0이 섞여 임계값이 0으로 떨어짐)
            continue
        m = clone(model).fit(X.iloc[tr], y.iloc[tr])
        pred[va] = m.predict(X.iloc[va])
        if task == "binary":
            proba[va] = pos_proba(m, X.iloc[va])
        covered[va] = True
    return pred, proba, covered


# ---------------------------------------------------------------- 6. decision
def choose_threshold(y, proba, dec):
    if not dec or dec.get("type") == "fixed":
        return float((dec or {}).get("threshold", 0.5))
    if dec["type"] == "threshold_for_recall":
        target_recall = dec.get("target_recall", 0.9)
        # 확률이 높은 순으로 줄 세워, 재현율 목표를 처음 넘는 지점의 확률을 임계값으로 (가장 높은 임계값)
        # 반올림하지 않는다: 확률이 아주 작은 모델은 반올림하면 전부 0이 되어 임계값이 0으로 떨어진다
        y = np.asarray(y).astype(int)
        p = np.asarray(proba, float)
        if y.sum() == 0:
            return 0.5
        budget=float(dec.get('max_false_positive_rate',.1))
        choices=np.unique(np.concatenate([p,[np.nextafter(p.max(),np.inf)]]))
        eligible=[]
        for threshold in choices:
            flag=p>=threshold
            fpr=float(flag[y==0].mean()) if (y==0).any() else 1.
            recall=float(flag[y==1].mean())
            if fpr<=budget+1e-12:eligible.append((recall,float(threshold)))
        if not eligible:return float(np.nextafter(p.max(),np.inf))
        target_choices=[v for v in eligible if v[0]>=target_recall]
        return max(v[1] for v in target_choices) if target_choices else max(eligible)[1]
    raise ValueError(dec["type"])


def decide(task, pred, proba, thr, dec):
    if task == "binary":
        warn = dec.get("warn_ratio", 0.5) * thr if dec else thr
        return np.where(proba >= thr, "NG", np.where(proba >= warn, "WARN", "OK"))
    if task == "regression" and dec and dec.get("type") == "limit":
        lim, w = dec["limit"], dec.get("warn_ratio", 0.8)
        return np.where(pred >= lim, "STOP" if dec.get("action") == "stop" else "NG",
                        np.where(pred >= lim * w, "WARN", "OK"))
    return np.array(["-"] * len(pred))


def metrics(task, y, pred, proba, dec, thr):
    if task == "binary":
        tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
        two = len(np.unique(y)) > 1
        return {"n": int(len(y)), "n_ng": int(y.sum()), "threshold": round(thr, 4),
                "recall": round(recall_score(y, pred, zero_division=0), 4),
                "precision": round(precision_score(y, pred, zero_division=0), 4),
                "false_positive_rate": round(float(fp / max(1, fp + tn)), 4),
                "f1": round(f1_score(y, pred, zero_division=0), 4),
                "roc_auc": round(roc_auc_score(y, proba), 4) if two else None,
                "pr_auc": round(average_precision_score(y, proba), 4) if two else None,
                "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}}
    if task == "multiclass":
        labels = sorted(pd.unique(np.concatenate([np.asarray(y, str), np.asarray(pred, str)])))
        return {"n": int(len(y)), "accuracy": round(accuracy_score(y.astype(str), pred.astype(str)), 4),
                "macro_f1": round(f1_score(y.astype(str), pred.astype(str), average="macro"), 4),
                "labels": labels,
                "confusion": confusion_matrix(y.astype(str), pred.astype(str), labels=labels).tolist()}
    out = {"n": int(len(y)), "mae": round(mean_absolute_error(y, pred), 4),
           "rmse": round(float(np.sqrt(mean_squared_error(y, pred))), 4),
           "r2": round(r2_score(y, pred), 4) if len(y) > 1 else None}
    if dec and dec.get("type") == "limit":
        a_true, a_pred = (y >= dec["limit"]).astype(int), (pred >= dec["limit"]).astype(int)
        out["limit_alarm"] = {"limit": dec["limit"], "recall": round(recall_score(a_true, a_pred, zero_division=0), 4),
                              "precision": round(precision_score(a_true, a_pred, zero_division=0), 4)}
    return out


def importance(model, cols, top=10):
    est = model.steps[-1][1] if hasattr(model, "steps") else model
    if hasattr(est, "feature_importances_"):
        v = est.feature_importances_
    elif hasattr(est, "coef_"):
        v = np.abs(np.ravel(est.coef_)) if est.coef_.ndim == 1 else np.abs(est.coef_).mean(0)
    else:
        return []
    o = np.argsort(v)[::-1][:top]
    return [{"feature": cols[i], "importance": round(float(v[i]), 4)} for i in o]


# ---------------------------------------------------------------- run
def run(path, out_root, max_seconds, data_root=None):
    t0 = time.time()
    cfg, cfg_warnings = load_scenario(path)
    sid = cfg["scenario"]
    base = os.path.abspath(data_root) if data_root else resolve(os.path.dirname(os.path.abspath(path)), cfg.get("data_root", "."))
    out_dir = os.path.join(out_root, sid)
    cache_dir = os.path.join(out_root, "_cache")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    task = cfg["task"]

    df = build_units(cfg, base, cache_dir, t0 + max_seconds)
    side_cells = _SIDE_TABLES.pop(sid, None)
    collapse_info = df.attrs.get("collapse")
    P = schema.validate_columns(cfg, df, after_derive=False)        # 데이터를 읽은 뒤: 가리키는 컬럼이 있는지
    if P.errors:
        raise ConfigError(path, P.errors, cfg_warnings + P.warnings)
    audit = data_audit(cfg, df, base)
    df = apply_filters(df, cfg.get("filters"))
    for d in cfg.get("derive", []) or []:  # 예: {name: work_date, from: TimeStamp, fn: date}
        if d["fn"] == "concat":               # 여러 컬럼을 이어 한 키로 (예: 압력|시간|각도 = 용접 조건)
            df[d["name"]] = df[list(d["from"])].astype(str).agg("|".join, axis=1)
            continue
        s = df[d["from"]]
        if d["fn"] == "date":
            df[d["name"]] = pd.to_datetime(s, errors="coerce").dt.strftime("%Y-%m-%d")
        elif d["fn"] == "hour":
            df[d["name"]] = pd.to_datetime(s, errors="coerce").dt.hour
        elif d["fn"] == "round":               # 연속값을 step 간격으로 묶음 (예: 시작 좌표 2mm 단위)
            step = float(d.get("step", 1))
            df[d["name"]] = (pd.to_numeric(s, errors="coerce") / step).round().astype("Int64").astype(str)
        elif d["fn"] == "prefix":
            df[d["name"]] = s.astype(str).str[: int(d.get("n", 1))]
    P = schema.validate_columns(cfg, df, after_derive=True)
    if P.errors:
        raise ConfigError(path, P.errors, cfg_warnings + P.warnings)
    cfg_warnings = cfg_warnings + P.warnings
    for w in cfg_warnings:
        print(f"  [설정 경고] {sid} · {w}")
    tb = (cfg.get("features") or {}).get("trailing_baseline")
    if tb:
        df = apply_trailing_baseline(df, cfg, tb)
    if task == "anomaly":
        return run_anomaly(cfg, df, out_dir, t0, side_cells, audit)
    df = attach_label(df, cfg, base)
    test_mask, groups = make_split(df, cfg)
    X = pick_features(df, cfg, ~test_mask)
    y = df["__y"].astype(int) if task == "binary" else df["__y"]
    Xtr, Xte, ytr, yte = X[~test_mask], X[test_mask], y[~test_mask], y[test_mask]
    gtr = groups[~test_mask] if groups is not None else None
    cv = inner_cv(cfg, ytr, gtr)

    # 후보 모델 교차검증 (학습 구간 안에서만)
    cands = candidates(task, cfg.get("model", {}).get("candidates"))
    if cfg.get("model", {}).get("search"):
        cands = search_candidates(cands, int(cfg["model"]["search"].get("n_iter", 12)))
    cv_scores, oof = {}, {}
    for name, mdl in cands.items():
        p, pr, cov = oof_predict(mdl, Xtr, ytr, cv, gtr, task)
        cv_scores[name] = round(float(score_fn(task, ytr[cov], p[cov], pr[cov])), 4)
        oof[name] = (p, pr, cov)
    best = max((n for n in cv_scores if n != "baseline"), key=lambda n: (np.nan_to_num(cv_scores[n], nan=-1e9)))
    best_params = None
    if "#" in best:
        est = cands[best].steps[-1][1] if hasattr(cands[best], "steps") else cands[best]
        best_params = {k: est.get_params()[k] for k in PARAM_SPACE[best.split("#")[0]]}
    model = clone(cands[best]).fit(Xtr, ytr)

    dec = cfg.get("decision", {})
    thr = 0.5
    if task == "binary":
        _, pr, cov = oof[best]
        thr = choose_threshold(ytr[cov].to_numpy(), pr[cov], dec)

    def predict(Xp):
        if task == "binary":
            pr = pos_proba(model, Xp)
            return (pr >= thr).astype(int), pr
        p = model.predict(Xp)
        return p, np.full(len(p), np.nan)

    pte, prte = predict(Xte)
    validation=None
    if task=='binary':
        vp,vscore,covered=oof[best]
        validation=metrics(task,ytr[covered],(vscore[covered]>=thr).astype(int),vscore[covered],dec,thr)

    # 후보 전부를 같은 평가 구간에서 비교 (선택은 위의 학습 구간 교차검증으로 이미 끝났다 — 여기 값은 보고용)
    test_cands = {}
    if task == "binary" and yte.nunique() > 1:
        for name, mdl in cands.items():
            if name == "baseline" or "#" in name:
                continue
            pr_c = pos_proba(model if name == best else clone(mdl).fit(Xtr, ytr), Xte)
            test_cands[name] = {"roc_auc": round(float(roc_auc_score(yte, pr_c)), 4), "pr_auc": round(float(average_precision_score(yte, pr_c)), 4)}
    base_m = clone(cands["baseline"]).fit(Xtr, ytr)
    bpred = base_m.predict(Xte)
    bpro = pos_proba(base_m, Xte) if task == "binary" else None

    report = {
        "scenario": sid, "process": cfg.get("process"), "title": cfg.get("title"),
        "question": cfg.get("question"), "engine_version": ENGINE_VERSION,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "units": {"total": int(len(df)), "train": int((~test_mask).sum()), "test": int(test_mask.sum()),
                  "n_features": int(X.shape[1])},
        "split": cfg.get("split"), "cv_metric": {"binary": "pr_auc", "multiclass": "macro_f1", "regression": "-mae"}[task],
        "cv_scores": cv_scores, "best_model": best, "best_params": best_params, "audit": audit,
        "collapse": collapse_info,
        "test": metrics(task, yte, pte, prte, dec, thr),
        "validation":validation, "threshold":float(thr),
        "invalid_test_units":int((~np.isfinite(Xte.to_numpy(float)).all(axis=1)).sum()),
        "beats_baseline":bool(cv_scores[best]>cv_scores["baseline"]),
        "test_baseline": metrics(task, yte, bpred, bpro if bpro is not None else np.zeros(len(yte)), dec, 0.5),
        "test_candidates": test_cands,
        "top_features": importance(model, list(X.columns)),
        "notes": cfg.get("notes", []),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    if task == "binary":
        report["test_baseline"].pop("threshold", None)

    # 7. MES 공통 출력 스키마 (공정과 무관하게 같은 컬럼)
    pall, prall = predict(X)
    out = pd.DataFrame({
        "scenario": sid, "process": cfg.get("process"), "unit_id": df["unit_id"],
        "split": np.where(test_mask, "test", "train"),
        "y_true": df["__y"], "y_pred": pall, "score": np.round(prall, 4),
        "decision": decide(task, pall, prall, thr, dec),
        "model": best, "engine_version": ENGINE_VERSION,
    })
    est = model.steps[-1][1] if hasattr(model, "steps") else model
    if hasattr(est, "explain"):               # MILG 엔진이 뽑혔을 때: WARN·NG 건마다 원인 후보 (불량 쪽으로 민 특성)
        flag = (out["decision"] != "OK").to_numpy()
        causes = np.full(len(out), "", dtype=object)
        if flag.any():
            causes[flag] = [" · ".join(f"{c['feature']} (z {c['z']:+.1f})" for c in row) for row in est.explain(X[flag])]
        out["cause_candidates"] = causes
    for c in cfg.get("output", {}).get("carry_columns", []):
        if c in df.columns:
            out[c] = df[c].to_numpy()
    if task in ('binary','regression','multiclass'):
        from mlng_ai.scenario_runtime import save_bundle
        artifact=save_bundle(out_dir,model,list(X.columns),cfg,report,float(thr))
        sample=X.loc[np.isfinite(X.to_numpy(float)).all(axis=1)].iloc[0].to_dict()
        with open(os.path.join(out_dir,'example-features.json'),'w',encoding='utf-8') as f:json.dump(sample,f,ensure_ascii=False)
    out.to_csv(os.path.join(out_dir, "predictions.csv"), index=False, encoding="utf-8-sig")
    json.dump(report, open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8").write(to_md(report))
    return report


def apply_trailing_baseline(df, cfg, tb):
    """같은 묶음(예: 작업일) 안에서 직전 N개 단위의 중앙값을 기준으로 삼아 편차만 남긴다.
    날마다 금형 온도처럼 조건이 통째로 바뀌는 경우(일간 드리프트)를 흡수한다.
    직전 단위가 min_periods보다 적은 가동 초기에는 원래 값을 그대로 둔다(→ 초기 이상은 그대로 보임)."""
    by, order = tb["by"], tb["order"]
    win, minp = int(tb.get("window", 50)), int(tb.get("min_periods", 10))
    reserved = {"unit_id", "_path", "__y", "__y_raw"}
    excl = set((cfg.get("features") or {}).get("exclude", [])) | reserved | {by}
    lab = cfg.get("label") or {}
    if lab.get("column"):
        excl.add(lab["column"])
    df = df.sort_values(order, kind="stable").reset_index(drop=True)
    feats = [c for c in df.columns if c not in excl and pd.api.types.is_numeric_dtype(df[c])]
    for c in feats:
        rm = df.groupby(by)[c].transform(lambda x: x.shift(1).rolling(win, min_periods=minp).median())
        df[c] = np.where(rm.notna(), df[c] - rm, df[c])
    return df


def cell_dev_features(cells, unit_ctx, train_units, chans, min_train=5, shrink=0.5, inj=None, tail_q=None):
    """위치 셀 비교 (Netzer 등 2021 방식 응용).
    구간을 이송축 위치 격자(셀)로 나누고, '같은 공구·프로그램·같은 위치 셀'의 기준 구간 중앙값 대비
    상대 편차를 구한 뒤, 구간 안 셀들의 가중 중앙값을 채널별 특성으로 쓴다(부호 유지).
    inj=(채널목록, 배율, 단위ID집합)이면 해당 단위의 셀 값을 배율만큼 키워 민감도 점검에 쓴다."""
    C = cells.copy()
    C["key"] = C["unit_id"].map(unit_ctx) + "#" + C["cell"].astype(str)
    if inj:
        chs, f, units = inj
        m = C["unit_id"].isin(units)
        for c in chs:
            if c in C.columns:
                C.loc[m, c] = C.loc[m, c] * f
    base = C[C["unit_id"].isin(train_units)]
    cnt = base.groupby("key").size()
    good = cnt[cnt >= min_train].index
    med = base.groupby("key")[chans].median()
    mad = (base[chans] - med.reindex(base["key"]).to_numpy()).abs().groupby(base["key"]).median() * 1.4826
    rel = (mad / med.abs().replace(0, np.nan)).loc[med.index.intersection(good)].median()
    C = C[C["key"].isin(good)]
    M = med.reindex(C["key"]).to_numpy()
    D = mad.reindex(C["key"]).to_numpy()
    den = np.sqrt(np.nan_to_num(D) ** 2 + (shrink * np.nan_to_num(rel.to_numpy())[None, :] * np.abs(M)) ** 2) + 1e-6
    dev = (C[chans].to_numpy(float) - M) / den
    cols = [f"cell_{c}" for c in chans]
    dd = pd.DataFrame(dev, columns=cols)
    dd["unit_id"], dd["w"] = C["unit_id"].to_numpy(), C["_w"].to_numpy()
    out = {}
    if tail_q:       # 구간 안 셀 편차의 상위 분위수: 일부 동작에서만 생긴 변화(국부 이상, 진행 중 마모)를 잡는다
        tq = dd.groupby("unit_id")[cols].quantile(float(tail_q))
        for c in cols:
            out[c.replace("cell_", "celltail_")] = tq[c]
    for c in cols:   # 가중 중앙값
        t = dd[["unit_id", c, "w"]].dropna().sort_values(["unit_id", c])
        t["cw"] = t.groupby("unit_id")["w"].cumsum()
        t["tot"] = t.groupby("unit_id")["w"].transform("sum")
        out[c] = t[t["cw"] >= t["tot"] / 2].groupby("unit_id")[c].first()
    return pd.DataFrame(out)


# ---------------------------------------------------------------- anomaly (라벨 없음)
def run_anomaly(cfg, df, out_dir, t0, cells=None, audit=None):
    """정답이 없을 때: 같은 맥락(예: 같은 공구)의 '학습 구간 평소 상태'와 비교한 이탈 점수.

    점수 = 그룹별 중앙값·MAD로 정규화한 |z| 중 상위 k개의 평균.
    임계값은 학습 구간 점수 분포의 분위수(WARN/NG)로 정한다.
    정답이 없으므로 '정확도' 대신 학습↔평가 알람률 변화, 공구별 점수 추세를 리포트한다.
    """
    a = cfg.get("anomaly", {})
    gcols = a.get("context", [])
    le = a.get("label_eval")
    # Freeze the original holdout before removing any faulty baseline unit.
    test_mask,_=make_split(df,cfg)
    y_lab=None
    if le:
        pos=df[le["column"]].astype(str).isin([str(v) for v in le["positive"]]).to_numpy()
        keep=~(pos & ~test_mask)
        df=df.loc[keep].reset_index(drop=True)
        test_mask=test_mask[keep]
        y_lab=pos[keep]
    baseline=np.flatnonzero(~test_mask)
    if len(baseline)<12:raise ValueError("Need at least 12 normal reference units")
    col=cfg.get("split",{}).get("column")
    if col:
        time_values=pd.to_datetime(df[col],errors='raise') if cfg['split'].get('datetime',True) else pd.to_numeric(df[col],errors='raise')
        baseline=baseline[np.argsort(time_values.iloc[baseline].to_numpy(),kind='stable')]
    calibration_mask=np.zeros(len(df),bool)
    calibration_mask[baseline[max(2,int(len(baseline)*.8)):]]=True
    tr=~(test_mask|calibration_mask)
    X=pick_features(df,cfg,tr)
    if X.empty:raise ValueError("No valid training sensor features")
    valid_input=np.isfinite(X.to_numpy(float)).all(axis=1)
    keep=test_mask|valid_input
    df=df.loc[keep].reset_index(drop=True);X=X.loc[keep].reset_index(drop=True)
    test_mask=test_mask[keep];calibration_mask=calibration_mask[keep];valid_input=valid_input[keep]
    if y_lab is not None:y_lab=y_lab[keep]
    tr=~(test_mask|calibration_mask)
    if tr.sum()<2 or calibration_mask.sum()<2:raise ValueError('Insufficient finite reference/calibration sensors')
    ctx = df[gcols].astype(str).agg("|".join, axis=1) if gcols else pd.Series("all", index=df.index)
    cb = a.get("cell_baseline")
    cell_cov = None
    if cb and cells is not None and len(cells):
        cell_ctx = df[cb.get("context", gcols)].astype(str).agg("|".join, axis=1)
        unit_ctx = pd.Series(cell_ctx.to_numpy(), index=df["unit_id"].to_numpy())
        train_units = set(df["unit_id"][tr])
        cell_chans = [c for c in cb["channels"] if c in cells.columns]

        def make_cell_feats(inj=None):
            cf = cell_dev_features(cells, unit_ctx, train_units, cell_chans, int(cb.get("min_train", 5)),
                                   float(a.get("pool_shrink", 0.5)), inj, cb.get("tail_quantile"))
            return cf.reindex(df["unit_id"].to_numpy())
        cf0 = make_cell_feats()
        cell_cov = float(cf0.notna().all(axis=1)[test_mask].mean())
        X = pd.concat([X.reset_index(drop=True), cf0.fillna(0.0).reset_index(drop=True)], axis=1)
        X.index = df.index
    min_n = int(a.get("min_context_units", 10))
    k = int(a.get("top_k", 3))
    clip = float(a.get("clip_z", 20))

    med_g = X[tr].groupby(ctx[tr]).median()
    mad_g = (X[tr] - med_g.reindex(ctx[tr]).to_numpy()).abs().groupby(ctx[tr]).median() * 1.4826
    cnt_g = ctx[tr].value_counts()
    med_all = X[tr].median()
    mad_all = (X[tr] - med_all).abs().median() * 1.4826
    ok_ctx = cnt_g[cnt_g >= min_n].index
    med = np.array(med_g.reindex(ctx).to_numpy(), dtype=float)
    mad = np.array(mad_g.reindex(ctx).to_numpy(), dtype=float)
    known = ctx.isin(ok_ctx).to_numpy()
    level = np.where(known, "context", "global").astype(object)
    fb_cols = a.get("context_fallback", [])
    if fb_cols:                                # 세밀한 맥락이 드물면 한 단계 넓은 맥락(예: 공구·프로그램)으로
        fctx = df[fb_cols].astype(str).agg("|".join, axis=1)
        fmed = X[tr].groupby(fctx[tr]).median()
        fmad = (X[tr] - fmed.reindex(fctx[tr]).to_numpy()).abs().groupby(fctx[tr]).median() * 1.4826
        fcnt = fctx[tr].value_counts()
        fok = fctx.isin(fcnt[fcnt >= min_n].index).to_numpy() & ~known
        med[fok] = np.array(fmed.reindex(fctx[fok]).to_numpy(), dtype=float)
        mad[fok] = np.array(fmad.reindex(fctx[fok]).to_numpy(), dtype=float)
        level[fok] = "fallback"
        known = known | fok
    glob_rows = level == "global"
    med[glob_rows] = med_all.to_numpy()          # 그래도 드물면 전체 기준으로 비교
    mad[glob_rows] = mad_all.to_numpy()
    calib = a.get("calibration", "empirical")
    mag = np.abs(med_all.to_numpy()) * float(a.get("rel_floor", 0.01))
    # 퍼짐이 거의 없는 맥락은 z가 폭주한다 → 같은 특성의 '맥락들 전형적인 상대 퍼짐'을 섞어 분모를 보정
    shrink = float(a.get("pool_shrink", 0.5))
    rel = (mad_g / med_g.abs().replace(0, np.nan)).loc[mad_g.index.intersection(ok_ctx)]
    rel_pool = np.nan_to_num(rel.median().to_numpy(), nan=0.0)
    pooled = shrink * rel_pool[None, :] * np.abs(np.nan_to_num(med))
    denom = np.sqrt(np.nan_to_num(mad) ** 2 + pooled ** 2)
    denom = np.maximum(denom, np.maximum(mag, 1e-6)[None, :])
    Xn = X.to_numpy(dtype=float)
    cols = np.array(X.columns)

    if calib == "empirical":
        # 특성마다 '기준 구간 양품에서 이만큼 벗어난 경우가 얼마나 드문가'(꼬리확률)로 바꾼 뒤 비교.
        # 값이 거칠게 끊기는 특성(초 단위 길이 등)이나 꼬리가 두꺼운 특성이 점수를 독차지하지 않는다.
        ztr = np.abs(np.nan_to_num((Xn[tr] - med[tr]) / denom[tr], nan=0.0))
        ref = np.sort(ztr, axis=0)
        n_ref = ref.shape[0]
        zmax = np.maximum(ref[-1], 1e-9)

        def to_rare(zz):
            out = np.empty_like(zz)
            for j in range(zz.shape[1]):
                ge = n_ref - np.searchsorted(ref[:, j], zz[:, j], side="left")
                out[:, j] = -np.log10((ge + 1) / (n_ref + 1))
                over = zz[:, j] > zmax[j]          # 기준 구간 최댓값을 넘으면 넘은 배율만큼 더 올림
                out[over, j] += np.log10(zz[over, j] / zmax[j]) * 3
            return out
    else:
        def to_rare(zz):
            return np.clip(zz, 0, clip)

    # 특성 묶음: 같은 센서에서 나온 특성(평균·표준편차·최대)은 함께 움직이므로 묶어서 본다
    groups_cfg = a.get("groups")
    gid = np.full(len(cols), -1)
    gnames = []
    if groups_cfg:
        for gi, (gname, globs) in enumerate(groups_cfg.items()):
            gnames.append(gname)
            for j, c in enumerate(cols):
                if gid[j] < 0 and any(fnmatch.fnmatch(c, g) for g in globs):
                    gid[j] = gi
    for j, c in enumerate(cols):                # 지정 안 된 특성은 채널(앞부분 이름)별 묶음
        if gid[j] < 0:
            ch = c.split("__")[0]
            if ch not in gnames:
                gnames.append(ch)
            gid[j] = gnames.index(ch)
    G = len(gnames)
    combine = a.get("combine", "top_k")

    def score_rows(Xa, rows):
        zz = np.abs(np.nan_to_num((Xa[rows] - med[rows]) / denom[rows], nan=0.0))
        rr = to_rare(zz)
        if combine == "group":
            gm = np.stack([rr[:, gid == g].mean(axis=1) for g in range(G)], axis=1)
            o = np.argsort(-gm, axis=1)[:, :k]
            sc = gm[np.arange(len(gm)), o[:, 0]]       # 가장 이상한 센서 묶음의 평균 희소도
            return sc, gm, o
        o = np.argsort(-rr, axis=1)[:, :k]
        return np.take_along_axis(rr, o, axis=1).mean(axis=1), rr, o

    all_rows = np.arange(len(Xn))
    score, z, order = score_rows(Xn, all_rows)
    names = np.array(gnames) if combine == "group" else cols
    reason = [", ".join(f"{names[j]}(희소도={z[i, j]:.1f})" for j in order[i]) for i in range(len(z))]

    qw, qn = a.get("warn_quantile", 0.95), a.get("ng_quantile", 0.99)

    def thresholds(sc):
        """기준 구간 점수의 분위수 → (WARN 전체값, NG 전체값, 단위별 WARN, 단위별 NG)"""
        valid=calibration_mask & np.isfinite(sc)
        if valid.sum()<2:raise ValueError("Insufficient valid reference scores")
        w0, n0 = float(np.quantile(sc[valid], qw,method="higher")), float(np.quantile(sc[valid], qn,method="higher"))
        w, n = np.full(len(sc), w0), np.full(len(sc), n0)
        if a.get("threshold_scope") == "context":
            # 하위 공정별 임계값 (KIT 2022): 기준 구간 단위가 충분한 맥락은 그 맥락 안의 분위수로
            min_t = int(a.get("context_threshold_min", 30))
            st = pd.Series(sc[valid]).groupby(ctx[valid].to_numpy())
            cnt_t = st.size()
            qw_g, qn_g = st.quantile(qw,interpolation="higher"), st.quantile(qn,interpolation="higher")
            big = cnt_t[cnt_t >= min_t].index
            m = ctx.isin(big).to_numpy()
            w[m] = qw_g.reindex(ctx[m]).to_numpy()
            n[m] = qn_g.reindex(ctx[m]).to_numpy()
        return w0, n0, w, n

    # 점수 후보: context(시나리오 엔진 기본 — 희소도 상위 평균) · milg(MILG 엔진 정상 기준 모드 — 벗어남 RMS). 첫 번째가 판정에 쓰이고 나머지는 나란히 비교만 한다
    cand_names = list(a.get("candidates") or ["context"])
    S = {"context": {"score": score, "fn": lambda Xa, rr_: score_rows(Xa, rr_)[0], "reason": reason, "model": f"context_{calib}"}}
    if "milg" in cand_names:
        if MilgNormalScorer is None:
            raise RuntimeError(f"anomaly.candidates 의 milg 를 쓸 수 없습니다 (mlng_ai 를 불러오지 못함: {_MILG_ERR})")
        ms = MilgNormalScorer(min_rows=min_n).fit(X[tr], ctx[tr])
        sm, top_m = ms.score(X, ctx)
        S["milg"] = {"score": sm, "fn": lambda Xa, rr_: ms.score(pd.DataFrame(Xa[rr_], columns=X.columns), ctx.iloc[rr_])[0],
                     "reason": [f"{t}(가장 크게 벗어남)" for t in top_m], "top": top_m, "model": "milg_normal"}
    for v in S.values():
        v["thr_w"], v["thr_n"], v["tw"], v["tn"] = thresholds(v["score"])
        usable=valid_input & np.isfinite(v["score"])
        v["score"]=np.where(usable,v["score"],np.nan)
        v["decision"] = np.where(~usable,"UNKNOWN",np.where(v["score"] > v["tn"], "NG", np.where(v["score"] > v["tw"], "WARN", "OK")))
        v["reason"]=[r if ok else "센서 누락 · 판단 불가" for r,ok in zip(v["reason"],usable)]
    primary = cand_names[0]
    score, reason, decision = S[primary]["score"], S[primary]["reason"], S[primary]["decision"]
    thr_w, thr_n, tw, tn = S[primary]["thr_w"], S[primary]["thr_n"], S[primary]["tw"], S[primary]["tn"]

    # 민감도 점검: 불량 샘플이 없을 때, 평가 구간 양품 신호를 일부러 f배 키워 몇 %를 잡는지 본다
    sens = []
    rows = np.where(test_mask)[0]
    for tst in (a.get("sensitivity") or []):
        mask = np.array([any(fnmatch.fnmatch(c, g) for g in tst["columns"]) and not c.startswith(("cell_", "celltail_")) for c in cols])
        if not mask.any():
            continue
        for f in tst.get("factors", [1.1, 1.2, 1.5]):
            Xa = X.to_numpy(dtype=float).copy()
            Xa[:, mask] *= float(f)
            if cb and cells is not None and tst.get("cell_channels"):
                test_units = set(df["unit_id"][test_mask])
                cfi = make_cell_feats((tst["cell_channels"], float(f), test_units)).fillna(0.0)
                for j, c in enumerate(cols):
                    if c in cfi.columns:
                        Xa[:, j] = cfi[c].to_numpy()
            for cn in cand_names:
                s2 = S[cn]["fn"](Xa, rows)
                row = {"test": tst["name"], "factor": float(f), "n_features": int(mask.sum()),
                       "detect_ng": round(float((s2 >= S[cn]["tn"][rows]).mean()), 4),
                       "detect_warn_or_ng": round(float((s2 >= S[cn]["tw"][rows]).mean()), 4)}
                if cn == primary:
                    sens.append(row)
                else:
                    S[cn].setdefault("sens", []).append(row)

    tcol = cfg.get("split", {}).get("column")
    trend = []
    if a.get("trend_by") and tcol:
        tt = pd.to_datetime(df[tcol], errors="coerce")
        for g, idx in df.groupby(a["trend_by"]).groups.items():
            if len(idx) < min_n:
                continue
            rho = pd.Series(score[idx]).rank().corr(pd.Series(tt[idx].rank().to_numpy()))
            trend.append({"group": str(g), "n": int(len(idx)), "spearman_score_vs_time": round(float(rho), 3),
                          "ng_rate": round(float((decision[idx] == "NG").mean()), 4)})
        trend.sort(key=lambda r: -r["spearman_score_vs_time"])

    feat_freq = (pd.Series(S[primary]["top"][decision == "NG"]).value_counts() if primary == "milg"
                 else pd.Series([names[j] for i in np.where(decision == "NG")[0] for j in order[i][:1]]).value_counts())
    rate = lambda m, lab: round(float((decision[m] == lab).mean()), 4) if m.any() else None
    cand_report = {}
    if len(cand_names) > 1:                       # 후보 나란히 비교 — 같은 기준 구간 · 같은 분위수 임계값
        for cn in cand_names:
            dc = S[cn]["decision"]
            cand_report[cn] = {"model": S[cn]["model"], "primary": cn == primary,
                               "baseline": {"warn_rate": round(float((dc[tr] == "WARN").mean()), 4), "ng_rate": round(float((dc[tr] == "NG").mean()), 4)},
                               "test": {"warn_rate": round(float((dc[test_mask] == "WARN").mean()), 4) if test_mask.any() else None,
                                        "ng_rate": round(float((dc[test_mask] == "NG").mean()), 4) if test_mask.any() else None},
                               "label_eval": label_eval_metrics(y_lab, S[cn]["score"], dc, test_mask) if y_lab is not None else None,
                               "sensitivity": sens if cn == primary else S[cn].get("sens", [])}
    report = {
        "scenario": cfg["scenario"], "process": cfg.get("process"), "title": cfg.get("title"),
        "question": cfg.get("question"), "engine_version": ENGINE_VERSION,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "units": {"total": int(len(df)), "train": int(tr.sum()), "calibration":int(calibration_mask.sum()), "test": int(test_mask.sum()),
                  "n_features": int(X.shape[1]), "contexts": int(ctx.nunique()),
                  "contexts_with_baseline": int(len(ok_ctx)), "units_without_context_baseline": int((~known).sum()),
                  "baseline_level": {k: int(v) for k, v in pd.Series(level).value_counts().items()}},
        "split": cfg.get("split"), "invalid_input_units":int((~valid_input).sum()), "best_model": S[primary]["model"], "candidates": cand_report,
        "threshold_source":"held_out_normal_calibration",
        "thresholds": {"warn": round(thr_w, 3), "ng": round(thr_n, 3), "warn_quantile": qw, "ng_quantile": qn,
                       "scope": a.get("threshold_scope", "global")},
        "test": {"n": int(test_mask.sum()), "warn_rate": rate(test_mask, "WARN"), "ng_rate": rate(test_mask, "NG")},
        "test_baseline": {"n": int(tr.sum()), "warn_rate": rate(tr, "WARN"), "ng_rate": rate(tr, "NG")},
        "normal_only": bool(a.get("normal_only", False)), "sensitivity": sens,
        "cell_baseline_coverage": cell_cov, "audit": audit,
        "label_eval": label_eval_metrics(y_lab, score, decision, test_mask) if y_lab is not None else None,
        "label_eval_compare": compare_existing(df, le, y_lab, test_mask) if (y_lab is not None and le.get("compare")) else None,
        "trend_top": trend[:8], "trend_bottom": trend[-3:] if len(trend) > 8 else [],
        "top_features": [{"feature": f, "importance": int(c)} for f, c in feat_freq.head(10).items()],
        "notes": cfg.get("notes", []), "elapsed_sec": round(time.time() - t0, 1),
    }
    out = pd.DataFrame({
        "scenario": cfg["scenario"], "process": cfg.get("process"), "unit_id": df["unit_id"],
        "split": np.where(test_mask,"test",np.where(calibration_mask,"calibration","train")), "y_true": np.nan, "y_pred": np.nan,
        "score": np.round(score, 3), "decision": decision, "input_valid":valid_input, "model": S[primary]["model"],
        "engine_version": ENGINE_VERSION, "reason": reason,
    })
    for cn in cand_names[1:]:                     # 비교 후보의 점수 · 판정도 같이 싣는다 (MES 는 decision 만 쓰면 된다)
        out[f"score_{cn}"] = np.round(S[cn]["score"], 3)
        out[f"decision_{cn}"] = S[cn]["decision"]
    for c in cfg.get("output", {}).get("carry_columns", []):
        if c in df.columns:
            out[c] = df[c].to_numpy()
    from mlng_ai.scenario_runtime import ContextReference,save_bundle
    refs={}
    for key in sorted(set(ctx[tr])):
        i=np.flatnonzero(tr & (ctx.to_numpy()==key))[0]
        refs[str(key)]=(med[i].copy(),denom[i].copy())
    global_med=med_all.to_numpy(float)
    global_scale=np.maximum(np.sqrt(np.nan_to_num(mad_all.to_numpy(float))**2+(shrink*rel_pool*np.abs(global_med))**2),np.maximum(mag,1e-6))
    fallback_refs={}
    if fb_cols:
        for key in fcnt[fcnt>=min_n].index:
            fm=fmed.loc[key].to_numpy(float);fs=fmad.loc[key].to_numpy(float)
            scale=np.maximum(np.sqrt(fs**2+(shrink*rel_pool*np.abs(fm))**2),np.maximum(mag,1e-6))
            fallback_refs[str(key)]=(fm,scale)
    frozen=ContextReference({'columns':list(X.columns),'references':refs,'fallback_references':fallback_refs,
        'global_reference':(global_med,global_scale),'calibration':calib,
        'tail_reference':ref if calib=='empirical' else None,'clip':clip,'combine':combine,
        'group_ids':gid,'group_names':gnames,'top_k':k})
    selected=ms if primary=='milg' else frozen
    context_thresholds={}
    for key in sorted(set(ctx[calibration_mask])):
        i=np.flatnonzero(calibration_mask & (ctx.to_numpy()==key))[0]
        context_thresholds[str(key)]=[float(tw[i]),float(tn[i])]
    thresholds_bundle={'warn':float(thr_w),'ng':float(thr_n),'contexts':context_thresholds}
    context_columns=list(dict.fromkeys(gcols+fb_cols))
    save_bundle(out_dir,selected,list(X.columns),cfg,report,thresholds_bundle,context_columns,
                {'fallback_columns':fb_cols,'anomaly_context_columns':gcols})
    valid_example=np.flatnonzero(valid_input)[0]
    sample={**X.iloc[valid_example].to_dict(),**df[context_columns].iloc[valid_example].to_dict()}
    with open(os.path.join(out_dir,'example-features.json'),'w',encoding='utf-8') as f:json.dump(sample,f,ensure_ascii=False)
    if primary=='context':
        fs,_=frozen.score(X,ctx.to_numpy(),df[fb_cols].astype(str).agg('|'.join,axis=1).to_numpy() if fb_cols else None)
        np.testing.assert_allclose(fs[valid_input],score[valid_input],rtol=1e-8,atol=1e-8)
    out.to_csv(os.path.join(out_dir, "predictions.csv"), index=False, encoding="utf-8-sig")
    json.dump(report, open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8").write(to_md_anomaly(report))
    return report


def label_eval_metrics(y, score, decision, test_mask):
    t = test_mask
    yt, st, dt = y[t], score[t], decision[t]
    usable=np.isfinite(st) & (dt!="UNKNOWN")
    out = {"test_units": int(t.sum()), "test_defects": int(yt.sum())}
    out["unknown_units"]=int((~usable).sum())
    if yt[usable].any() and (~yt[usable]).any():
        out["auc"] = round(float(roc_auc_score(yt[usable], st[usable])), 4)
        out["pr_auc"] = round(float(average_precision_score(yt[usable], st[usable])), 4)
    out["defects_caught_ng"] = int((yt & (dt == "NG")).sum())
    out["defects_caught_warn_or_ng"] = int((yt & np.isin(dt, ["WARN", "NG"])).sum())
    good = ~yt
    out["false_alarm_ng"] = round(float((dt[good] == "NG").mean()), 4) if good.any() else None
    out["false_alarm_warn_or_ng"] = round(float(np.isin(dt[good], ["WARN", "NG"]).mean()), 4) if good.any() else None
    return out


def compare_existing(df, le, y, test_mask):
    """기존 판정(예: 체결기 OK/NOK)이 같은 평가 구간에서 실제 불량을 얼마나 잡았는지 — MILG와 나란히 비교."""
    c = le["compare"]
    flag = df[c["column"]].astype(str).isin([str(v) for v in c["positive"]]).to_numpy()
    t = test_mask
    yt, ft = y[t], flag[t]
    return {"name": c.get("name", c["column"]), "defects_caught": int((yt & ft).sum()), "test_defects": int(yt.sum()),
            "false_alarm": round(float(ft[~yt].mean()), 4) if (~yt).any() else None}


def to_md_anomaly(r):
    u = r["units"]
    L = [f"# {r['scenario']} · {r['title']}", "", f"- 공정: {r['process']}", f"- 질문: {r['question']}",
         f"- 단위: 전체 {u['total']} / 기준(학습) {u['train']} / 평가 {u['test']} · 특성 {u['n_features']}개",
         f"- 비교 맥락: {u['contexts']}개 중 기준 확보 {u['contexts_with_baseline']}개 (기준 부족 단위 {u['units_without_context_baseline']}개는 전체 기준으로 비교)",
         f"- 분할: {r['split']}",
         (f"- 방식: MILG 엔진 정상 기준 모드 — 같은 맥락의 기준 구간 중앙값 · 산포 대비 벗어남 RMS ({r['best_model']})" if r["best_model"] == "milg_normal"
          else f"- 방식: 같은 맥락의 기준 구간 대비 특성별 희소도(-log10 꼬리확률) 상위 평균 ({r['best_model']})"),
         f"- 임계값: WARN ≥ {r['thresholds']['warn']} (기준 구간 {r['thresholds']['warn_quantile']:.0%}), "
         f"NG ≥ {r['thresholds']['ng']} ({r['thresholds']['ng_quantile']:.0%})", "",
         "## " + ("오경보율 (전부 양품인 데이터)" if r.get("normal_only") else "알람률 (기준 구간 vs 평가 구간)"), "",
         "| 구간 | 단위 수 | WARN | NG |", "|---|---|---|---|",
         f"| 기준 | {r['test_baseline']['n']} | {r['test_baseline']['warn_rate']} | {r['test_baseline']['ng_rate']} |",
         f"| 평가 | {r['test']['n']} | {r['test']['warn_rate']} | {r['test']['ng_rate']} |", "",
         ("데이터가 모두 양품이므로 여기 나온 WARN·NG는 전부 오경보입니다. 평가 구간 값이 현장에서 예상되는 오경보율입니다."
          if r.get("normal_only") else
          "평가 구간 NG 비율이 기준(약 1%)보다 크게 높으면 설비·공구 상태가 기준 시기와 달라졌다는 신호입니다."), ""]
    le = r.get("label_eval")
    if le:
        L += ["## 실제 불량으로 평가 (기준은 양품만으로 잡음)", "",
              f"평가 구간 {le['test_units']:,}개 중 실제 불량 {le['test_defects']}개 · AUC {le.get('auc')} · PR-AUC {le.get('pr_auc')}", "",
              "| | NG로 판정 | WARN 이상 |", "|---|---|---|",
              f"| 불량 검출 | {le['defects_caught_ng']}/{le['test_defects']} | {le['defects_caught_warn_or_ng']}/{le['test_defects']} |",
              f"| 양품 오경보 | {le['false_alarm_ng']:.1%} | {le['false_alarm_warn_or_ng']:.1%} |", ""]
        ce = r.get("label_eval_compare")
        if ce:
            L += [f"비교 — 기존 판정({ce['name']}): 불량 {ce['defects_caught']}/{ce['test_defects']} 검출, "
                  f"양품 오경보 {ce['false_alarm']:.1%}", ""]
    cr = r.get("candidates")
    if cr:
        L += ["## 점수 방식 비교 (같은 기준 구간 · 같은 분위수 임계값 — 굵게 = 판정에 쓴 방식)", "",
              "| 방식 | 평가 WARN | 평가 NG | 실제 불량 AUC | 불량 검출 (NG · WARN 이상) | 양품 오경보 (NG · WARN 이상) |", "|---|---|---|---|---|---|"]
        for cn, c in cr.items():
            e = c.get("label_eval") or {}
            det = f"{e['defects_caught_ng']}/{e['test_defects']} · {e['defects_caught_warn_or_ng']}/{e['test_defects']}" if e else "—"
            fa = f"{e['false_alarm_ng']:.1%} · {e['false_alarm_warn_or_ng']:.1%}" if e and e.get("false_alarm_ng") is not None else "—"
            L.append(f"| {'**' + c['model'] + '**' if c['primary'] else c['model']} | {c['test']['warn_rate']} | {c['test']['ng_rate']} | {e.get('auc', '—') if e else '—'} | {det} | {fa} |")
        others = [(cn, c) for cn, c in cr.items() if not c["primary"] and c.get("sensitivity")]
        for cn, c in others:
            L += ["", f"민감도 — {c['model']}: " + " · ".join(f"{x['test']} ×{x['factor']} {x['detect_warn_or_ng']:.0%}" for x in c["sensitivity"])]
        L += [""]
    if r.get("sensitivity"):
        L += ["## 민감도 점검 (평가 구간 양품 신호를 일부러 키웠을 때 잡아내는 비율)", "",
              "| 점검 | 배율 | 바꾼 특성 수 | NG로 검출 | WARN 이상 검출 |", "|---|---|---|---|---|"]
        L += [f"| {x['test']} | ×{x['factor']} | {x['n_features']} | {x['detect_ng']:.1%} | {x['detect_warn_or_ng']:.1%} |" for x in r["sensitivity"]]
        L += ["", "실제 불량이 아니라 인위적으로 만든 변화라서, '이 정도 변화부터 잡힌다'는 감도 확인용입니다.", ""]
    if r["trend_top"]:
        L += ["## 점수가 시간에 따라 오르는 맥락", "",
              "| 맥락 | 단위 수 | 점수-시간 순위상관 | NG 비율 |", "|---|---|---|---|"]
        for t in r["trend_top"] + r["trend_bottom"]:
            L.append(f"| {t['group']} | {t['n']} | {t['spearman_score_vs_time']} | {t['ng_rate']} |")
    L += ["", "## NG 판정에 자주 나온 특성", ""] + [f"- {f['feature']} ({f['importance']}회)" for f in r["top_features"]]
    if r.get("cell_baseline_coverage") is not None:
        L += ["", f"위치 셀 기준 확보율(평가 구간): {r['cell_baseline_coverage']:.1%}"]
    L += audit_md(r.get("audit"))
    if r.get("notes"):
        L += ["", "## 주의"] + [f"- {n}" for n in r["notes"]]
    return "\n".join(L) + "\n"


def to_md(r):
    L = [f"# {r['scenario']} · {r['title']}", "", f"- 공정: {r['process']}", f"- 질문: {r['question']}",
         f"- 단위: 전체 {r['units']['total']} / 학습 {r['units']['train']} / 평가 {r['units']['test']} · 특성 {r['units']['n_features']}개",
         f"- 분할: {r['split']}", f"- 교차검증({r['cv_metric']}): {r['cv_scores']} → **{r['best_model']}**",
         (f"- 행 합치기: 원본 {r['collapse']['rows_in']:,}행 → 완전중복 제거 {r['collapse']['after_dedupe']:,}행 → 단위 {r['collapse']['units']:,}개"
          if r.get("collapse") else ""), "",
         "## 평가 구간 결과", "", "| 지표 | 모델 | 기준선 |", "|---|---|---|"]
    for k, v in r["test"].items():
        if isinstance(v, (int, float)) or v is None:
            L.append(f"| {k} | {v} | {r['test_baseline'].get(k, '')} |")
    for k in ("confusion", "limit_alarm"):
        if k in r["test"]:
            L.append(f"\n{k}: `{r['test'][k]}`")
    if r.get("test_candidates"):
        L += ["", "## 후보별 평가 구간 비교 (보고용 — 선택은 학습 구간 교차검증)", "", "| 후보 | ROC-AUC | PR-AUC |", "|---|---|---|"]
        L += [f"| {'**' + k + '**' if k == r['best_model'] else k} | {v['roc_auc']} | {v['pr_auc']} |" for k, v in r["test_candidates"].items()]
    L += ["", "## 주요 특성" + (" (원인 후보 순위)" if r["best_model"] == "milg" else ""), ""] + [f"- {f['feature']} ({f['importance']})" for f in r["top_features"]]
    L += audit_md(r.get("audit"))
    if r.get("notes"):
        L += ["", "## 주의"] + [f"- {n}" for n in r["notes"]]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--out", default=os.path.join(ENGINE_DIR, "outputs"))
    ap.add_argument("--max-seconds", type=float, default=1e9, help="파일형 단위 특성 추출 시간 예산(초)")
    ap.add_argument("--data-root",help="Override template data root")
    ap.add_argument("--install-state",help="Register passing candidates into this local engine state")
    ap.add_argument("--process",help="Explicit process key for a single scenario")
    ap.add_argument("--source-license",help="Verified commercial-use source license")
    ap.add_argument("--approved-by",help="Operator recording this registration")
    ap.add_argument("--check", action="store_true", help="실행하지 않고 시나리오 파일만 검사")
    a = ap.parse_args()
    paths = [p for s in a.scenarios for p in sorted(glob.glob(s))]
    if a.install_state and (not a.source_license or not a.approved_by):
        ap.error("--install-state requires --source-license and --approved-by")
    if a.process and len(paths)!=1:
        ap.error("--process requires exactly one scenario")
    if not paths:ap.error("No scenario files matched")
    if a.check:
        bad = 0
        for p in paths:
            try:
                _, warns = load_scenario(p)
                print(f"[OK] {os.path.basename(p)}" + (f"  (경고 {len(warns)})" if warns else ""))
                for w in warns:
                    print(f"     경고  {w}")
            except ConfigError as e:
                bad += 1
                print(f"[설정 오류] {os.path.basename(p)} — {len(e.errors)}개")
                for m in e.errors:
                    print(f"     오류  {m}")
                for w in e.warnings:
                    print(f"     경고  {w}")
        return 1 if bad else 0
    summary = []
    failures=0
    for p in paths:
        if os.path.basename(p).startswith("_"):
            continue
        try:
            r = run(p, a.out, a.max_seconds, a.data_root)
            t = r["test"]
            key = {k: t.get(k) for k in ("recall", "precision", "false_positive_rate", "pr_auc", "macro_f1", "accuracy", "mae", "r2", "warn_rate", "ng_rate") if k in t}
            print(f"[OK] {r['scenario']:<22} {r['best_model']:<14} {key}  ({r['elapsed_sec']}s)")
            if a.install_state:
                if r.get('deployment_gate',{}).get('passed'):
                    from mlng_ai.scenario_runtime import install
                    process_key=a.process or ('scenario_'+r['scenario'].lower().replace('-','_'))
                    release=install(os.path.join(a.out,r['scenario'],'candidate.joblib'),a.install_state,process_key,a.source_license,a.approved_by,os.path.join(a.out,r['scenario'],'deployment'))
                    print(f"[REGISTERED] {process_key}: {release}")
                else:
                    print(f"[HELD] {r['scenario']}: evaluation gate failed; existing routes retained")
            summary.append({"scenario": r["scenario"], "process": r["process"], "model": r["best_model"], **key})
        except Incomplete as e:
            failures+=1
            print(f"[..] {os.path.basename(p)}: {e}")
        except ConfigError as e:
            failures+=1
            print(f"[설정 오류] {os.path.basename(p)} — 실행하지 않았습니다 ({len(e.errors)}개)")
            for m in e.errors:
                print(f"     오류  {m}")
            for w in e.warnings:
                print(f"     경고  {w}")
        except Exception as e:
            failures+=1
            print(f"[ERR] {os.path.basename(p)}: {type(e).__name__}: {e}")
    if summary:
        pd.DataFrame(summary).to_csv(os.path.join(a.out, "summary.csv"), index=False, encoding="utf-8-sig")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
