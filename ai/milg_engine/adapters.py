"""MILG 데이터 읽기 플러그인.

엔진은 CSV·엑셀은 직접 읽는다. 장비마다 형식이 다른 파일(체결기 JSON 등)은 여기에 읽는 함수를 하나 추가하고,
시나리오에서 `unit.reader: <이름>`으로 고른다. 읽는 함수는 '파일 경로 → 시계열 DataFrame(행=시간, 열=채널)'만 하면 된다.
"""
from __future__ import annotations

import json

import pandas as pd


def read_pyscrew_json(path):
    """pyscrew 체결 데이터(Weidmüller CS351 등 체결기 JSON) → 단계별 토크·각도 곡선.
    'tightening steps'의 각 단계 graph를 이어 붙이고, 몇 번째 단계인지 step 컬럼으로 남긴다."""
    j = json.load(open(path, encoding="utf-8"))
    parts = []
    for i, st in enumerate(j.get("tightening steps") or []):
        g = st.get("graph") or {}
        if not g.get("torque values"):
            continue
        d = pd.DataFrame({
            "torque": g.get("torque values"),
            "angle": g.get("angle values"),
            "gradient": g.get("gradient values"),
            "time": g.get("time values"),
        })
        d["step"] = i
        parts.append(d)
    if not parts:
        return pd.DataFrame(columns=["torque", "angle", "gradient", "time", "step"])
    return pd.concat(parts, ignore_index=True)


READERS = {
    "pyscrew_json": read_pyscrew_json,
}


def read(name, path):
    if name not in READERS:
        raise ValueError(f"알 수 없는 reader: {name} (있는 것: {', '.join(READERS)})")
    return READERS[name](path)
