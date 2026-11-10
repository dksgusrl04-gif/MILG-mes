"""압입 · 조립 삽입 기본 모델 — 힘 · 토크 곡선 공정. 곡선 1개 = 한 행으로 요약해 MILG 엔진에 넣는다.

데이터 (공개 · CC BY 4.0)
  Delta 로봇 바퀴 조립   Zenodo 13641620 — 타이어를 림에 끼우는 조립 524회, 힘 3축 · 토크 3축 (0.004 s 간격), OK 라벨, 6일
                         3 · 4일차 라벨은 저자가 무효로 밝혀 제외. 1 · 2일차는 영상 분류기 라벨(부정확할 수 있음), 5 · 6일차는 사람 라벨.
  CERTH 스냅핏           Zenodo 1209203 — 캔틸레버 · 환형 스냅핏 삽입 힘 곡선 220개, 성공/실패 라벨
                         (학습 세트 = 사람 조립 · 로봇이 센서 역할, 시험 세트 = 로봇 자동 조립). 라벨 0 을 실패로 본다(문서에 방향 표기 없음).

곡선 요약 (길이에 기대지 않는 값만 — 실패하면 일찍 멈추는 기록이라 길이 자체는 정답을 흘린다)
  채널마다 최대 · 최소 · 평균 · 표준편차 · 마지막 5% 평균 · 최대 위치(전체 대비) · 최대 기울기 · 기울기 표준편차 · 큰 봉우리 수

  python -m mlng_ai pressfit <Delta CSV> <스냅핏 zip 또는 폴더> <요약 폴더>
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .datasets import Table

CH_KO = {'force_x': ('힘 X', 'N'), 'force_y': ('힘 Y', 'N'), 'force_z': ('힘 Z (삽입 방향)', 'N'), 'torque_x': ('토크 X', 'N·m'), 'torque_y': ('토크 Y', 'N·m'),
         'torque_z': ('토크 Z', 'N·m'), 'force': ('삽입 힘', 'N')}
ST_KO = {'max': '최대', 'min': '최소', 'mean': '평균', 'std': '표준편차', 'end': '마지막 구간 평균', 'peakpos': '최대 위치 (전체 대비)', 'slope': '최대 기울기',
         'slopesd': '기울기 표준편차', 'npeaks': '큰 봉우리 수', 'area': '곡선 평균 높이',
         **{f'seg{i}': f'구간 {i}/5 평균' for i in range(1, 6)}, **{f'segslope{i}': f'구간 {i}/5 기울기' for i in range(1, 6)}}
SEG = 5     # 곡선을 50점으로 맞춘 뒤 5구간 — 구간별 평균 · 기울기 (곡선 모양: 어디서 힘이 오르고 꺾이나)


def curve_features(v: np.ndarray, key: str) -> dict:
    v = np.asarray(v, float).ravel()
    v = v[np.isfinite(v)]
    if len(v) < 3:
        return {}
    n = len(v)
    d = np.diff(v)
    tail = v[-max(1, n // 20):]
    hi = v.min() + 0.5 * (v.max() - v.min())
    peaks = np.sum((v[1:-1] > v[:-2]) & (v[1:-1] >= v[2:]) & (v[1:-1] > hi)) if v.max() > v.min() else 0
    r = {f'{key}_max': v.max(), f'{key}_min': v.min(), f'{key}_mean': v.mean(), f'{key}_std': v.std(), f'{key}_end': tail.mean(),
         f'{key}_peakpos': float(np.argmax(v)) / (n - 1), f'{key}_slope': float(np.abs(d).max()), f'{key}_slopesd': float(d.std()), f'{key}_npeaks': float(peaks)}
    if n >= 2 * SEG:
        u = np.interp(np.linspace(0, n - 1, 50), np.arange(n), v)
        for i, sg in enumerate(np.array_split(u, SEG), 1):
            r[f'{key}_seg{i}'] = float(sg.mean())
            r[f'{key}_segslope{i}'] = float(np.polyfit(np.arange(len(sg)), sg, 1)[0])
        r[f'{key}_area'] = float(u.mean())
    return r


def summarize_delta(csv: str | Path) -> pd.DataFrame:
    d = pd.read_csv(csv)
    d.columns = [c.lower() for c in d.columns]
    rows = []
    for idx, g in d.groupby('idx', sort=True):
        r = {'curve_id': f'D{int(idx):03d}', 'day': int(g.meas_id.iloc[0]), 'ok': bool(str(g.label.iloc[0]).lower() == 'true')}
        for c in ('force_x', 'force_y', 'force_z', 'torque_x', 'torque_y', 'torque_z'):
            r.update(curve_features(g[c].to_numpy(), c))
        rows.append(r)
    return pd.DataFrame(rows)


def _snap_files(src: Path):
    """zip 이든 풀린 폴더든 .mat 파일 (이름, 바이트) 목록"""
    if src.is_file() and src.suffix == '.zip':
        z = zipfile.ZipFile(src)
        return [(n, z.read(n)) for n in z.namelist() if n.endswith('.mat')]
    return [(str(p.relative_to(src)), p.read_bytes()) for p in src.rglob('*.mat')]


def summarize_snap(src: str | Path) -> pd.DataFrame:
    import scipy.io as sio
    files = dict(_snap_files(Path(src)))
    rows = []
    for name, raw in files.items():
        if name.endswith('_Labels.mat'):
            continue
        lab_name = name.replace('.mat', '_Labels.mat')
        if lab_name not in files:
            continue
        m = sio.loadmat(io.BytesIO(raw))
        s = m[[k for k in m if not k.startswith('__')][0]][0, 0]
        L = sio.loadmat(io.BytesIO(files[lab_name]))
        lab = L[[k for k in L if not k.startswith('__')][0]].ravel()
        kind = 'cantilever' if 'antil' in name else 'annular'
        part = 'robot' if 'Testing' in name else 'human'
        for i, sig in enumerate(s.dtype.names):
            if i >= len(lab):
                break
            r = {'curve_id': f'{kind[:3]}-{part}-{sig}', 'snap_type': kind, 'assembler': part, 'group': f'{kind}·{part}', 'ok': bool(lab[i] == 1)}
            r.update(curve_features(np.asarray(s[sig]).squeeze(), 'force'))
            rows.append(r)
    return pd.DataFrame(rows)


def summarize(delta_csv, snap_src, dst) -> list[Path]:
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    out = []
    if delta_csv:
        p = dst / 'pf_delta.csv'
        summarize_delta(delta_csv).to_csv(p, index=False)
        out.append(p)
    if snap_src:
        p = dst / 'pf_snap.csv'
        summarize_snap(snap_src).to_csv(p, index=False)
        out.append(p)
    return out


# ── 엔진 표
def _meta(df: pd.DataFrame) -> dict:
    meta = {'assembly_ng': {'label': '조립 불량'}, 'curve_id': {'label': '곡선 번호'}}
    for c in df.columns:
        k, _, st = c.rpartition('_')
        if k in CH_KO and st in ST_KO:
            meta[c] = {'label': f'{CH_KO[k][0]} {ST_KO[st]}', 'unit': '' if st in ('peakpos', 'npeaks') else CH_KO[k][1]}
    return meta


def delta_table(path) -> tuple[Table, np.ndarray]:
    raw = pd.read_csv(path)
    raw = raw[raw.day.isin([1, 2, 5, 6])].sort_values(['day', 'curve_id']).reset_index(drop=True)     # 3 · 4일차 라벨 무효
    raw['assembly_ng'] = (~raw.ok.astype(bool)).astype(float)
    df = raw.drop(columns=['ok', 'day'])
    return Table('pf_delta', '압입 · 바퀴 림 조립 (Delta 로봇)', df, _meta(df), {}, '', {}, 'row', 'Zenodo 13641620 (CC BY 4.0) · 1 · 2 · 5 · 6일차'), ('D' + raw.day.astype(str)).to_numpy()


def snap_table(path) -> tuple[Table, np.ndarray]:
    raw = pd.read_csv(path).sort_values(['snap_type', 'assembler', 'curve_id']).reset_index(drop=True)
    raw['assembly_ng'] = (~raw.ok.astype(bool)).astype(float)
    df = raw.drop(columns=['ok', 'snap_type', 'assembler', 'group'])
    return Table('pf_snap', '스냅핏 삽입 (캔틸레버 · 환형)', df, _meta(df), {}, '', {}, 'row', 'Zenodo 1209203 (CC BY 4.0)'), raw.group.to_numpy()


GROUP_KO = {'pf_delta': '작업일', 'pf_snap': '스냅 종류 · 조립 주체', 'j_screw': '실험 세트', 'j_insert': '삽입 위치', 'j_collar': '공구 형상', 'j_crimp': '전선 단면'}


# ── MCT + 압입: 따로 · 같은 공정군 합침 · 두 공정 합침
def run(mct_dir, pf_dirs, out_dir: Path, screw_label: str = 'faulty') -> dict:
    import json
    from .engine import auc
    from .joining import load_tables
    from .mct import RULES, SHORT, hmotp_table, nasa_table, stiehl_table, tool_cv, _a, _p
    mct_dir = Path(mct_dir)
    pf_dirs = [Path(x) for x in ([pf_dirs] if isinstance(pf_dirs, (str, Path)) else pf_dirs)]
    mct = {'mct_hmotp': hmotp_table(mct_dir / 'hmotp_cuts.csv')[0], 'mct_nasa': nasa_table()[0]}
    if (mct_dir / 'stiehl_runs.csv').exists():
        mct['mct_stiehl'] = stiehl_table(mct_dir / 'stiehl_runs.csv')[0]
    pf, G, comp = {}, {}, {}
    for pf_dir in pf_dirs:
        if (pf_dir / 'pf_delta.csv').exists():
            pf['pf_delta'], G['pf_delta'] = delta_table(pf_dir / 'pf_delta.csv')
        if (pf_dir / 'pf_snap.csv').exists():
            pf['pf_snap'], G['pf_snap'] = snap_table(pf_dir / 'pf_snap.csv')
        T, GG, R = load_tables(pf_dir, screw_label)
        pf.update(T); G.update(GG)
        if 'j_screw' in R and screw_label == 'faulty':     # 체결기 자체 판정(NOK)이 이상 조건을 얼마나 잡나
            r = R['j_screw']; comp['j_screw'] = ('체결기 판정 NOK', auc(r.nok.to_numpy(float), r.join_ng.to_numpy()))
        if 'j_crimp' in R:
            r = R['j_crimp'].dropna(subset=['cfm_ng']); comp['j_crimp'] = ('상용 압착 모니터', auc(r.cfm_ng.to_numpy(float), r.join_ng.to_numpy()))
    fam_m = tool_cv(mct, 'sensor')
    fam_p = tool_cv(pf, 'sensor', groups=G)
    both = tool_cv({**mct, **pf}, 'sensor', groups=G)
    from .local_inputs import local_cv               # 엔진 보강: 공정 전용 입력 (곡선 공정은 양쪽 벗어남까지)
    plus = {k: local_cv(t, G.get(k), two_sided=k not in mct) for k, t in {**mct, **pf}.items()}
    res = {}
    for k in list(mct) + list(pf):
        fam = fam_m if k in mct else fam_p
        res[k] = {'name': fam[k]['name'], 'groups': fam[k]['tools'], 'rows': fam[k]['rows'], 'events': fam[k]['events'],
                  'unit': '공구' if k in mct else GROUP_KO.get(k, '묶음'), 'comparator': comp.get(k),
                  'rule': fam[k]['rule'], 'local': fam[k]['local'], 'family': fam[k]['joint'], 'both': both[k]['joint'], 'plus': plus[k],
                  'folds': [{'group': a['tool'], 'local': a['local'], 'family': a['joint'], 'both': b['joint']} for a, b in zip(fam[k]['folds'], both[k]['folds'])]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'mct_pressfit.json').write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    L = ['# MCT + 압입 — 따로 만든 모델 vs 합친 모델', '',
         '같은 조건: 묶음(공구 · 작업일 · 스냅 종류)을 통째로 빼고 학습 → 뺀 묶음으로 평가 · 입력 = 센서만 · 값 = 위험도 상위 10% 알림 안의 불량 비율 (AUC).',
         '따로 = 그 데이터만 · 같은 공정 합침 = 같은 공정의 다른 데이터까지 · MCT + 압입 합침 = 두 공정 전부 한 모델 (데이터별 보정).', '',
         '엔진 보강 = 따로 + 그 공정 원본 칼럼을 엔진 슬롯으로 (압입 계열은 곡선 5구간 · 양쪽 벗어남 포함, 부스팅 없음).', '',
         '| 공정 | 데이터 | 묶음 · 행 · 불량 | 규칙 | 따로 | 같은 공정 합침 | MCT + 압입 합침 | 엔진 보강 |', '|---|---|---|---|---|---|---|---|']
    
    for k, x in res.items():
        L.append(f"| {'MCT' if k in mct else '압입 계열'} | {x['name']} | {x['groups']} {x['unit']} · {x['rows']} · {x['events']} | "
                 + ' | '.join(f"{_p(x[m]['capture'])} ({_a(x[m]['auc'])})" for m in ('rule', 'local', 'family', 'both', 'plus')) + ' |')
    if comp:
        L += ['', '## 현장 장비 자체 판정과 비교 (AUC)', ''] + [f"- {res[k]['name']}: {n} {_a(v)} · MILG 따로 {_a(res[k]['local']['auc'])}" for k, (n, v) in comp.items()]
    L += ['', '## 묶음별 AUC (따로 → MCT + 압입 합침)', '']
    for k, x in res.items():
        L.append(f"- {x['name']}: " + ' · '.join(f"{f['group']} {_a(f['local'])}→{_a(f['both'])}" for f in x['folds']))
    L += ['', '정답 규칙: ' + ' · '.join(f"{SHORT.get(k, k)} {RULES[k]}" for k in mct) + ' · 압입 = 라벨 OK 아님(Delta 3 · 4일차 라벨 무효로 제외, 스냅핏 라벨 0 = 실패로 봄)'
          + f" · 스크류 체결 = {'실험으로 넣은 이상 조건(faulty)' if screw_label == 'faulty' else '체결기 판정 NOK'} · 링 삽입 = 위치 오차를 준 시도 · 버링 = 가장자리 균열 6개 이상 · 압착 = 전문가 라벨 불량."]
    (out_dir / 'mct_pressfit.md').write_text('\n'.join(L) + '\n', encoding='utf-8')
    return res
