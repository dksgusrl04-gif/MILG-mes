"""MILG 시나리오 YAML 스키마 검증 — 외부 패키지 없이 동작.

두 단계로 검사한다.
  1) validate_config(cfg)          : 파일만 보고 — 모르는 키(오타), 빠진 필수 키, 값의 형식·범위, 조합 규칙
  2) validate_columns(cfg, units)  : 데이터를 읽은 뒤 — 시나리오가 가리키는 컬럼이 실제로 있는지

오류는 한 번에 모두 모아 'split.tset_size: 모르는 키 (혹시 test_size?)' 형태로 돌려준다.
"""
from __future__ import annotations

import difflib
import fnmatch
import re

# ---------------------------------------------------------------- 스키마 표기
# 각 키: (형식, 필수 여부, 추가 규칙 dict)
#   형식: "str" "int" "num" "bool" "list" "dict" "str|list" "any" 또는 하위 스키마 dict
#   규칙: enum=[...], min=, max=, items="str"|하위스키마, gt0=True, open=True(자유 키 허용)
STR, INT, NUM, BOOL, LIST, DICT, ANY = "str", "int", "num", "bool", "list", "dict", "any"


def F(typ, req=False, **rules):
    return {"type": typ, "req": req, **rules}


CSV_KEYS = {
    "sep": F(STR), "encoding": F(STR), "header": F(ANY), "names": F(LIST, items=STR),
    "usecols": F(LIST), "skiprows": F(ANY),
}

FILTER = {"column": F(STR, True), "eq": F(ANY), "in": F(LIST), "not_in": F(LIST), "notna": F(BOOL)}

COLLAPSE = {"dedupe": F(LIST, items=STR), "key": F("str|list", True), "any": F(DICT, open=True)}

CELLS = {"columns": F(LIST, True, items=STR), "step": F(NUM, gt0=True), "channels": F(LIST, items=STR)}

ATTACH = {"path": F(STR, True), "unit_key": F(STR, True), "table_key": F(STR), "columns": F(LIST, items=STR), **CSV_KEYS}
UNIT_COMMON = {"type": F(STR, True, enum=["table", "grouped_table", "files", "segments", "windows"]),
               "attach": F(LIST, items=ATTACH)}

READER_NAMES = ["pyscrew_json"]   # adapters.READERS와 맞춘다
WIDE_STATS = ["mean", "std", "min", "max", "p2p", "rms", "q95", "q05", "auc"]
MERGE = {"path": F(STR, True), "keys": F("str|list", True), "suffix": F(STR), **CSV_KEYS}
WIDE = {"groups": F(DICT, True, open=True), "stats": F(LIST, items=STR, enum_items=WIDE_STATS),
        "segments": F(INT, min=1), "trim_zeros": F(BOOL)}
SIG_STATS = ["mean", "std", "min", "max", "p2p", "rms", "last", "first", "q05", "q95", "kurt", "skew", "len"]
UNIT_BY_TYPE = {
    "table": {"path": F(STR, True), "id": F(STR), "pre_filters": F(LIST, items=FILTER),
              "collapse": F(COLLAPSE), "merge": F(LIST, items=MERGE), "wide_series": F(WIDE), **CSV_KEYS},
    "grouped_table": {"path": F(STR, True), "key": F(STR, True),
                      "stats": F(LIST, items=STR, enum_items=["mean", "std", "min", "max"]),
                      "drop_columns": F(LIST, items=STR), "chunksize": F(INT, gt0=True), **CSV_KEYS},
    "files": {"root": F(STR, True), "glob": F(STR), "path_regex": F(STR), "exclude_glob": F(LIST, items=STR),
              "stats": F(LIST, items=STR, enum_items=["mean", "std", "min", "max", "p2p", "rms", "last", "first",
                                                      "q05", "q95", "kurt", "skew", "len"]),
              "segments": F(INT, min=1), "channels": F(LIST, items=STR),
              "reader": F(STR, enum=READER_NAMES), "segment_column": F(STR), **CSV_KEYS},
    "windows": {"paths": F(STR, True), "rows": F(INT, True, min=2), "step_rows": F(INT, min=1), "time": F(STR),
                "channels": F(LIST, items=STR), "stats": F(LIST, items=STR, enum_items=SIG_STATS),
                "segments": F(INT, min=1), "first_columns": F(LIST, items=STR), **CSV_KEYS},
    "segments": {"paths": F(STR, True), "time": F(STR, True), "segment_by": F(LIST, True, items=STR),
                 "channels": F(LIST, True, items=STR), "row_filter": F({"column": F(STR, True), "in": F(LIST, True)}),
                 "max_gap_sec": F(NUM, gt0=True), "min_rows": F(INT, min=1), "chunksize": F(INT, gt0=True),
                 "stats": F(LIST, items=STR, enum_items=["mean", "std", "min", "max", "p2p"]),
                 "first_columns": F(LIST, items=STR), "cells": F(CELLS), "encoding": F(STR)},
}

LABEL_COMMON = {"source": F(STR, True, enum=["column", "path_group", "join", "lookup_by_index"]),
                "positive": F(LIST), "negative": F(LIST), "map": F(DICT, open=True),
                "threshold": F({"op": F(STR, True, enum=[">", ">=", "<", "<="]), "value": F(NUM, True)})}
LABEL_BY_SOURCE = {
    "column": {"column": F(STR, True)},
    "path_group": {"group": F(STR, True)},
    "join": {"path": F(STR, True), "label_key": F(STR, True), "unit_key": F(STR), "column": F(STR), "expr": F(STR),
             "filters": F(LIST, items=FILTER), "carry_columns": F(LIST, items=STR), **CSV_KEYS},
    "lookup_by_index": {"path_template": F(STR, True), "index_group": F(STR, True), "index_base": F(INT),
                        "column": F(STR, True), **CSV_KEYS},
}

FEATURES = {
    "include": F("str|list"), "exclude": F(LIST, items=STR), "exclude_glob": F(LIST, items=STR),
    "max_missing": F(NUM, min=0, max=1),
    "trailing_baseline": F({"by": F(STR, True), "order": F(STR, True), "window": F(INT, min=2),
                            "min_periods": F(INT, min=1)}),
}

SPLIT = {"method": F(STR, True, enum=["random", "group", "time"]), "test_size": F(NUM, gt0=True, max=0.95),
         "seed": F(INT), "group": F(STR), "test_groups": F(LIST), "column": F(STR), "datetime": F(BOOL)}

MODEL_NAMES = ["baseline", "logistic", "random_forest", "hgb", "ridge", "milg"]   # milg = MILG 판정 엔진 (이진 전용)
MODEL = {"candidates": F(LIST, items=STR, enum_items=MODEL_NAMES), "search": F({"n_iter": F(INT, min=1)})}

DECISION = {"type": F(STR, enum=["threshold_for_recall", "fixed", "limit"]), "target_recall": F(NUM, gt0=True, max=1),
            "threshold": F(NUM), "warn_ratio": F(NUM, gt0=True, max=1), "limit": F(NUM), "action": F(STR, enum=["stop", "ng"])}

SENS_TEST = {"name": F(STR, True), "columns": F(LIST, True, items=STR), "cell_channels": F(LIST, items=STR),
             "factors": F(LIST, items=NUM)}
ANOMALY = {
    "context": F(LIST, items=STR), "context_fallback": F(LIST, items=STR), "min_context_units": F(INT, min=1),
    "top_k": F(INT, min=1), "warn_quantile": F(NUM, gt0=True, max=1), "ng_quantile": F(NUM, gt0=True, max=1),
    "threshold_scope": F(STR, enum=["global", "context"]), "context_threshold_min": F(INT, min=1),
    "calibration": F(STR, enum=["empirical", "none"]), "combine": F(STR, enum=["top_k", "group"]),
    "groups": F(DICT, open=True), "pool_shrink": F(NUM, min=0), "rel_floor": F(NUM, min=0), "clip_z": F(NUM, gt0=True),
    "trend_by": F("str|list"), "normal_only": F(BOOL),
    "cell_baseline": F({"channels": F(LIST, True, items=STR), "min_train": F(INT, min=1), "context": F(LIST, items=STR),
                        "tail_quantile": F(NUM, gt0=True, max=1)}),
    "sensitivity": F(LIST, items=SENS_TEST),
    "label_eval": F({"column": F(STR, True), "positive": F(LIST, True),
                     "compare": F({"column": F(STR, True), "positive": F(LIST, True), "name": F(STR)})}),
}

AUDIT = {"raw_rows": F(INT, min=1), "watch": F(LIST, items=STR), "report_constant": F(BOOL),
         "low_res_levels": F(INT, min=1), "low_res_median": F(NUM), "max_findings": F(INT, min=1)}

DERIVE = {"name": F(STR, True), "from": F("str|list", True), "fn": F(STR, True, enum=["date", "hour", "round", "prefix", "concat"]),
          "step": F(NUM, gt0=True), "n": F(INT, min=1)}

TOP = {
    "scenario": F(STR, True), "process": F(STR, True), "title": F(STR, True), "question": F(STR),
    "data_root": F(STR), "unit": F(DICT, True), "filters": F(LIST, items=FILTER), "derive": F(LIST, items=DERIVE),
    "task": F(STR, True, enum=["binary", "multiclass", "regression", "anomaly"]), "label": F(DICT),
    "features": F(FEATURES), "split": F(SPLIT), "model": F(MODEL), "decision": F(DECISION), "anomaly": F(ANOMALY),
    "audit": F(AUDIT), "output": F({"carry_columns": F(LIST, items=STR)}), "notes": F(LIST, items=STR),
}


# ---------------------------------------------------------------- 검사기
class Problems:
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, path, msg):
        self.errors.append(f"{path}: {msg}")

    def warn(self, path, msg):
        self.warnings.append(f"{path}: {msg}")


def _typename(v):
    if v is None:
        return "빈 값"
    return {str: "문자열", int: "정수", float: "숫자", bool: "참/거짓", list: "목록", dict: "묶음"}.get(type(v), type(v).__name__)


def _type_ok(v, typ):
    if typ == ANY:
        return True
    if typ == STR:
        return isinstance(v, str)
    if typ == INT:
        return isinstance(v, int) and not isinstance(v, bool)
    if typ == NUM:
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if typ == BOOL:
        return isinstance(v, bool)
    if typ == LIST:
        return isinstance(v, list)
    if typ == DICT:
        return isinstance(v, dict)
    if typ == "str|list":
        return isinstance(v, (str, list))
    return True


TYPE_KO = {STR: "문자열", INT: "정수", NUM: "숫자", BOOL: "true/false", LIST: "목록 [ ]", DICT: "묶음",
           "str|list": "문자열 또는 목록", ANY: "아무 값"}


def _check_value(path, v, field, P):
    typ = field["type"]
    if isinstance(typ, dict):            # 하위 스키마
        if not isinstance(v, dict):
            P.err(path, f"묶음(키: 값)이어야 합니다. 지금은 {_typename(v)}")
            return
        _check_map(path, v, typ, P)
        return
    if not _type_ok(v, typ):
        P.err(path, f"{TYPE_KO[typ]}이어야 합니다. 지금은 {_typename(v)} ({v!r})")
        return
    if "enum" in field and v not in field["enum"]:
        hint = difflib.get_close_matches(str(v), [str(e) for e in field["enum"]], n=1)
        P.err(path, f"'{v}'는 쓸 수 없는 값입니다. 가능한 값: {', '.join(map(str, field['enum']))}"
                    + (f" (혹시 {hint[0]}?)" if hint else ""))
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if field.get("gt0") and v <= 0:
            P.err(path, f"0보다 커야 합니다 (지금 {v})")
        if "min" in field and v < field["min"]:
            P.err(path, f"{field['min']} 이상이어야 합니다 (지금 {v})")
        if "max" in field and v > field["max"]:
            P.err(path, f"{field['max']} 이하여야 합니다 (지금 {v})")
    if typ == LIST and isinstance(v, list):
        it = field.get("items")
        for i, x in enumerate(v):
            p = f"{path}[{i}]"
            if isinstance(it, dict):
                if not isinstance(x, dict):
                    P.err(p, f"묶음(키: 값)이어야 합니다. 지금은 {_typename(x)}")
                else:
                    _check_map(p, x, it, P)
            elif it and not _type_ok(x, it):
                P.err(p, f"{TYPE_KO[it]}이어야 합니다. 지금은 {_typename(x)} ({x!r})")
            elif "enum_items" in field and x not in field["enum_items"]:
                hint = difflib.get_close_matches(str(x), field["enum_items"], n=1)
                P.err(p, f"'{x}'는 쓸 수 없는 값입니다. 가능한 값: {', '.join(field['enum_items'])}"
                         + (f" (혹시 {hint[0]}?)" if hint else ""))


def _check_map(path, m, schema, P, open_=False):
    for k, v in m.items():
        p = f"{path}.{k}" if path else k
        if isinstance(k, bool):   # YAML 1.1: on/off/yes/no/true/false 키는 참·거짓으로 읽힌다
            P.err(f"{path}.{k}" if path else str(k), "키 이름이 참/거짓으로 읽혔습니다. YAML에서 on·off·yes·no는 "
                  "따옴표로 감싸거나 다른 이름을 쓰세요 (예: 'on' → keys)")
            continue
        if k not in schema:
            if open_:
                continue
            hint = difflib.get_close_matches(str(k), list(schema), n=1, cutoff=0.6)
            P.err(p, "모르는 키입니다" + (f" (혹시 {hint[0]}?)" if hint else f". 쓸 수 있는 키: {', '.join(schema)}"))
            continue
        if v is None and not schema[k]["req"]:
            continue
        _check_value(p, v, schema[k], P)
    for k, f in schema.items():
        if f["req"] and k not in m:
            P.err(f"{path}.{k}" if path else k, "필수 키가 빠졌습니다")


def validate_config(cfg) -> Problems:
    P = Problems()
    if not isinstance(cfg, dict):
        P.err("(파일)", "YAML 최상위가 '키: 값' 묶음이 아닙니다")
        return P
    _check_map("", cfg, TOP, P)

    # unit: 유형별로 쓸 수 있는 키가 다르다
    u = cfg.get("unit")
    if isinstance(u, dict):
        t = u.get("type")
        if t in UNIT_BY_TYPE:
            _check_map("unit", u, {**UNIT_COMMON, **UNIT_BY_TYPE[t]}, P)
        elif "type" not in u:
            P.err("unit.type", "필수 키가 빠졌습니다 (table / grouped_table / files / segments)")
        else:
            _check_value("unit.type", t, UNIT_COMMON["type"], P)
        if t == "files" and u.get("path_regex"):
            try:
                re.compile(u["path_regex"])
            except re.error as e:
                P.err("unit.path_regex", f"정규식 오류: {e}")
        if isinstance(u.get("cells"), dict) and t != "segments":
            P.err("unit.cells", "segments 단위에서만 쓸 수 있습니다")

    task = cfg.get("task")
    lab = cfg.get("label")
    a = cfg.get("anomaly") or {}

    # label: 지도학습이면 필수, 이상 탐지면 쓰지 않음(대신 anomaly.label_eval)
    if task in ("binary", "multiclass", "regression"):
        if not isinstance(lab, dict):
            P.err("label", f"task가 {task}이면 필수입니다")
        else:
            src = lab.get("source")
            if src in LABEL_BY_SOURCE:
                _check_map("label", lab, {**LABEL_COMMON, **LABEL_BY_SOURCE[src]}, P)
            else:
                _check_map("label", {"source": src} if src else {}, LABEL_COMMON, P)
            if task == "binary" and not any(k in lab for k in ("positive", "negative", "threshold")):
                P.err("label", "binary는 positive(불량값), negative(정상값), threshold 중 하나가 필요합니다")
            if task != "binary" and any(k in lab for k in ("positive", "negative")):
                P.warn("label", f"positive/negative는 binary에서만 쓰입니다 (지금 task: {task})")
            if src == "join" and not (lab.get("column") or lab.get("expr")):
                P.err("label", "join은 column 또는 expr 중 하나가 필요합니다")
        if cfg.get("anomaly"):
            P.warn("anomaly", f"task가 {task}이면 anomaly 설정은 쓰이지 않습니다")
    elif task == "anomaly":
        if lab:
            P.err("label", "anomaly에서는 쓰지 않습니다. 실제 불량으로 평가하려면 anomaly.label_eval을 쓰세요")
        if cfg.get("decision"):
            P.warn("decision", "anomaly에서는 쓰이지 않습니다 (anomaly.warn_quantile / ng_quantile로 정함)")
        if cfg.get("model"):
            P.warn("model", "anomaly에서는 쓰이지 않습니다")
        wq, nq = a.get("warn_quantile", 0.95), a.get("ng_quantile", 0.99)
        if isinstance(wq, (int, float)) and isinstance(nq, (int, float)) and wq >= nq:
            P.err("anomaly", f"warn_quantile({wq})은 ng_quantile({nq})보다 작아야 합니다")
        cb = a.get("cell_baseline")
        if cb and not (isinstance(u, dict) and u.get("cells")):
            P.err("anomaly.cell_baseline", "unit.cells가 있어야 씁니다 (segments 단위 + 위치 셀)")
        if a.get("sensitivity") and not a.get("normal_only") and not a.get("label_eval"):
            P.warn("anomaly.sensitivity", "민감도 점검은 보통 normal_only: true(전부 양품)일 때 의미가 있습니다")
        for i, t in enumerate(a.get("sensitivity") or []):
            if isinstance(t, dict) and t.get("cell_channels") and not cb:
                P.warn(f"anomaly.sensitivity[{i}].cell_channels", "cell_baseline이 없어 쓰이지 않습니다")

    # split: 방법별 필수 키
    s = cfg.get("split")
    if isinstance(s, dict):
        m = s.get("method")
        if m == "group" and not s.get("group"):
            P.err("split.group", "method가 group이면 필수입니다 (어떤 컬럼으로 묶을지)")
        if m == "time" and not s.get("column"):
            P.err("split.column", "method가 time이면 필수입니다 (시간 컬럼)")
        if m != "group" and s.get("test_groups"):
            P.warn("split.test_groups", "method가 group일 때만 쓰입니다")
        if m == "random" and task != "anomaly":
            P.warn("split.method", "random 분할은 같은 조건의 반복 사이클이 학습·평가에 나뉘어 성능이 부풀 수 있습니다")
    elif task:
        P.warn("split", "없으면 random 분할을 씁니다. 현장 배포 조건에 맞는 분할(group/time)을 권합니다")

    # model: MILG 판정 엔진은 이진 판정 전용
    if task and task != "binary" and "milg" in ((cfg.get("model") or {}).get("candidates") or []):
        P.err("model.candidates", "milg(MILG 판정 엔진)은 binary에서만 쓸 수 있습니다")

    # decision: 과제와 맞는지
    d = cfg.get("decision")
    if isinstance(d, dict) and task in ("binary", "multiclass", "regression"):
        t = d.get("type")
        if t == "limit" and task != "regression":
            P.err("decision.type", "limit은 regression에서만 쓸 수 있습니다")
        if t in ("threshold_for_recall", "fixed") and task != "binary":
            P.err("decision.type", f"{t}은 binary에서만 쓸 수 있습니다")
        if t == "limit" and "limit" not in d:
            P.err("decision.limit", "type이 limit이면 필수입니다")

    # model.search: 평가 그룹이 적으면 과적합 경고 (MCT-01 실험 결과)
    mdl = cfg.get("model") or {}
    if isinstance(mdl, dict) and mdl.get("search") and isinstance(s, dict) and s.get("method") == "group":
        P.warn("model.search", "group 분할에서 학습 그룹이 적으면 탐색이 교차검증에 과적합될 수 있습니다")

    # derive 이름이 겹치는지
    names = [x.get("name") for x in (cfg.get("derive") or []) if isinstance(x, dict)]
    for n in {n for n in names if names.count(n) > 1}:
        P.err("derive", f"name '{n}'이 두 번 정의됐습니다")
    for i, x in enumerate(cfg.get("derive") or []):
        if isinstance(x, dict) and (x.get("fn") == "concat") != isinstance(x.get("from"), list):
            P.err(f"derive[{i}].from", "fn이 concat이면 컬럼 목록, 아니면 컬럼 하나를 적어야 합니다")
        if isinstance(x, dict) and x.get("fn") == "round" and "step" not in x:
            P.warn(f"derive[{i}].step", "round에서 생략하면 1을 씁니다")
    return P


# ---------------------------------------------------------------- 데이터를 읽은 뒤: 컬럼 존재 확인
def _need(cols, path, name, P, where="단위 표"):
    if name is None or name in cols:
        return
    hint = difflib.get_close_matches(str(name), [str(c) for c in cols], n=2, cutoff=0.6)
    P.err(path, f"컬럼 '{name}'이 {where}에 없습니다" + (f" (혹시 {' / '.join(hint)}?)" if hint else ""))


def validate_columns(cfg, units, after_derive=False) -> Problems:
    """build_units 직후(after_derive=False) 또는 derive 이후(True)에 호출."""
    P = Problems()
    cols = set(units.columns)
    derived = {x["name"] for x in (cfg.get("derive") or []) if isinstance(x, dict) and "name" in x}
    avail = cols | (set() if after_derive else derived)
    if not after_derive:
        for i, x in enumerate(cfg.get("derive") or []):
            for c in (x.get("from") if isinstance(x.get("from"), list) else [x.get("from")]):
                _need(cols, f"derive[{i}].from", c, P)
        for i, f in enumerate(cfg.get("filters") or []):
            _need(cols, f"filters[{i}].column", f.get("column"), P)
        return P
    lab = cfg.get("label") or {}
    if lab.get("source") == "column":
        _need(avail, "label.column", lab.get("column"), P)
    if lab.get("source") == "path_group":
        _need(avail, "label.group", lab.get("group"), P, "경로 정규식 이름 그룹")
    if lab.get("source") == "join":
        _need(avail, "label.unit_key", lab.get("unit_key", lab.get("label_key")), P)
    if lab.get("source") == "lookup_by_index":
        _need(avail, "label.index_group", lab.get("index_group"), P, "경로 정규식 이름 그룹")
    s = cfg.get("split") or {}
    if s.get("method") == "group":
        _need(avail, "split.group", s.get("group"), P)
        if s.get("test_groups") and s.get("group") in units.columns:
            have = set(units[s["group"]].astype(str))
            miss = [g for g in s["test_groups"] if str(g) not in have]
            if miss:
                P.err("split.test_groups", f"{miss}는 '{s['group']}' 값에 없습니다 (있는 값 예: {sorted(have)[:5]})")
    if s.get("method") == "time":
        joined = set(lab.get("carry_columns", [])) if lab.get("source") == "join" else set()
        if s.get("column") not in joined:
            _need(avail, "split.column", s.get("column"), P)
    a = cfg.get("anomaly") or {}
    for k in ("context", "context_fallback"):
        for c in a.get(k) or []:
            _need(avail, f"anomaly.{k}", c, P)
    if a.get("trend_by"):
        for c in ([a["trend_by"]] if isinstance(a["trend_by"], str) else a["trend_by"]):
            _need(avail, "anomaly.trend_by", c, P)
    if a.get("label_eval"):
        _need(avail, "anomaly.label_eval.column", a["label_eval"].get("column"), P)
        if a["label_eval"].get("compare"):
            _need(avail, "anomaly.label_eval.compare.column", a["label_eval"]["compare"].get("column"), P)
    tb = (cfg.get("features") or {}).get("trailing_baseline")
    if tb:
        _need(avail, "features.trailing_baseline.by", tb.get("by"), P)
        _need(avail, "features.trailing_baseline.order", tb.get("order"), P)
    for i, c in enumerate((cfg.get("output") or {}).get("carry_columns") or []):
        if c not in avail and not (lab.get("source") == "join" and c in lab.get("carry_columns", [])):
            P.warn(f"output.carry_columns[{i}]", f"컬럼 '{c}'이 없어 출력에서 빠집니다")
    # 제외 목록에 있는데 데이터에 없는 컬럼 → 오타일 가능성
    for c in (cfg.get("features") or {}).get("exclude") or []:
        if c not in avail:
            hint = difflib.get_close_matches(str(c), [str(x) for x in avail], n=1, cutoff=0.7)
            if hint:
                P.warn("features.exclude", f"'{c}'은 데이터에 없습니다 (혹시 {hint[0]}? 오타면 제외가 안 됩니다)")
    for i, t in enumerate(a.get("sensitivity") or []):
        pats = t.get("columns") or []
        if pats and not any(fnmatch.fnmatch(c, p) for c in avail for p in pats):
            P.warn(f"anomaly.sensitivity[{i}].columns", f"{pats}에 맞는 특성이 없어 이 점검은 건너뜁니다")
    return P
