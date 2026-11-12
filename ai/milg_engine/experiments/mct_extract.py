"""MCT 실험 공통 입력: DBexport에서 '절삭' 행만 필요한 컬럼으로 뽑아 파일별 pkl로 저장.
주 단위 파일이 경계일에 겹치므로, 앞 파일의 마지막 시각 이후 행만 남긴다.
사용: python mct_extract.py <DBexport.csv> [...]   (이미 만든 파일은 건너뜀)
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_rows")
COLS = ["Update_time", "RunState", "ToolNo", "RunPGM", "MotionGCode",
        "X_Mac", "Y_Mac", "Z_Mac", "B_AxisPos",
        "Cur_R_A", "Cur_S_A", "Cur_T_A", "SpindleLoad",
        "Vib_X_mms", "Vib_Y_mms", "Vib_Z_mms", "X_Current", "Y_Current", "Z_Current"]


def extract(path):
    name = os.path.basename(path).replace(".csv", "")
    dst = os.path.join(OUT, name + ".pkl")
    if os.path.exists(dst):
        print("skip", name)
        return
    t0 = time.time()
    parts = []
    for ch in pd.read_csv(path, usecols=COLS, chunksize=300_000, encoding="utf-8-sig", low_memory=False):
        ch = ch[ch["RunState"] == "가동중·절삭"].drop(columns="RunState")
        parts.append(ch)
    df = pd.concat(parts, ignore_index=True)
    df["t"] = pd.to_datetime(df["Update_time"], errors="coerce")
    df = df.drop(columns="Update_time")
    out = pd.DataFrame({
        "t": df["t"].to_numpy(),
        "tool": pd.to_numeric(df["ToolNo"], errors="coerce").astype("float32"),
        "pgm": pd.to_numeric(df["RunPGM"], errors="coerce").astype("float32"),
        "g": df["MotionGCode"].astype(str).str.strip(),
        "x": df["X_Mac"].astype("float32"), "y": df["Y_Mac"].astype("float32"),
        "z": df["Z_Mac"].astype("float32"), "b": df["B_AxisPos"].astype("float32"),
        "cur": df[["Cur_R_A", "Cur_S_A", "Cur_T_A"]].mean(axis=1).astype("float32"),
        "load": df["SpindleLoad"].astype("float32"),
        "vib": df[["Vib_X_mms", "Vib_Y_mms", "Vib_Z_mms"]].mean(axis=1).astype("float32"),
        "ycur": df["Y_Current"].astype("float32"),
    })
    os.makedirs(OUT, exist_ok=True)
    out.to_pickle(dst)
    print(f"{name}: {len(out):,} rows, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        extract(p)
