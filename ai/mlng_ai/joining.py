"""힘 · 토크 곡선 공정 (압입 계열) 추가 데이터 — 압축 파일을 풀지 않고 바로 읽어 «곡선 1개 = 한 행» 요약 CSV 를 만든다.

  스크류 체결 s01~s06   PyScrew (West 외, Zenodo, CC BY 4.0) — 토크 · 각도 곡선 34,082개
                        정답: scenario_condition = faulty (실험으로 넣은 이상 조건 — 나사산 손상 · 윤활 · 와셔 · 소재 등).
                        체결기 자체 판정(workpiece_result NOK)은 같은 곡선의 최종 토크 · 각도로 정해지므로 비교용 칼럼으로만 남긴다.
  링 삽입               Copper Rings Insertion (Zenodo 6364950, CC BY 4.0) — 로봇 손목 힘 · 토크 300회.
                        정답: 위치 오차를 일부러 준 시도(케이스 ≠ 0) = 조건 이상.
  버링(칼라) 성형        Punching-Collar Forming (2단 프레스, 216개) — 펀칭 · 칼라 성형 힘-변위 곡선.
                        정답: 가장자리 균열 수 ≥ 6 (상위 약 1/3).
  압착(크림프)           Crimp Force Curve Dataset (Harvard Dataverse, CC0) — 2,439개, 전문가 라벨.
                        상용 압착 모니터(CFM) 판정은 비교용 칼럼으로만 남긴다.
                        pickle 은 pandas · numpy 객체만 허용하는 방식으로 읽는다 (임의 코드 실행 차단).

  python -m mlng_ai joining <압입 폴더> <요약 폴더>
"""
from __future__ import annotations

import io
import json
import pickle
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .datasets import Table
from .pressfit import curve_features

SCREW_LABEL = 'faulty'      # faulty · nok
CRACKS_NG = 6


# ── 스크류 체결
def _screw_one(raw: bytes) -> dict:
    j = json.loads(raw)
    tq, an, gr = [], [], []
    for st in j.get('tightening steps', []):
        g = st.get('graph') or {}
        tq += g.get('torque values', []); an += g.get('angle values', []); gr += g.get('gradient values', [])
    r = {}
    r.update(curve_features(np.array(tq, float), 'torque'))
    r.update(curve_features(np.array(gr, float), 'gradient'))
    a = np.array(an, float)
    if len(a) and len(tq):
        r['angle_at_torque_peak'] = float(a[int(np.argmax(tq))])
        r['angle_final'] = float(a[-1])
    return r


def summarize_screw(src: Path, dst: Path) -> Path:
    out = dst / 'join_screw.csv'
    done = set(pd.read_csv(out, usecols=['scenario']).scenario) if out.exists() else set()
    for z in sorted(src.glob('s0*_*.zip')):
        sc = z.stem.split('_')[0]
        if sc in done:
            print(f'  {sc} 이미 요약 — 건너뜀')
            continue
        t0 = time.time()
        Z = zipfile.ZipFile(z)
        names = {n.rsplit('/', 1)[-1]: n for n in Z.namelist() if n.endswith('.json')}
        lab = pd.read_csv(io.BytesIO(Z.read([n for n in Z.namelist() if n.endswith('labels.csv')][0])))
        rows = []
        for _, L in lab.iterrows():
            n = names.get(L.file_name)
            if not n:
                continue
            r = {'curve_id': f'{sc}-{L.run_id}', 'scenario': sc, 'class_value': L.class_value, 'faulty': int(L.scenario_condition == 'faulty'),
                 'nok': int(L.workpiece_result == 'NOK'), 'workpiece_usage': L.workpiece_usage}
            r.update(_screw_one(Z.read(n)))
            rows.append(r)
        pd.DataFrame(rows).to_csv(out, mode='a', header=not out.exists(), index=False)
        print(f'  {sc} {len(rows)}개 · {time.time() - t0:.0f}초', flush=True)
    return out


# ── 링 삽입
def summarize_insertion(zp: Path, dst: Path) -> Path:
    Z = zipfile.ZipFile(zp)
    rows = []
    for n in Z.namelist():
        p = n.split('/')
        if len(p) < 4 or not p[-1].startswith('ft_') or p[-1].startswith('ft_tool'):
            continue
        d = pd.read_csv(io.BytesIO(Z.read(n)), header=None).to_numpy(float)
        r = {'curve_id': p[-1][3:-4], 'slot': f'slot{p[1]}', 'case': p[2], 'misaligned': int(p[2] != '0')}
        for k, c in enumerate(['force_x', 'force_y', 'force_z', 'torque_x', 'torque_y', 'torque_z'][:d.shape[1]]):
            r.update(curve_features(d[:, k], c))
        rows.append(r)
    out = dst / 'join_insertion.csv'
    pd.DataFrame(rows).to_csv(out, index=False)
    return out


# ── 버링(칼라) 성형
def summarize_collar(zp: Path, dst: Path) -> Path:
    Z = zipfile.ZipFile(zp)
    names = Z.namelist()
    meta = pd.read_excel(io.BytesIO(Z.read([n for n in names if n.endswith('.xlsx')][0])))
    files = {n.rsplit('/', 1)[-1][:-4]: n for n in names if n.endswith('.csv')}
    rows = []
    for _, m in meta.iterrows():
        r = {'curve_id': m.sample_id, 'group': f'{m.tool_geometry}·{m.cutting_edge_radius}', 'cracks': int(m.number_cracks), 'sheet_thickness': m.sheet_thickness_actual}
        for stage, key in (('punch', m.punching_id), ('collar', m.collar_forming_id)):
            if key in files:
                d = pd.read_csv(io.BytesIO(Z.read(files[key])))
                r.update(curve_features(d['Y'].to_numpy(float), f'{stage}_force'))
                r[f'{stage}_stroke'] = float(d['X'].max() - d['X'].min())
        rows.append(r)
    out = dst / 'join_collar.csv'
    pd.DataFrame(rows).to_csv(out, index=False)
    return out


# ── 압착 (pickle: pandas · numpy 객체만 허용)
class _SafeUnpickler(pickle.Unpickler):
    OK = {'slice', 'list', 'dict', 'tuple', 'set', 'frozenset', 'int', 'float', 'str', 'bytes', 'complex', 'bool', 'range', 'object'}

    def find_class(self, module, name):
        if module.split('.')[0] in ('pandas', 'numpy') or (module == 'builtins' and name in self.OK):
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f'허용하지 않는 객체: {module}.{name}')


def summarize_crimp(zp: Path, dst: Path) -> Path:
    Z = zipfile.ZipFile(zp)
    d = _SafeUnpickler(Z.open([n for n in Z.namelist() if n.endswith('.pkl')][0])).load()
    rows = []
    for _, x in d.iterrows():
        r = {'curve_id': f'C{int(x.CrimpID)}', 'cross_section': f"{x['Wire_cross-section_conductor']:g}mm2", 'ng': int(x.Binary_label_encoded),
             'cfm_ng': int(x.CFM_label_encoded) if pd.notna(x.CFM_label_encoded) else np.nan, 'sub_label': x.Sub_label_string}
        r.update(curve_features(np.asarray(x.Force_curve_RoI, float), 'force'))
        rows.append(r)
    out = dst / 'join_crimp.csv'
    pd.DataFrame(rows).to_csv(out, index=False)
    return out


def summarize(src, dst) -> list[Path]:
    src, dst = Path(src), Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    out = []
    print('스크류 체결'); out.append(summarize_screw(src, dst))
    ins = next(src.rglob('insertion-dataset.zip'), None)
    if ins:
        print('링 삽입'); out.append(summarize_insertion(ins, dst))
    col = next(src.rglob('collar_forming*.zip'), None)
    if col:
        print('버링 성형'); out.append(summarize_collar(col, dst))
    cr = next(src.rglob('dataverse_files.zip'), None)
    if cr:
        print('압착'); out.append(summarize_crimp(cr, dst))
    if any(src.glob('synthetic_data.csv')):
        print('synthetic_data.csv — 정답(불량) 칼럼이 없어 학습에서 제외')
    return out


# ── 엔진 표
KO = {'torque': ('체결 토크', 'N·m'), 'gradient': ('토크 기울기', ''), 'force_x': ('힘 X', 'N'), 'force_y': ('힘 Y', 'N'), 'force_z': ('힘 Z', 'N'),
      'torque_x': ('토크 X', 'N·m'), 'torque_y': ('토크 Y', 'N·m'), 'torque_z': ('토크 Z', 'N·m'), 'punch_force': ('펀칭 힘', 'kN'), 'collar_force': ('칼라 성형 힘', 'kN'),
      'force': ('압착 힘', 'N')}
ST = {'max': '최대', 'min': '최소', 'mean': '평균', 'std': '표준편차', 'end': '마지막 구간 평균', 'peakpos': '최대 위치 (전체 대비)', 'slope': '최대 기울기',
      'slopesd': '기울기 표준편차', 'npeaks': '큰 봉우리 수', 'area': '곡선 평균 높이',
      **{f'seg{i}': f'구간 {i}/5 평균' for i in range(1, 6)}, **{f'segslope{i}': f'구간 {i}/5 기울기' for i in range(1, 6)}}


def _meta(df, extra=None):
    m = {'join_ng': {'label': '불량 · 이상 조건'}, 'curve_id': {'label': '곡선 번호'}}
    for c in df.columns:
        k, _, s = c.rpartition('_')
        if k in KO and s in ST:
            m[c] = {'label': f'{KO[k][0]} {ST[s]}', 'unit': '' if s in ('peakpos', 'npeaks') or s.startswith('segslope') else KO[k][1]}
    m.update(extra or {})
    return m


def _mk(key, name, raw, label_col, group_col, drop, source, extra=None):
    raw = raw.sort_values([group_col, 'curve_id']).reset_index(drop=True)
    raw['join_ng'] = raw[label_col].astype(float)
    df = raw.drop(columns=[c for c in drop + [label_col, group_col] if c in raw])
    return Table(key, name, df, _meta(df, extra), {}, '', {}, 'row', source), raw[group_col].astype(str).to_numpy(), raw


def load_tables(d: str | Path, screw_label: str = SCREW_LABEL) -> tuple[dict, dict, dict]:
    d = Path(d)
    T, G, R = {}, {}, {}
    if (d / 'join_screw.csv').exists():
        T['j_screw'], G['j_screw'], R['j_screw'] = _mk('j_screw', '스크류 체결 (PyScrew s01~s06)', pd.read_csv(d / 'join_screw.csv'), screw_label, 'scenario',
                                                       ['class_value', 'faulty', 'nok', 'workpiece_usage'], 'PyScrew (CC BY 4.0)',
                                                       {'angle_at_torque_peak': {'label': '최대 토크 때 회전 각도', 'unit': '°'}, 'angle_final': {'label': '최종 회전 각도', 'unit': '°'}})
    if (d / 'join_insertion.csv').exists():
        T['j_insert'], G['j_insert'], R['j_insert'] = _mk('j_insert', '링 삽입 (로봇 · 위치 오차)', pd.read_csv(d / 'join_insertion.csv'), 'misaligned', 'slot', ['case'], 'Zenodo 6364950 (CC BY 4.0)')
    if (d / 'join_collar.csv').exists():
        raw = pd.read_csv(d / 'join_collar.csv')
        raw['crack_ng'] = (raw.cracks >= CRACKS_NG).astype(int)
        T['j_collar'], G['j_collar'], R['j_collar'] = _mk('j_collar', '버링 성형 (펀칭 → 칼라)', raw, 'crack_ng', 'group', ['cracks'], 'Punching-Collar Forming dataset',
                                                          {'sheet_thickness': {'label': '판 두께', 'unit': 'mm'}, 'punch_stroke': {'label': '펀칭 행정', 'unit': 'mm'}, 'collar_stroke': {'label': '칼라 성형 행정', 'unit': 'mm'}})
    if (d / 'join_crimp.csv').exists():
        T['j_crimp'], G['j_crimp'], R['j_crimp'] = _mk('j_crimp', '압착 (크림프 · 전선 단면 2종)', pd.read_csv(d / 'join_crimp.csv'), 'ng', 'cross_section', ['cfm_ng', 'sub_label'], 'Harvard Dataverse WBDKN6 (CC0)')
    return T, G, R
