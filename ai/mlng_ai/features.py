"""공정 공통 표현 — 공정마다 다른 칼럼을 «의미 슬롯» 의 같은 모양 벡터로 바꾼다.

슬롯 (모든 공정이 같은 자리)
  q:<물리량>   그 물리량 칼럼들이 «이 공정의 평소 값» 에서 얼마나 벗어났나 (|z| 최댓값)
  spread       변동(표준편차 · 산포) 칼럼들이 평소보다 얼마나 커졌나
  spec         규격(설정값 ± 허용폭) 대비 벗어난 정도 (규격이 있는 칼럼만)
  event        교체 · 급변 같은 0/1 사건이 켜졌나
  all_max      모든 조건 칼럼 중 가장 크게 벗어난 정도 · n_out: 크게 벗어난 칼럼 비율
  cat_rare     작업자 · 자재 · 설비 같은 범주 값이 지금까지 드물었던 것인가
  load         LOT 수량(노출)이 평소보다 큰가 · missing: 빈 값 비율
  up:*         LOT 추적으로 이어진 앞 공정 LOT 의 같은 슬롯 (공정 간 원인 추적)

«누적형»: 평소 값(평균 · 표준편차 · 범주 빈도)은 그 행 «이전까지» 쌓인 데이터만으로 계산한다.
그래서 새 데이터가 들어올 때마다 기준이 자연스럽게 갱신되고, 미래 정보가 섞이지 않는다 (시간순 검증과 같은 조건).
마지막에 슬롯마다 다시 «이 공정에서 평소보다 몇 σ» 로 맞춰서, 공정이 달라도 같은 크기로 비교된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .datasets import Table
import re

from .lexicon import ALARM_WORDS, QUANTITY
from .schema import ColumnSense, leakage_guard, sense_table

QS = list(QUANTITY) + ['unknown']
ACC_SLOTS = ['acc:native', 'acc:count', 'acc:exposure', 'acc:dose', 'acc:drift', 'acc:spec_dose']
WAVE_SLOTS = ['wave:dose', 'wave:trend', 'wave:rough']
TEXT_SLOTS = ['text:risk', 'text:alarm']
BASE_SLOTS = [f'q:{q}' for q in QS] + ['spread', 'spec', 'event', 'all_max', 'n_out', 'cat_rare', 'load', 'missing'] + ACC_SLOTS + WAVE_SLOTS + TEXT_SLOTS
UP_SLOTS = ['up:all_max', 'up:n_out', 'up:spec', 'up:spread', 'up:event', 'up:q:temperature', 'up:q:pressure', 'up:q:current', 'up:q:wear', 'up:q:vibration', 'up:q:time',
            'up:acc:dose', 'up:wave:dose', 'up:text:risk']
GROUPS = {'적산': ACC_SLOTS, '파형': WAVE_SLOTS, '텍스트': TEXT_SLOTS}
SLOTS = BASE_SLOTS + UP_SLOTS + ['up:has']
SLOT_KO = {'spread': '변동(산포) 증가', 'spec': '규격 대비 이탈', 'event': '교체 · 급변 사건', 'all_max': '가장 크게 벗어난 조건', 'n_out': '벗어난 조건 수',
           'cat_rare': '드문 작업자 · 자재 · 설비', 'load': '수량(장입량) 과다', 'missing': '빈 값', 'up:has': '앞 공정 연결 있음',
           'acc:native': '적산 값 (사용 시간 · 마모)', 'acc:count': '교체 뒤 누적 횟수', 'acc:exposure': '교체 뒤 누적 생산량', 'acc:dose': '누적 이탈량 (작은 이탈이 쌓임)', 'acc:drift': '지속 치우침 (한쪽으로 계속)',
           'acc:spec_dose': '규격 이탈 누적', 'wave:dose': '파형 · 규격 밖 체류 (이탈 적분)', 'wave:trend': '파형 · 작업 중 기울기', 'wave:rough': '파형 · 요동',
           'text:risk': '메모 · 알람 문장 위험도 (학습)', 'text:alarm': '메모 속 이상 징후 단어'}
for q, (ko, _) in QUANTITY.items():
    SLOT_KO[f'q:{q}'] = f'{ko} 이탈'
SLOT_KO['q:unknown'] = '이름 없는 센서 이탈'
for s in UP_SLOTS:
    SLOT_KO[s] = '앞 공정 · ' + SLOT_KO[s[3:]]
MIN_HIST = 20         # 이보다 적게 쌓이면 «평소» 를 모른다 → 0 (표준편차 추정이 흔들리지 않을 만큼)
RESET_WORDS = ['교체', '교환', 'changeover', 'change', '정비', 'maintenance', 'reset', 'dress', '드레싱', '점검', 'replace']


# ── 적산: 교체 · 정비(리셋) 이후 계속 더해지는 양
def find_entity(df: pd.DataFrame, S: dict, given: str = '') -> str:
    """적산을 묶을 단위 — 공구 · 금형 · 설비처럼 «한동안 같은 값이 이어지다 바뀌는» 범주 · 식별자 칼럼"""
    if given and given in df:
        return given
    best, score = '', 1.0
    for c, s in S.items():
        if s.role not in ('id', 'category'):
            continue
        v = df[c].astype(str).to_numpy()
        k = len(set(v))
        if 2 <= k <= max(2, len(v) // 5):
            ch = float(np.mean(v[1:] != v[:-1])) if len(v) > 1 else 1.0
            if ch < min(score, 0.15):
                best, score = c, ch
    return best


def accumulate(df, S, t, n, X):
    N = len(df)
    reset = np.zeros(N, bool); reset[0] = True
    why = []
    ent = find_entity(df, S, t.entity)
    if ent:
        v = df[ent].astype(str).to_numpy(); reset[1:] |= v[1:] != v[:-1]; why.append(ent)
    for c, sv in S.items():
        if sv.role == 'param' and sv.stat == 'event' and any(w in (c + ' ' + sv.label).lower() for w in RESET_WORDS):
            reset |= pd.to_numeric(df[c], errors='coerce').fillna(0).to_numpy() > 0; why.append(c)
    nat = [c for c, sv in S.items() if sv.role == 'param' and sv.stat == 'accum']
    for c in nat:                                         # 적산 값이 크게 떨어진 곳 = 교체
        x = pd.to_numeric(df[c], errors='coerce').to_numpy(float)
        rng = np.nanmax(x) - np.nanmin(x) if np.isfinite(x).any() else 0
        if rng > 0:
            reset[1:] |= np.nan_to_num(np.diff(x)) < -0.4 * rng
    has_reset = reset[1:].any()
    cnt = np.zeros(N); expo = np.zeros(N); dose = np.zeros(N); sdose = np.zeros(N)
    # 누적 이탈량: 칼럼마다 CUSUM (평소보다 0.5σ 넘게 벗어난 만큼만 쌓임) → 가장 많이 쌓인 칼럼
    params = [c for c, sv in S.items() if sv.role == 'param' and sv.stat in ('level', 'spread')]
    ZS = [expanding_z(pd.to_numeric(df[c], errors='coerce').to_numpy(float)) for c in params] or [np.zeros(N)]
    ZC = [np.abs(z) for z in ZS]
    cs = np.zeros(len(ZC))
    ew = np.zeros(len(ZS)); drift = np.zeros(N); drift_who = np.zeros(N, int)
    dose_who = np.zeros(N, int)
    spv = X['spec'].to_numpy(float)
    c = e = d = sd = 0.0
    for i in range(N):
        if reset[i]:
            c = e = d = sd = 0.0
        if reset[i]:
            cs[:] = 0.0
        c += 1; e += n[i]
        for j, z in enumerate(ZC):
            cs[j] = max(0.0, cs[j] + z[i] - 0.8)              # |z| 평균은 약 0.8 — 그보다 큰 만큼만 쌓인다
        d = float(cs.max()); dose_who[i] = int(cs.argmax())
        for j, z in enumerate(ZS):                           # 지속 치우침: 부호 있는 z 의 지수 평균 (λ 0.1 ≈ 최근 10회)
            ew[j] = 0.9 * ew[j] + 0.1 * z[i]
        drift[i] = float(np.abs(ew).max()) * np.sqrt(19); drift_who[i] = int(np.abs(ew).argmax())   # √((2−λ)/λ) 로 σ 단위
        sd = (sd if has_reset else 0.9 * sd) + max(spv[i] - 1.0, 0.0)   # 리셋 정보가 없으면 천천히 잊는 합
        cnt[i], expo[i], dose[i], sdose[i] = c, e, min(d, 30), min(sd, 30)
    X['acc:dose'], X['acc:spec_dose'], X['acc:drift'] = dose, sdose, drift
    if params:
        acc_who = (params, dose_who, drift_who)
    else:
        acc_who = None
    if has_reset:
        X['acc:count'] = cnt                                  # 교체 뒤 몇 번째인가 (뒤에서 공정 안 σ 로 맞춤)
        if n.max() > 1:                                       # 수량이 행마다 다를 때만 (한 개 단위면 횟수와 같음)
            X['acc:exposure'] = np.log1p(expo)
    return nat, ent, int(reset.sum()), acc_who


# ── 비정형 ① 파형: LOT 하나의 공정값 기록(온도 프로파일 · 타발 추이)을 모양 숫자로
def wave_features(t, lots, X, who):
    if not t.series:
        return []
    keys, D, T, R = [], [], [], []
    for k, sr in t.series.items():
        by, tol = sr['by_lot'], sr.get('tol')
        d, tr, ro = np.zeros(len(lots)), np.zeros(len(lots)), np.zeros(len(lots))
        for i, lot in enumerate(lots):
            a = by.get(lot)
            if not a or len(a) < 4:
                continue
            a = np.asarray(a, float); a = a[np.isfinite(a)]
            if len(a) < 4:
                continue
            sp = sr['sp'].get(lot) if isinstance(sr.get('sp'), dict) else sr.get('sp')
            sc = float(tol) if tol else (np.std(a) or 1.0)
            center = float(sp) if sp is not None else float(np.median(a))
            dev = np.abs(a - center) / sc
            d[i] = float(np.mean(np.clip(dev - 1.0, 0, None)))                      # 허용폭 밖에 머문 양 (시간 평균 이탈 적분)
            tr[i] = abs(float(np.polyfit(np.arange(len(a)), a, 1)[0])) * len(a) / sc  # 작업 처음 → 끝 기울기
            ro[i] = float(np.mean(np.abs(np.diff(a)))) / sc                         # 요동
        keys.append(k); D.append(d); T.append(tr); R.append(ro)
    for slot, M in (('wave:dose', D), ('wave:trend', T), ('wave:rough', R)):
        A = np.column_stack(M)
        X[slot] = A.max(1); who[slot] = ([t.series[k]['label'] for k in keys], A.argmax(1))
    return keys


# ── 비정형 ② 문장: 작업 메모 · 알람 글
TOK = re.compile(r'[0-9a-zA-Z가-힣]+')


def grams(text: str) -> set:
    t = str(text).lower()
    w = TOK.findall(t)
    out = set(w)
    for x in w:                                            # 글자 3-그램 (한글 어미 · 띄어쓰기 달라도 잡음)
        if len(x) >= 3:
            out.update(x[j:j + 3] for j in range(len(x) - 2))
    return out


def text_features(df, S, y, n, X, who):
    cols = [c for c, sv in S.items() if sv.role == 'text']
    if not cols:
        return []
    N = len(df)
    risk, alarm = np.zeros(N), np.zeros(N)
    rate = y / np.maximum(n, 1)
    pos, neg = {}, {}
    P = Nn = 0
    sy = sn = 0.0
    for i in range(N):
        txt = ' '.join(str(df[c].iat[i]) for c in cols if pd.notna(df[c].iat[i]))
        g = grams(txt)
        alarm[i] = sum(1 for w in ALARM_WORDS if w in txt.lower())
        if P >= 3 and Nn >= 3 and g:                       # 그 행 이전까지 쌓인 글만으로 점수 (누적 · 미래 정보 없음)
            s = sum(np.log((pos.get(x, 0) + 0.5) / (P + 1)) - np.log((neg.get(x, 0) + 0.5) / (Nn + 1)) for x in g)
            risk[i] = s / np.sqrt(len(g))
        ev = (y[i] > 0) if n.max() <= 1 else (rate[i] > (sy / sn if sn else 0))
        book = pos if ev else neg
        for x in g:
            book[x] = book.get(x, 0) + 1
        P += int(ev); Nn += int(not ev); sy += y[i]; sn += n[i]
    X['text:risk'], X['text:alarm'] = np.clip(risk, -10, 10), alarm
    who['text:risk'] = (cols, np.zeros(N, int)); who['text:alarm'] = (cols, np.zeros(N, int))
    return cols


def expanding_z(x: np.ndarray) -> np.ndarray:
    """그 행 이전까지의 평균 · 표준편차로 본 z (빈 값은 0)"""
    v = np.where(np.isfinite(x), x, 0.0)
    m = np.isfinite(x).astype(float)
    n = np.concatenate([[0], np.cumsum(m)[:-1]])
    s1 = np.concatenate([[0], np.cumsum(v)[:-1]])
    s2 = np.concatenate([[0], np.cumsum(v * v)[:-1]])
    with np.errstate(invalid='ignore', divide='ignore'):
        mu = s1 / n
        sd = np.sqrt(np.maximum(s2 / n - mu * mu, 0) * n / np.maximum(n - 1, 1))
        z = (x - mu) / np.where(sd > 1e-12, sd, np.nan)
    z[(n < MIN_HIST) | ~np.isfinite(z)] = 0.0
    return np.clip(z, -6, 6)


def expanding_rarity(vals: pd.Series) -> np.ndarray:
    """그 행 이전까지 이 범주 값이 얼마나 드물었나: −log((이전 등장 + 1)/(이전 행 + 2)) 를 0~1 로"""
    cnt, out = {}, np.zeros(len(vals))
    for i, v in enumerate(vals.astype(str).to_numpy()):
        if i >= MIN_HIST:
            out[i] = -np.log((cnt.get(v, 0) + 1) / (i + 2)) / np.log(i + 2)
        cnt[v] = cnt.get(v, 0) + 1
    return out


@dataclass
class Encoded:
    table: Table
    senses: dict
    X: pd.DataFrame            # 슬롯 값 (공정 안 표준화 전)
    y: np.ndarray              # 사건 수 (불량 수 · 0/1)
    n: np.ndarray              # 노출 (수량 · 1)
    lot: np.ndarray            # LOT 키 (앞 공정 연결)
    detail: dict = field(default_factory=dict)   # 행마다 슬롯을 만든 칼럼 (설명용)
    leak: list = field(default_factory=list)
    info: dict = field(default_factory=dict)


def outcome_vector(s: pd.Series, cs: ColumnSense) -> np.ndarray:
    v = pd.to_numeric(s, errors='coerce')
    if v.notna().mean() > 0.95:
        u = np.unique(v.dropna())
        if len(u) == 2:                                   # 이진: 큰 값이 사건 (부호가 반대면 작은 값)
            ev = (v == (u.min() if cs.polarity < 0 else u.max())).astype(float).to_numpy()
        else:
            ev = v.fillna(0).clip(lower=0).to_numpy(float)   # 개수
    else:                                                 # 글자: «좋은 쪽» 단어가 없는 값이 사건 (band / noband)
        t = s.astype(str).str.lower()
        bad = t.str.contains(r'불합격|불량|부적합|^ng$|\bng\b|fail|reject|defect|^band$|^x$', regex=True)      # 한국 현장 표기: 불량 · NG · 불합격
        good = t.str.contains(r'no ?band|noband|\bok\b|^ok|pass|good|normal|합격|양품|적합|^o$', regex=True) & ~bad
        ev = (bad | ~good).astype(float).to_numpy()
    return ev


def encode(t: Table, overrides: dict | None = None, llm: bool = False) -> Encoded:
    df = t.df
    S = sense_table(df, t.meta, overrides=overrides, llm=llm, process=t.process)
    oc = [c for c, s in S.items() if s.role == 'outcome']
    if not oc:
        raise ValueError(f'{t.key}: 결과(불량) 칼럼을 찾지 못했습니다 — overrides 로 지정하세요')
    y = outcome_vector(df[oc[0]], S[oc[0]])
    ex = [c for c, s in S.items() if s.role == 'exposure']
    n = pd.to_numeric(df[ex[0]], errors='coerce').fillna(1).clip(lower=1).to_numpy(float) if ex else np.ones(len(df))
    if not ex and y.max() > 1:
        n = np.maximum(n, y)
    leak = leakage_guard(df, S, (y > 0).astype(float) if y.max() <= 1 else (y / n > np.median(y / n)).astype(float))

    params = [c for c, s in S.items() if s.role == 'param']
    Z = {c: expanding_z(pd.to_numeric(df[c], errors='coerce').to_numpy(float)) for c in params if S[c].stat != 'event'}
    X = pd.DataFrame(0.0, index=df.index, columns=BASE_SLOTS)
    who = {}
    for q in QS:
        cs = [c for c in Z if S[c].quantity == q and S[c].stat == 'level']     # 적산 값은 acc:native 로 따로
        if cs:
            A = np.abs(np.column_stack([Z[c] for c in cs]))
            X[f'q:{q}'] = A.max(1); who[f'q:{q}'] = (cs, A.argmax(1))
    sp = [c for c in Z if S[c].stat == 'spread']
    if sp:
        A = np.column_stack([Z[c] for c in sp])                 # 변동은 «커질 때» 만
        X['spread'] = np.clip(A, 0, None).max(1); who['spread'] = (sp, A.argmax(1))
    if t.spec:
        cols, D = [], []
        for c, (spv, tol) in t.spec.items():
            if c in df and S.get(c) and S[c].role == 'param':
                spa = pd.to_numeric(df[spv], errors='coerce').to_numpy(float) if isinstance(spv, str) else np.full(len(df), float(spv))
                D.append(np.clip(np.abs(pd.to_numeric(df[c], errors='coerce').to_numpy(float) - spa) / float(tol), 0, 6)); cols.append(c)
        if D:
            A = np.nan_to_num(np.column_stack(D)); X['spec'] = A.max(1); who['spec'] = (cols, A.argmax(1))
    evc = [c for c in params if S[c].stat == 'event']
    if evc:
        A = np.column_stack([pd.to_numeric(df[c], errors='coerce').fillna(0).to_numpy(float) for c in evc]); X['event'] = A.max(1); who['event'] = (evc, A.argmax(1))
    if Z:
        A = np.abs(np.column_stack(list(Z.values())))
        X['all_max'] = A.max(1); X['n_out'] = (A > 2.5).mean(1); who['all_max'] = (list(Z), A.argmax(1))
        X['missing'] = np.column_stack([df[c].isna().to_numpy() for c in Z]).mean(1)
    cats = [c for c, s in S.items() if s.role == 'category']
    if cats:
        A = np.column_stack([expanding_rarity(df[c]) for c in cats]); X['cat_rare'] = A.max(1); who['cat_rare'] = (cats, A.argmax(1))
    if ex:
        X['load'] = np.clip(expanding_z(np.log(n)), 0, None)
    lot = df[t.lot_col].astype(str).to_numpy() if t.lot_col else np.array([''] * len(df))
    # 적산
    nat, ent, nres, aw = accumulate(df, S, t, n, X)
    if aw:
        who['acc:dose'] = (aw[0], aw[1]); who['acc:drift'] = (aw[0], aw[2])
    if nat:
        A = np.abs(np.column_stack([expanding_z(pd.to_numeric(df[c], errors='coerce').to_numpy(float)) for c in nat]))
        X['acc:native'] = A.max(1); who['acc:native'] = (nat, A.argmax(1))
    # 비정형
    waves = wave_features(t, lot, X, who)
    texts = text_features(df, S, y, n, X, who)
    enc = Encoded(t, S, X, y, n, lot, who, leak)
    enc.info = {'entity': ent, 'resets': nres, 'accum_cols': nat, 'waves': waves, 'texts': texts}
    return enc


def add_upstream(encs: dict[str, Encoded]) -> None:
    """LOT 추적: 앞 공정 LOT 의 슬롯을 «up:» 로 붙인다 (앞 LOT 이 여럿이면 최댓값 — 가장 나빴던 앞 공정)"""
    by_lot = {}
    for e in encs.values():
        if not e.table.lot_col:
            continue
        g = e.X.assign(_lot=e.lot).groupby('_lot').mean(numeric_only=True)
        for lot, row in g.iterrows():
            by_lot[lot] = (e.table.key, row)
    for e in encs.values():
        U = pd.DataFrame(0.0, index=e.X.index, columns=UP_SLOTS + ['up:has'])
        src = {}
        if e.table.parents:
            for i, lot in enumerate(e.lot):
                ps = [by_lot[p] for p in e.table.parents.get(lot, []) if p in by_lot]
                if not ps:
                    continue
                U.iat[i, U.columns.get_loc('up:has')] = 1.0
                for s in UP_SLOTS:
                    vals = [r[s[3:]] for _, r in ps if s[3:] in r.index]
                    if vals:
                        U.iat[i, U.columns.get_loc(s)] = float(np.max(vals))
                src[i] = sorted({k for k, _ in ps})
        e.X = pd.concat([e.X[BASE_SLOTS], U], axis=1)
        e.detail['_up'] = src


def standardize(X: pd.DataFrame) -> pd.DataFrame:
    """슬롯을 공정 안에서 «평소보다 몇 σ» 로 (누적 · 이전 행 기준). 0/1 표시 슬롯은 그대로"""
    out = X.copy()
    for c in X.columns:
        if c == 'up:has':
            continue
        v = X[c].to_numpy(float)
        if np.nanstd(v) < 1e-12:
            out[c] = 0.0
        else:
            out[c] = expanding_z(v)
    return out
