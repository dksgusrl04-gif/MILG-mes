"""사출 · 프레스 · 용접 기본 모델 — 학습 팩(공개 데이터) 중 «불량 · 교체 임박 정답이 있는» 것만 골라 MILG 엔진에 학습한다.

  python -m mlng_ai packs-summarize <학습 팩 폴더> <요약 폴더>     원본 zip → 한 행 = 한 사이클(스트로크 · 용접 구간) 요약 CSV (이어서 실행 가능)
  python -m mlng_ai packs <요약 폴더>                             검증(묶음을 통째로 빼고 평가) → reports/packs.md · state/base/<공정>_engine.json · MES 화면

데이터 (모두 공개 · 정답 있음)
  사출   inj_quality    Injection-Molding Production Data with Quality Labels (B2SHARE · CC BY 4.0) — 564 사이클 · 실험 30개 · NOK 라벨
         inj_polenta    Polenta 외 (AIRTLab) 사출 품질 4등급 — 1,451 사이클 · 등급 1(폐기)을 불량으로 봄 (등급 뜻은 논문 순서에 따른 가정)
                        파일이 라벨 순으로 정렬돼 있고 시간 · LOT 이 없어 «묶음을 통째로 빼는» 검증이 안 된다 → 수치는 참고 · 적용 보류
  프레스 press_fineblank  Fine-blanking Punch Force Strokes with Wear assessment (RWTH) — 실험 4개 · 스트로크별 펀치 힘 곡선 · 마모 4시점
                          마모(0 = 새 펀치 · 1 = 실험 끝)는 4시점만 재서 사이를 직선으로 잇는다 → 마모 ≥ 0.8 = 교체 임박
  용접   weld_arc       Predictive Quality Arc Welding (Zenodo 15497262 · CC BY 4.0) — 전류 · 전압 200점 구간 · 라벨 0 = 불량 · 1 = 양호 · -1 = 없음
         weld_rsw       Resistance Spot Welding Insights (Mendeley rwh8kjzdch · CC BY 4.0) — 495 타점 · Good / Bad / Explode

검증: 묶음(실험 · 조건 · 시간 구간)을 통째로 빼고 학습 → 뺀 묶음으로 평가. 사용량(스트로크 수)은 입력에서 뺀다. 결과 측정값(무게 · 치수 · 인장 · 너깃)도 뺀다.
비교: 규칙(최대 이탈) · MILG 엔진(공통 슬롯) · MILG 엔진 + 공정 전용 입력 · 범용 부스팅(비교 기준 — 엔진 계산에 넣지 않는다).
"""
from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .datasets import Table

HERE = Path(__file__).resolve().parent.parent
WEB = HERE.parent / 'web' / 'data'
SEG = 5
WEAR_LIMIT = 0.8          # 정규화 마모(0 = 새 펀치 · 1 = 실험 끝) — 80% 를 교체 임박으로 (MCT Stiehl 과 같은 규칙)
ARC_EVERY = 10            # 용접 구간은 10개 중 1개만 요약 (이웃 구간은 거의 같은 신호)
GATE = 0.75               # 적용 기준: 엔진 구분력(AUC)
ST_KO = {'max': '최대', 'min': '최소', 'mean': '평균', 'std': '표준편차', 'end': '마지막 구간 평균', 'peakpos': '최대 위치 (전체 대비)', 'slope': '최대 기울기',
         'slopesd': '기울기 표준편차', 'area': '곡선 평균 높이', **{f'seg{i}': f'구간 {i}/5 평균' for i in range(1, 6)}, **{f'segslope{i}': f'구간 {i}/5 기울기' for i in range(1, 6)}}
CH_KO = {'force': ('펀치 힘', 'N'), 'cur': ('용접 전류', 'A'), 'volt': ('용접 전압', 'V'), 'pow': ('용접 전력', 'W'), 'inj_prs': ('사출 압력', 'bar'), 'cav_prs': ('금형 내압', 'bar'),
         'inj_vel': ('사출 속도', 'mm/s'), 'rsw_force': ('가압력', 'N'), 'rsw_cur': ('용접 전류', 'A')}

# 학습에 쓰지 않은 것 — 이유
SKIPPED = [
    ('사출', 'Production Data (d-optimal DoE)', '정상 303건뿐 (NOK 0)', '보류'),
    ('사출', 'Foxconn 사출 치수', '사용 조건(라이선스) 미확인', '보류'),
    ('사출', 'Uni Kassel 사출 + 체결 연계', '조건 변경 실험 — 불량 라벨 아님 (설정값이 곧 정답)', '보류'),
    ('사출', 'SKZ 점도 센서', '68건 · 불량 라벨 없음', '부적합'),
    ('사출', 'AD-STGN (CMMS)', '칼럼 이름 익명 · 정답은 시험 111건뿐', '부적합'),
    ('사출', 'RWTH 재생 PP · IMM · Preform · Mendeley DataModel', '제어 실험 곡선 · 시뮬레이션 27건 · 머리글만 — 불량 라벨 없음', '부적합'),
    ('프레스', 'RDDAC (DaRUS 2026)', '분류값이 공정 조건 18종 (불량 아님)', '부적합'),
    ('프레스', 'Deep drawing 힘 곡선 (합성 요건)', '조건당 기록 1개 — 통째로 빼는 검증 불가', '보류'),
    ('프레스', 'Fine-blanking DiCo · Stuttgart 시뮬레이션 · Air bending · Tube bending', '회귀 · 시뮬레이션 · 스프링백 — 불량 라벨 없음', '부적합'),
    ('용접', 'RSW 차체 (GitHub)', 'CC BY-NC 4.0 — 상용 학습 불가', '제외'),
    ('용접', '레이저 배터리 탭 (포토다이오드)', '약 150건 · 원신호 rar', '보류'),
    ('용접', '레이저 맞대기 (LWIR)', '열화상 프레임 라벨 — 영상 모델 필요', '보류'),
    ('용접', 'GMAW 비드 형상 2종 · CMT 프로파일 · RSW benchmark · Refill FSSW', '형상 회귀 · 코드만 · 7z (미확인)', '부적합'),
]


# ── 곡선 묶음 요약 (행 = 곡선 1개, 벡터 계산)
def block(A: np.ndarray, key: str) -> dict:
    """A (n × 길이) → 곡선마다 최대 · 최소 · 평균 · 표준편차 · 끝 구간 · 최대 위치 · 기울기 · 5구간 평균 · 5구간 기울기"""
    A = np.asarray(A, float)
    n, L = A.shape
    d = np.diff(A, axis=1)
    r = {f'{key}_max': A.max(1), f'{key}_min': A.min(1), f'{key}_mean': A.mean(1), f'{key}_std': A.std(1), f'{key}_end': A[:, -max(1, L // 20):].mean(1),
         f'{key}_peakpos': A.argmax(1) / (L - 1), f'{key}_slope': np.abs(d).max(1), f'{key}_slopesd': d.std(1), f'{key}_area': A.mean(1)}
    for i, ix in enumerate(np.array_split(np.arange(L), SEG), 1):
        S = A[:, ix]
        x = np.arange(len(ix)) - (len(ix) - 1) / 2
        r[f'{key}_seg{i}'] = S.mean(1)
        r[f'{key}_segslope{i}'] = (S * x).sum(1) / (x * x).sum()
    return r


def _inner(pack_dir: Path, name: str, tmp: Path) -> Path | None:
    """학습 팩 폴더(또는 그 안의 묶음 zip) 에서 name 으로 끝나는 파일을 찾아 돌려준다 — zip 안에 있으면 tmp 로 꺼낸다"""
    for root in (tmp, pack_dir):                      # 이미 꺼내 둔 것 먼저
        for p in sorted(root.rglob(name)):
            if p.is_file():
                return p
    for z in sorted(pack_dir.rglob('*.zip')):
        try:
            with zipfile.ZipFile(z) as f:
                hit = [i for i in f.infolist() if i.filename.split('/')[-1] == name]
                if hit:
                    return Path(f.extract(hit[0], tmp))
        except zipfile.BadZipFile:
            continue
    return None


# ── 사출
INJ_CURVES = {'DXP_Inj1PrsAct': 'inj_prs', 'DXP_MldCavPrs1Act': 'cav_prs', 'DXP_Inj1VelAct': 'inj_vel'}
INJ_KEEP = ('ENV_', 'SET_', 'QUA_', 'CALC_')          # 공정 · 설비 값만. 무게(SCA) · 치수(CV) · 열화상(IR) · 시뮬레이션(SIM) = 결과 쪽이라 뺀다


def summarize_inj_quality(pack: Path, dst: Path, tmp: Path) -> Path | None:
    out = dst / 'inj_quality.csv'
    z = _inner(pack, 'Injection-Molding Production Data with Quality Labels.zip', tmp)
    if out.exists() or z is None:
        return out if out.exists() else None
    with zipfile.ZipFile(z) as f:
        t = pd.read_parquet(io.BytesIO(f.read([n for n in f.namelist() if n.endswith('.parquet')][0])))
    t = t.sort_values('MET_TimeCycleStart').reset_index(drop=True)
    d = pd.DataFrame({'cycle_id': t.MET_JobCycleID.astype(str), 'experiment': t.MET_ExperimentNumber.astype(str), 'material': t.MET_MaterialName.astype(str), 'part_ng': t.LBL_NOK.astype(float)})
    for c in t.columns:
        if c.startswith(INJ_KEEP) and t[c].dtype.kind in 'fiu':
            d[c] = t[c].to_numpy(float)
    for c, k in INJ_CURVES.items():
        if c in t and t[c].dtype == object:
            rows = [np.asarray(v, float) if v is not None else np.array([]) for v in t[c]]
            L = int(np.median([len(v) for v in rows if len(v)]))
            A = np.array([np.interp(np.linspace(0, 1, 100), np.linspace(0, 1, len(v)), np.nan_to_num(v)) if len(v) >= 10 else np.full(100, np.nan) for v in rows]) if L >= 10 else None
            if A is not None:
                for kk, v in block(A, k).items():
                    d[kk] = v
    d.to_csv(out, index=False)
    return out


def summarize_inj_polenta(pack: Path, dst: Path, tmp: Path) -> Path | None:
    out = dst / 'inj_polenta.csv'
    if out.exists():
        return out
    src = next((p for p in pack.rglob('*.csv') if 'iGuzzini' in p.name or 'Polenta' in p.name), None)
    if src is None:
        for z in sorted(pack.rglob('*.zip')):
            with zipfile.ZipFile(z) as f:
                hit = [i for i in f.infolist() if 'iGuzzini' in i.filename or 'Polenta' in i.filename]
                if hit:
                    src = Path(f.extract(hit[0], tmp)); break
    if src is None:
        return None
    d = pd.read_csv(src, sep=';')
    d.columns = [c.split(' - ')[-1].strip() for c in d.columns]
    d.insert(0, 'cycle_id', [f'P{i:04d}' for i in range(len(d))])
    d.to_csv(out, index=False)
    return out


# ── 프레스: 파인 블랭킹 펀치 힘 (pandas split 형식 JSON · 수백 MB → 한 줄씩 흘려 읽기)
def _json_rows(fh, chunk: int = 1 << 23):
    """«data»:[[…],[…]] 의 행을 글자 그대로 하나씩 (숫자 변환은 쓰는 쪽에서 — 건너뛰는 행은 변환하지 않는다)"""
    buf, started = '', False
    while True:
        b = fh.read(chunk)
        if not b:
            break
        buf += b.decode('utf-8', 'replace')
        if not started:
            m = re.search(r'"data"\s*:\s*\[', buf)
            if not m:
                buf = buf[-16:]
                continue
            buf, started = buf[m.end():], True
        pos = 0
        while True:
            a = buf.find('[', pos)
            e = buf.find(']', a + 1) if a >= 0 else -1
            if a < 0 or e < 0:
                break
            yield buf[a + 1:e]
            pos = e + 1
        buf = buf[pos:]


def _nums(s: str) -> np.ndarray:
    v = np.fromstring(s, sep=',')
    if len(v) != s.count(',') + 1:                     # null 이 섞인 행
        v = np.array([np.nan if x.strip() in ('null', 'NaN', '') else float(x) for x in s.split(',')], float)
    return v


def summarize_press_fineblank(pack: Path, dst: Path, tmp: Path, every: int = 5) -> Path | None:
    """실험마다 표 하나 — 열 = 스트로크 · 행 = 스트로크 안의 시각. 시각은 every 개 중 1개만 읽고, 스트로크마다 힘 곡선을 요약한다"""
    out = dst / 'press_fineblank.csv'
    if out.exists():
        return out
    z = _inner(pack, 'Series of Time Series representing Fine-blanking Punch Force Strokes with Wear assessment.zip', tmp)
    if z is None:
        return None
    parts = []
    with zipfile.ZipFile(z) as f:
        wear = json.loads(f.read('E_Wear_AVG.json'))
        for e in sorted(wear):
            ex = e.split('_')[0]
            part = dst / f'press_fineblank_{ex}.csv'
            if not part.exists():
                with f.open(f'{ex}_punch_force.json') as fh:
                    rows = [np.nan_to_num(_nums(v)).astype(np.float32) for i, v in enumerate(_json_rows(fh)) if i % every == 0]
                A = np.array(rows).T                          # 스트로크 × 시각
                d = pd.DataFrame({'stroke_id': [f'{ex}-{i:05d}' for i in range(len(A))], 'tool_id': ex, 'use_count': np.arange(len(A)), **block(A, 'force')})
                d['wear'] = np.interp(d.use_count, wear[e]['Index'], wear[e]['AVG'])     # 마모는 4시점만 재서 사이를 직선으로 잇는다
                d.to_csv(part, index=False)
            parts.append(pd.read_csv(part))
    pd.concat(parts).to_csv(out, index=False)
    return out


# ── 용접
def summarize_weld_arc(pack: Path, dst: Path, tmp: Path, budget_s: float = 1e9) -> Path | None:
    """전류 · 전압 200점 구간 → 요약. 2.7 GB 라 이어서 실행할 수 있게 위치를 적어 둔다 (끝나면 weld_arc.csv)"""
    out, part, state = dst / 'weld_arc.csv', dst / 'weld_arc.part.csv', dst / 'weld_arc.state.json'
    if out.exists():
        return out
    src = _inner(pack, 'welding_data.csv', tmp)
    if src is None:
        z = _inner(pack, 'Metal Arc Welding.zip', tmp)
        src = _inner(z.parent, 'welding_data.csv', tmp) if z else None
    if src is None:
        return None
    st = json.loads(state.read_text()) if state.exists() else {'offset': 0, 'row': 0}
    t0 = time.time()
    with open(src, 'rb') as fh:
        hdr = fh.readline().decode().strip().split(',')
        if st['offset']:
            fh.seek(st['offset'])
        ic, vc = [i for i, c in enumerate(hdr) if c.startswith('I_')], [i for i, c in enumerate(hdr) if c.startswith('V_')]
        while time.time() - t0 < budget_s:
            lines = fh.readlines(64 << 20)
            if not lines:
                d = pd.read_csv(part)
                d.to_csv(out, index=False); part.unlink(); state.unlink()
                return out
            keep = [ln for j, ln in enumerate(lines, st['row']) if j % ARC_EVERY == 0 and not ln.startswith(b'-1,')]
            if keep:
                A = np.loadtxt(io.BytesIO(b''.join(keep)), delimiter=',', ndmin=2)
                I, V = A[:, ic], A[:, vc]
                d = pd.DataFrame({'label': A[:, 0].astype(int), 'exp_id': A[:, 1].astype(int), 'run_id': A[:, 2].astype(int), **block(I, 'cur'), **block(V, 'volt'), **{k: v for k, v in block(I * V, 'pow').items() if k.endswith(('_mean', '_std', '_max'))}})
                d['short_frac'] = (V < 0.5 * np.median(V, axis=1, keepdims=True)).mean(1)        # 전압이 꺼지는(단락) 비율
                d.to_csv(part, mode='a', header=not part.exists(), index=False)
            st = {'offset': fh.tell(), 'row': st['row'] + len(lines)}
            state.write_text(json.dumps(st))
    return None


def summarize_weld_rsw(pack: Path, dst: Path, tmp: Path) -> Path | None:
    out = dst / 'weld_rsw.csv'
    if out.exists():
        return out
    z = _inner(pack, 'Resistance Spot Welding Insights A Dataset Integrating Process Parameters, Infrared, and Surface Imaging.zip', tmp)
    if z is None:
        return None
    with zipfile.ZipFile(z) as f:
        d = pd.read_csv(io.BytesIO(f.read([n for n in f.namelist() if n.endswith('.csv')][0])), encoding='utf-8-sig')
    d.columns = [c.strip() for c in d.columns]
    rows = []
    for sid, g in d.groupby('Sample ID', sort=True):
        a = g.iloc[0]
        F, C = g['Force (N)'].to_numpy(float), g['Current (A)'].to_numpy(float)
        rows.append({'sample_id': f'S{int(sid):04d}', 'pressure_psi': a['Pressure (PSI)'], 'weld_time_ms': a['Welding Time (ms)'], 'angle_deg': a['Angle (Deg)'],
                     'thickness_a': a['Thickness A (mm)'], 'thickness_b': a['Thickness B (mm)'], 'category': str(a['Category']),
                     'rsw_force_max': F.max(), 'rsw_force_mean': F.mean(), 'rsw_force_std': F.std(), 'rsw_force_end': F[-1],
                     'rsw_cur_max': C.max(), 'rsw_cur_mean': C.mean(), 'rsw_cur_std': C.std(), 'rsw_cur_end': C[-1]})
    pd.DataFrame(rows).to_csv(out, index=False)
    return out


SUMMARIZERS = {'사출': [summarize_inj_quality, summarize_inj_polenta], '프레스': [summarize_press_fineblank], '용접': [summarize_weld_rsw, summarize_weld_arc]}


def summarize(pack_dir, dst, only: str | None = None, budget_s: float = 1e9) -> list[Path]:
    pack_dir, dst = Path(pack_dir), Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    tmp = dst / '_tmp'                                 # 꺼낸 원본 (이어서 실행할 때 다시 쓰고, 끝나면 지워도 된다)
    tmp.mkdir(exist_ok=True)
    done = []
    for proc, fns in SUMMARIZERS.items():
        src = pack_dir / proc if (pack_dir / proc).is_dir() else pack_dir
        for fn in fns:
            if only and only not in (proc, fn.__name__):
                continue
            p = fn(src, dst, tmp, budget_s) if fn is summarize_weld_arc else fn(src, dst, tmp)
            if p:
                done.append(p)
    return done


# ── 표 (엔진 입력) + 묶음
def _curve_meta(df: pd.DataFrame) -> dict:
    m = {}
    for c in df.columns:
        for k, (ko, unit) in CH_KO.items():
            if c.startswith(k + '_') and c[len(k) + 1:] in ST_KO:
                m[c] = {'label': f'{ko} {ST_KO[c[len(k) + 1:]]}', 'unit': '' if c.endswith('peakpos') else unit}
    return m


def _blocks(n: int, k: int = 5) -> np.ndarray:
    return np.array([f'구간{i + 1}' for i in np.arange(n) * k // n])


def load_tables(d) -> dict:
    """요약 폴더 → {키: (표, 묶음, 설명)} — 묶음은 통째로 빼는 단위"""
    d, T = Path(d), {}
    if (d / 'inj_quality.csv').exists():
        r = pd.read_csv(d / 'inj_quality.csv')
        df = r.drop(columns=['experiment', 'material'])
        T['inj_quality'] = (Table('inj_quality', '사출 · 생산 데이터 (품질 라벨)', df, {'part_ng': {'label': '성형 불량'}, 'cycle_id': {'label': '사이클 번호'}, **_curve_meta(df)}, {}, '', {}, 'row', 'B2SHARE k0v7s-jf859 (CC BY 4.0)'),
                            r.experiment.to_numpy(), dict(proc='사출', unit='실험', y='part_ng', label='NOK 라벨', lic='CC BY 4.0', two=True))
    if (d / 'inj_polenta.csv').exists():
        r = pd.read_csv(d / 'inj_polenta.csv')
        r['part_ng'] = (r.quality == 1).astype(float)
        df = r.drop(columns=['quality'])
        T['inj_polenta'] = (Table('inj_polenta', '사출 · 품질 4등급 (AIRTLab)', df, {'part_ng': {'label': '성형 불량'}, 'cycle_id': {'label': '사이클 번호'}}, {}, '', {}, 'row', 'Polenta 외 2022 (AIRTLab)'),
                            _blocks(len(r)), dict(proc='사출', unit='행 구간', y='part_ng', label='등급 1 (폐기) — 등급 뜻은 가정', lic='미확인', two=True,
                                                   hold='묶음 정보 없음 (라벨 순 정렬 · 시간 · LOT 없음)'))
    if (d / 'press_fineblank.csv').exists():
        r = pd.read_csv(d / 'press_fineblank.csv').sort_values(['tool_id', 'use_count']).reset_index(drop=True)
        r['tool_ng'] = (r.wear >= WEAR_LIMIT).astype(float)
        df = r.drop(columns=['wear'])
        T['press_fineblank'] = (Table('press_fineblank', '프레스 · 파인 블랭킹 펀치', df, {'tool_ng': {'label': f'펀치 교체 필요 (마모 ≥ {WEAR_LIMIT:g})'}, 'tool_id': {'label': '펀치 (실험)'}, 'stroke_id': {'label': '스트로크 번호'},
                                'use_count': {'label': '펀치 누적 스트로크', 'unit': '회'}, **_curve_meta(df)}, {}, '', {}, 'row', 'RWTH Aachen Fine-blanking (공개)', entity='tool_id'),
                                r.tool_id.to_numpy(), dict(proc='프레스', unit='펀치(실험)', y='tool_ng', label=f'마모 ≥ {WEAR_LIMIT:g} (실험 끝 = 1)', lic='미확인', two=False, rel=True))
    if (d / 'weld_rsw.csv').exists():
        r = pd.read_csv(d / 'weld_rsw.csv')
        r['weld_ng'] = (r.category != 'Good').astype(float)
        g = ('P' + r.pressure_psi.astype(int).astype(str) + '·T' + r.weld_time_ms.astype(int).astype(str)).to_numpy()
        df = r.drop(columns=['category'])
        T['weld_rsw'] = (Table('weld_rsw', '용접 · 저항 점용접', df, {'weld_ng': {'label': '용접 불량'}, 'sample_id': {'label': '타점 번호'}, 'pressure_psi': {'label': '가압 압력', 'unit': 'psi'},
                         'weld_time_ms': {'label': '통전 시간', 'unit': 'ms'}, 'angle_deg': {'label': '전극 각도', 'unit': 'deg'}, 'thickness_a': {'label': '판 두께 A', 'unit': 'mm'}, 'thickness_b': {'label': '판 두께 B', 'unit': 'mm'},
                         **{c: {'label': f"{CH_KO[c.rsplit('_', 1)[0]][0]} {ST_KO.get(c.rsplit('_', 1)[1], '마지막')}", 'unit': CH_KO[c.rsplit('_', 1)[0]][1]} for c in df if c.startswith(('rsw_force', 'rsw_cur'))}}, {}, '', {}, 'row', 'Mendeley rwh8kjzdch (CC BY 4.0)'),
                         g, dict(proc='용접', unit='조건(압력 × 시간)', y='weld_ng', label='Bad · Explode', lic='CC BY 4.0', two=True))
    if (d / 'weld_arc.csv').exists():
        r = pd.read_csv(d / 'weld_arc.csv')
        r = r[r.label >= 0].reset_index(drop=True)
        if len(r) > 12000:                                 # 구간 수가 많아 실험마다 고르게 줄인다
            r = r.groupby('exp_id', group_keys=False).apply(lambda x: x.iloc[::max(1, len(r) // 12000)]).reset_index(drop=True)
        r['weld_ng'] = (r.label == 0).astype(float)
        r.insert(0, 'window_id', [f'W{i:06d}' for i in range(len(r))])
        ex = np.sort(r.exp_id.unique())
        fold = {e: f'실험묶음{i % min(6, len(ex)) + 1}' for i, e in enumerate(ex)}
        df = r.drop(columns=['label', 'exp_id', 'run_id'])
        T['weld_arc'] = (Table('weld_arc', '용접 · 아크 용접 (전류 · 전압)', df, {'weld_ng': {'label': '용접 불량'}, 'window_id': {'label': '구간 번호'}, 'short_frac': {'label': '단락 구간 비율'}, **_curve_meta(df)}, {}, '', {}, 'row', 'Zenodo 15497262 (CC BY 4.0)'),
                         r.exp_id.map(fold).to_numpy(), dict(proc='용접', unit=f'실험 묶음 (실험 {len(ex)}개)', y='weld_ng', label='라벨 0 (불량)', lic='CC BY 4.0', two=True))
    return T


# ── 검증: 묶음을 통째로 빼며 규칙 · 엔진 · 엔진 + 공정 전용 입력 · 범용 부스팅
IDS = {'cycle_id', 'stroke_id', 'sample_id', 'window_id', 'tool_id', 'use_count'}


def evaluate(t: Table, G: np.ndarray, info: dict) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from .engine import auc
    from .evaluate import alert_metrics
    from .features import SLOTS
    from .local_inputs import engine_with_local, local_frame
    from .mct import _fit, _fold_data
    from .model import CumulativeModel
    ycol, rel = info['y'], bool(info.get('rel'))
    L = local_frame(t.df, 'tool_id' if 'tool_id' in t.df else None)
    if rel:                                              # 공구(펀치)를 통째로 뺄 때는 절대 수준이 공구마다 달라 «교체 후 변화» 만 쓴다 (MCT 설비 이전과 같은 규칙)
        L = L[[c for c in L if c.startswith('rel_')]]
    Xml = t.df.drop(columns=[c for c in t.df if c in IDS or c == ycol]).apply(pd.to_numeric, errors='coerce')
    Xml = pd.concat([Xml, L[[c for c in L if c.startswith('rel_')]]], axis=1).fillna(Xml.median()).fillna(0.0)
    y_all = t.df[ycol].to_numpy(float)
    pool, ys, ops, folds = {k: [] for k in ('rule', 'engine', 'plus', 'boost')}, [], {k: [] for k in ('engine', 'plus', 'boost')}, []
    for g in sorted(pd.unique(G)):
        te = G == g
        if not (y_all[~te] == 1).any() or not (y_all[~te] == 0).any() or not te.any():
            continue
        Xa, ya, Xb, yb, rule = _fold_data(t, g, 'sensor', G)
        M = _fit(CumulativeModel(SLOTS), [(t.key, Xa, ya)])
        S = {'rule': (None, rule), 'engine': (M.predict(t.key, Xa, np.ones(len(ya)))[0], M.predict(t.key, Xb, np.ones(len(yb)))[0])}
        Xa2, _, Xb2, _, _ = _fold_data(t, g, 'rel', G) if rel else (Xa, ya, Xb, yb, rule)
        a, b, _, _ = engine_with_local(t.key, Xa2, ya, Xb2, L[~te], L[te], info['two'])
        S['plus'] = (a, b)
        gb = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, random_state=0).fit(Xml[~te], y_all[~te].astype(int))
        S['boost'] = (gb.predict_proba(Xml[~te])[:, 1], gb.predict_proba(Xml[te])[:, 1])
        for k, (s_tr, s_te) in S.items():
            s_te = np.asarray(s_te, float)
            pool[k] += list(s_te)
            if s_tr is not None:
                thr = float(np.quantile(np.asarray(s_tr)[np.asarray(ya) == 0], 0.95))          # 학습 묶음 정상의 95% 분위
                ops[k].append((int(((s_te > thr) & (yb == 1)).sum()), int((yb == 1).sum()), int(((s_te > thr) & (yb == 0)).sum()), int((yb == 0).sum())))
        ys += list(yb)
        folds.append({'group': str(g), 'rows': int(te.sum()), 'events': int(yb.sum()), 'plus': auc(np.asarray(S['plus'][1]), yb) if 0 < yb.sum() < len(yb) else None})
    y = np.array(ys)
    res = {'name': t.process, 'proc': info['proc'], 'source': t.source, 'license': info['lic'], 'label': info['label'], 'unit': info['unit'], 'rows': int(len(y)), 'events': int(y.sum()),
           'groups': len(folds), 'inputs': int(L.shape[1]), 'input': '교체 후 변화만' if rel else '공정 전용 입력', 'models': {}, 'folds': folds}
    for k, s in pool.items():
        s = np.array(s, float)
        m = {'auc': auc(s, y), 'capture10': alert_metrics(s, y)['capture']}
        if k in ops and ops[k]:
            o = np.array(ops[k]).sum(0)
            m.update(recall=float(o[0] / o[1]) if o[1] else None, false_alarm=float(o[2] / o[3]) if o[3] else None)
        res['models'][k] = m
    ok = bool(res['models']['plus']['auc'] >= GATE)
    res['apply'] = ok and not info.get('hold')
    res['reason'] = '' if res['apply'] else (info.get('hold') or f'기준 미달 (AUC {GATE:g})')
    return res


def _f(v, p=2):
    return '—' if v is None or (isinstance(v, float) and not np.isfinite(v)) else f'{v:.{p}f}'


def report_md(res: dict) -> str:
    L = ['# 사출 · 프레스 · 용접 기본 모델 — 학습 팩 검증', '',
         '묶음(실험 · 조건 · 펀치 · 시간 구간)을 통째로 빼고 학습 → 뺀 묶음으로 평가. 값 = 구분력(AUC). 누적 사용량 · 결과 측정값(무게 · 치수 · 인장)은 입력에서 제외.',
         f'적용 기준: MILG 엔진 + 공정 전용 입력 AUC ≥ {GATE:g}. 범용 부스팅은 비교 기준 (엔진 계산에 넣지 않음).', '',
         '| 공정 | 데이터 | 묶음 · 행 · 불량 | 규칙 | MILG 엔진 | 엔진 + 공정 전용 입력 | 범용 부스팅 | 잡은 불량 · 정상 알림 | 사용 조건 | 판정 |', '|---|---|---|---|---|---|---|---|---|---|']
    for k, r in res['data'].items():
        m = r['models']
        L.append(f"| {r['proc']} | {r['name']} | {r['groups']} {r['unit']} · {r['rows']:,} · {r['events']:,} | {_f(m['rule']['auc'])} | {_f(m['engine']['auc'])} | **{_f(m['plus']['auc'])}** | {_f(m['boost']['auc'])} | "
                 f"{_f((m['plus'].get('recall') or 0) * 100, 0)}% · {_f((m['plus'].get('false_alarm') or 0) * 100, 0)}% | {r['license']} | {'적용' if r['apply'] else '보류 — ' + r.get('reason', '')} |")
    L += ['', '입력: ' + ' · '.join(f"{r['name']} = {r.get('input', '공정 전용 입력')}" for r in res['data'].values()) + ' (공구 · 펀치를 통째로 뺄 때는 절대 수준이 달라 «교체 후 변화» 만 쓴다).']
    L += ['', '정답: ' + ' · '.join(f"{r['name']} = {r['label']}" for r in res['data'].values()), '', '## 묶음별 AUC (엔진 + 공정 전용 입력)', '']
    for r in res['data'].values():
        L.append(f"- {r['name']}: " + ' · '.join(f"{f['group']} {_f(f['plus'])}" for f in r['folds'][:40]))
    L += ['', '## 학습에 쓰지 않은 데이터', '', '| 공정 | 데이터 | 이유 | 판정 |', '|---|---|---|---|'] + [f'| {a} | {b} | {c} | {d} |' for a, b, c, d in SKIPPED]
    return '\n'.join(L) + '\n'


def web_rows(res: dict, applied: bool = True) -> list[dict]:
    """MES 화면 행 — applied=True 면 적용된 것만 («기준선 비교» 표), False 면 검증한 것 전부"""
    u = lambda r: r['unit'].split('(')[0].replace('실험 묶음', '실험').strip()     # noqa: E731 — 짧은 묶음 이름 (실험 · 펀치 · 조건)
    return [{'proc': r['proc'], 'name': r['name'], 'short': r['name'].split(' · ', 1)[-1].split(' (')[0], 'size': f"{u(r)} {r['groups']} · {r['rows']:,}행", 'unit': f"{u(r)} 단위로 뺌", 'rule': round(r['models']['rule']['auc'], 2),
             'base': round(r['models']['engine']['auc'], 2), 'engine': round(r['models']['plus']['auc'], 2), 'boost': round(r['models']['boost']['auc'], 2), 'input': r.get('input', '공정 전용 입력'),
             'recall': r['models']['plus'].get('recall'), 'false_alarm': r['models']['plus'].get('false_alarm'), 'license': r['license'], 'apply': r['apply'], 'reason': r.get('reason', '').split(' (')[0], 'pack': k}
            for k, r in res['data'].items() if r['apply'] or not applied]


def _save(t: Table, path: Path, meta: dict, two: bool, rel: bool):
    """전체 데이터로 학습 → JSON 하나 (엔진 상태 + 공정 전용 입력 표준화 값). 외부 모델 파일 없음"""
    from .features import SLOTS
    from .local_inputs import FIT, LocalScaler, fit_engine, local_frame
    from .mct import _encode
    e, X = _encode(t, 'rel' if rel else 'sensor')
    L = local_frame(t.df, 'tool_id' if 'tool_id' in t.df else None)
    if rel:
        L = L[[c for c in L if c.startswith('rel_')]]
    sc = LocalScaler(two).fit(L)
    M = fit_engine(t.key, np.column_stack([X, sc.transform(L)]), np.asarray(e.y, float), list(SLOTS) + sc.names)
    d = {'kind': 'milg_engine_local', 'process': t.key, 'model': M.to_dict(), 'fit': FIT, 'variant': 'rel' if rel else 'sensor',
         'local': {'cols': sc.cols, 'mu': sc.mu.tolist(), 'sd': sc.sd.tolist(), 'two_sided': two, 'rel_n': 10}, **meta}
    txt = json.dumps(d, ensure_ascii=False)
    d = json.loads(txt, parse_float=lambda x: float(f'{float(x):.6g}'))     # 유효 숫자 6자리 — 파일 크기를 줄인다
    path.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')


def run(sum_dir, out_dir: Path | None = None, state_dir: Path | None = None, web: bool = True, only: list[str] | None = None) -> dict:
    import datetime as dt
    out_dir, state_dir = Path(out_dir or HERE / 'reports'), Path(state_dir or HERE / 'state' / 'base')
    T = load_tables(sum_dir)
    old = json.loads((out_dir / 'packs.json').read_text(encoding='utf-8')) if (out_dir / 'packs.json').exists() else {'data': {}}
    res = {'built': dt.datetime.now().strftime('%Y-%m-%d %H:%M'), 'gate': GATE, 'data': dict(old.get('data', {})), 'skipped': [list(s) for s in SKIPPED]}
    for k, (t, G, info) in T.items():
        if only and k not in only:
            continue
        r = evaluate(t, G, info)
        if r['apply']:                                   # 전체 데이터로 다시 학습해 저장 (JSON 하나 · 외부 모델 파일 없음)
            state_dir.mkdir(parents=True, exist_ok=True)
            _save(t, state_dir / f'{k}_engine.json', {'name': t.process, 'source': t.source, 'license': info['lic'], 'label': info['label'], 'cv_auc': r['models']['plus']['auc'], 'built': res['built']}, info['two'], bool(info.get('rel')))
            r['model_file'] = f'{k}_engine.json'
        res['data'][k] = r
    res['data'] = {k: res['data'][k] for k in ('inj_quality', 'inj_polenta', 'press_fineblank', 'weld_arc', 'weld_rsw') if k in res['data']}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'packs.json').write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    (out_dir / 'packs.md').write_text(report_md(res), encoding='utf-8')
    if web:
        publish(res)
    return res


def publish(res: dict | None = None) -> bool:
    """MES 화면(web/data/ai_model.json) 에 반영 — 기준선 비교 표에 적용된 공정 행 · 학습 팩 검증 표. res 가 없으면 reports/packs.json 을 읽는다"""
    res = res or json.loads((HERE / 'reports' / 'packs.json').read_text(encoding='utf-8'))
    f = WEB / 'ai_model.json'
    if not f.exists():
        return False
    art = json.loads(f.read_text(encoding='utf-8'))
    if 'mct' not in art:
        return False
    art['mct']['bench'] = [b for b in art['mct'].get('bench', []) if not b.get('pack')] + web_rows(res)
    art['packs'] = {'built': res['built'], 'gate': res.get('gate', GATE), 'rows': web_rows(res, applied=False), 'skipped': res['skipped']}
    f.write_text(json.dumps(art, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    return True
