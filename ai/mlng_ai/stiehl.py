"""Mendeley «Multivariate time series data of milling processes with varying tool wear and machine tools» (Stiehl, 2023, CC BY 4.0)
→ 가공 1회 = 한 행 요약 CSV (MCT 기본 모델 입력).

  원본: h5 파일 6,418개 (설비 3대 × 공구 9개, 회차마다 마모 VB µm) + filelist.csv
        signals_sensor  : 공구 동력계 절삭력 X · Y · Z (25 kHz)
        signals_machine : 주축 · 이송축 토크, 이송축 위치 편차 (500 Hz, 설비 제어기)
  출력: stiehl_runs.csv  — machine · tool · run · contact_min · wear_um + 채널별 RMS · 표준편차 · 최대 진폭 · 첨도 · 평균

  python -m mlng_ai stiehl <h5 폴더> <요약 폴더> [--workers 4]
  이미 요약한 파일은 건너뛰므로 중간에 멈춰도 다시 실행하면 이어서 한다.
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

SKIP = {'time_machine', 'time_sensor', 'tool_position_x', 'tool_position_y', 'tool_position_z'}
NAME = {'force_sensor_x': 'force_x', 'force_sensor_y': 'force_y', 'force_sensor_z': 'force_z', 'torque_spindle': 'torque_spindle',
        'torque_axis_x': 'torque_axis_x', 'torque_axis_y': 'torque_axis_y', 'torque_axis_z': 'torque_axis_z',
        'position_control_deviation_axis_x': 'pos_dev_x', 'position_control_deviation_axis_y': 'pos_dev_y'}
LABEL = {'force_x': ('절삭력 X', 'N'), 'force_y': ('절삭력 Y', 'N'), 'force_z': ('절삭력 Z', 'N'), 'torque_spindle': ('주축 토크', 'N·m'),
         'torque_axis_x': ('X축 토크', 'N·m'), 'torque_axis_y': ('Y축 토크', 'N·m'), 'torque_axis_z': ('Z축 토크', 'N·m'),
         'pos_dev_x': ('X축 위치 편차', 'mm'), 'pos_dev_y': ('Y축 위치 편차', 'mm')}


def _stats(v: np.ndarray, key: str) -> dict:
    v = v[np.isfinite(v)].astype(np.float64)
    if not len(v):
        return {}
    m, s = v.mean(), v.std()
    return {f'{key}_mean': m, f'{key}_rms': float(np.sqrt((v * v).mean())), f'{key}_std': s,
            f'{key}_p2p': float(np.percentile(v, 99.5) - np.percentile(v, 0.5)), f'{key}_kurt': float(((v - m) ** 4).mean() / (s ** 4 + 1e-12))}


def summarize_file(path: str) -> dict:
    import h5py
    r = {'file': os.path.basename(path)}
    with h5py.File(path, 'r') as f:
        lab = f['labels']
        r.update(machine=int(lab['machine'][()].ravel()[0]), tool=int(lab['tool'][()].ravel()[0]), run=int(lab['run'][()].ravel()[0]),
                 contact_min=float(lab['cumulated_tool_contact_time'][()].ravel()[0]), wear_um=float(lab['wear'][()].ravel()[0]))
        for grp in ('signals_sensor', 'signals_machine'):
            if grp not in f:
                continue
            for k, ds in f[grp].items():
                if k in SKIP:
                    continue
                r.update(_stats(np.asarray(ds[()]).ravel(), NAME.get(k, k)))
        if 'signals_sensor/time_sensor' in f:
            t = np.asarray(f['signals_sensor/time_sensor'][()]).ravel()
            r['cut_seconds'] = float(t[-1] - t[0]) if len(t) > 1 else np.nan
    return r


def summarize(src: str | Path, dst: str | Path, workers: int = 4) -> Path:
    """파일마다 채널 구성이 다를 수 있어(일부 회차에만 있는 채널) 진행 기록은 JSONL 로 쌓고, 끝에 모든 칼럼을 합쳐 CSV 로 쓴다"""
    import json
    src, dst = Path(src), Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    out, log = dst / 'stiehl_runs.csv', dst / 'stiehl_runs.jsonl'
    files = sorted(src.rglob('*.h5'))
    rows = [json.loads(l) for l in log.read_text(encoding='utf-8').splitlines() if l.strip()] if log.exists() else []
    done = {r['file'] for r in rows}
    todo = [str(p) for p in files if p.name not in done]
    print(f'h5 {len(files)}개 · 이미 요약 {len(done)}개 · 이번에 {len(todo)}개 → {out}')
    t0 = time.time()
    with open(log, 'a', encoding='utf-8') as lf, ProcessPoolExecutor(max_workers=max(1, workers)) as ex:
        for i, r in enumerate(ex.map(summarize_file, todo, chunksize=8), 1):
            r = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in r.items()}
            lf.write(json.dumps(r, ensure_ascii=False) + '\n')
            rows.append(r)
            if i % 200 == 0 or i == len(todo):
                lf.flush()
                print(f'  {i}/{len(todo)}  {time.time() - t0:.0f}초', flush=True)
    df = pd.DataFrame(rows).drop_duplicates('file', keep='last')
    df.to_csv(out, index=False)
    part = df.notna().mean()
    some = part[(part > 0) & (part < 1)]
    if len(some):
        print(f'  일부 회차에만 있는 칼럼 {len(some)}개 (빈 값으로 둠): ' + ', '.join(sorted({c.rsplit("_", 1)[0] for c in some.index})))
    print(f'완료 — {len(df)}회 · 칼럼 {df.shape[1]}개')
    return out


def main(argv):
    w = int(argv[argv.index('--workers') + 1]) if '--workers' in argv else max(1, (os.cpu_count() or 2) - 1)
    summarize(argv[0], argv[1], w)


if __name__ == '__main__':
    main(sys.argv[1:])
