"""칼럼 의미 해석 — 칼럼 이름 · 설명 · 단위 · 값의 모양을 보고 «무슨 칼럼인가» 를 정한다.

결과: ColumnSense(role, quantity, confidence, why)
  role      id · time · outcome · outcome_detail · exposure · category · param · ignore
  quantity  공정 조건(param)일 때 물리량 (lexicon.QUANTITY 키) — 모르면 'unknown'
  polarity  결과 칼럼에서 1(또는 그 값)이 «좋은 쪽» 이면 -1 (예: ok, pass, 합격)

근거 세 가지를 점수로 합친다 (로컬 · 인터넷 불필요)
  1 사전: 단위([K] · (bar) · rpm) 와 한국어 · 영어 · 약어 키워드
  2 문자 n-그램 임베딩: 처음 보는 이름도 비슷한 글자 조합으로 가장 가까운 물리량을 찾는다 (한글 포함)
  3 값의 모양: 고유값 비율 · 이진 여부 · 정수 여부 · 단조 증가 · 값 범위
신뢰도가 낮은 칼럼만 (선택) LLM 에 물어보고, 사람이 확정한 값(overrides)이 있으면 그것이 최우선이다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .lexicon import ACCUM, BAD_POLARITY, GOOD_POLARITY, TEXT_WORDS, OUTCOME_DETAIL, QUANTITY, ROLE_WORDS, SETPOINT, SPREAD, UNIT

DIM = 512


@dataclass
class ColumnSense:
    name: str
    role: str
    quantity: str = ''
    stat: str = ''                 # level(평균 · 값) · spread(변동) · event(0/1 사건)
    confidence: float = 0.0
    polarity: int = 1
    unit: str = ''
    label: str = ''
    why: list = field(default_factory=list)
    source: str = 'local'          # local · llm · human

    def to_dict(self):
        return asdict(self)


# ── 글자 처리
def split_words(s: str) -> list[str]:
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', str(s))            # camelCase → camel Case
    s = re.sub(r'[_\-./\[\]()]+', ' ', s.lower())
    s = re.sub(r'([a-z])(\d)', r'\1 \2', s)                     # hz1 → hz 1
    return [w for w in s.split() if w]


def unit_of(name: str, unit: str = '') -> str:
    if unit:
        return unit.strip().lower()
    m = re.search(r'[\[(]\s*([^\])]+?)\s*[\])]', str(name))
    return m.group(1).strip().lower() if m else ''


def embed(text: str) -> np.ndarray:
    """문자 2·3-그램 해싱 임베딩 (외부 모델 없이 · 한글도 같은 방식)"""
    v = np.zeros(DIM)
    t = ' ' + re.sub(r'\s+', ' ', text.lower()) + ' '
    for n in (2, 3):
        for i in range(len(t) - n + 1):
            g = t[i:i + n]
            if g.strip():
                h = int(hashlib.md5(g.encode('utf-8')).hexdigest()[:8], 16)
                v[h % DIM] += 1.0
    nrm = np.linalg.norm(v)
    return v / nrm if nrm else v


_PROTO = {q: embed(' '.join([ko] + kws)) for q, (ko, kws) in QUANTITY.items()}


def _kw_hit(words: list[str], text: str, kws: list[str]) -> float:
    """가장 강한 일치 + 두 번째 일치의 절반 (같은 뜻 단어가 여러 개 맞으면 더 확실)"""
    hits = []
    for k in kws:
        if ' ' in k or not k.isascii():
            if k in text:
                hits.append(2.0 if len(k) >= 2 else 1.0)
        elif k in words:
            hits.append(2.0)
        elif len(k) >= 4 and any(w.startswith(k) or k in w for w in words):
            hits.append(1.2)
    hits.sort(reverse=True)
    return (hits[0] + 0.5 * hits[1]) if len(hits) > 1 else (hits[0] if hits else 0.0)


# ── 값의 모양
def profile(values: pd.Series) -> dict:
    s = values.dropna()
    n = len(s)
    num = pd.to_numeric(s, errors='coerce')
    is_num = n > 0 and num.notna().mean() > 0.95
    p = {'n': n, 'numeric': bool(is_num), 'nunique': int(s.nunique()), 'unique_ratio': float(s.nunique() / n) if n else 0.0}
    if is_num:
        x = num.dropna().to_numpy(float)
        p.update({'binary': bool(set(np.unique(x)) <= {0.0, 1.0} or len(np.unique(x)) == 2), 'zero_one': bool(set(np.unique(x)) <= {0.0, 1.0}),
                  'integer': bool(np.all(np.abs(x - np.round(x)) < 1e-9)), 'min': float(x.min()), 'max': float(x.max()),
                  'monotonic': bool(n > 5 and (np.all(np.diff(x) >= 0))), 'nonneg': bool(x.min() >= 0)})
        dx = np.diff(x)
        rng = x.max() - x.min()
        # 톱니 모양: 대부분 늘어나다가 가끔 크게 떨어짐 (교체 · 정비로 0 이 됨) → 적산 값
        p['sawtooth'] = bool(n > 30 and rng > 0 and (dx >= 0).mean() > 0.8 and (dx < -0.4 * rng).sum() >= 1 and (dx > 0).mean() > 0.3)
    else:
        lens = s.astype(str).str.len()
        p.update({'binary': p['nunique'] == 2, 'integer': False, 'monotonic': False,
                  'text': bool(n and lens.mean() >= 8 and s.astype(str).str.contains(r'\s').mean() > 0.5 and p['unique_ratio'] > 0.2),
                  'spacey': bool(n and s.astype(str).str.contains(r'\s').mean() > 0.3)})
        p['datetime'] = bool(n and pd.to_datetime(s.astype(str).head(50), errors='coerce', format='mixed').notna().mean() > 0.9)
    return p


# ── 한 칼럼 해석
def sense(name: str, values: pd.Series, label: str = '', unit: str = '') -> ColumnSense:
    words = split_words(name) + split_words(label)
    text = ' '.join([str(name).lower(), str(label).lower()])
    u = unit_of(name, unit)
    p = profile(values)
    why = []

    # 1) 역할
    hit = {r: _kw_hit(words, text, kws) for r, (_, kws) in ROLE_WORDS.items()}
    detail = _kw_hit(words, text, OUTCOME_DETAIL)
    role, conf = 'param', 0.6
    if p['n'] == 0 or p['nunique'] <= 1:
        role, conf = 'ignore', 0.95; why.append('값이 하나뿐')
    elif detail >= 2 and p['binary'] and hit['outcome'] < 2:
        role, conf = 'outcome_detail', 0.8; why.append('고장 · 불량 «유형» 표시 — 결과와 같은 사건이라 입력에서 제외')
    elif hit['outcome'] >= 2 and (p['binary'] or (p['numeric'] and p.get('integer') and p.get('nonneg'))):
        role, conf = 'outcome', 0.9 if p['binary'] else 0.75; why.append('결과 이름 + ' + ('이진값' if p['binary'] else '0 이상 정수(개수)'))
    elif _kw_hit(words, text, SETPOINT) >= 2 and p['numeric']:
        role, conf = 'setpoint', 0.85; why.append('설정값 — 조건이 아니라 목표값 (규격 비교에만)')
    elif hit['time'] >= 2 and (p.get('datetime') or p.get('monotonic') or (p['numeric'] and p.get('min', 0) > 1e7)):
        role, conf = 'time', 0.9; why.append('시각 이름 + 날짜 · 단조 증가')
    elif hit['id'] >= 2 and (p['unique_ratio'] > 0.9 or not p['numeric']):
        role, conf = 'id', 0.85; why.append('식별자 이름 + 값이 거의 모두 다름')
    elif not p['numeric'] and not p.get('datetime') and (p.get('text') or (_kw_hit(words, text, TEXT_WORDS) >= 2 and p.get('spacey'))):
        role, conf = 'text', 0.85; why.append('띄어쓰기가 있는 문장 — 작업 메모 · 알람 같은 비정형 글')
    elif not p['numeric'] and p['unique_ratio'] > 0.9 and p['n'] > 20:
        role, conf = 'id', 0.7; why.append('글자 값이 거의 모두 다름')
    elif hit['id'] >= 2 and p['numeric'] and p.get('integer') and p['nunique'] > 12 and hit['outcome'] < 2:
        role, conf = 'id', 0.7; why.append('번호 이름 + 정수 코드 (반복돼도 크기에 뜻이 없음)')
    elif hit['exposure'] >= 2 and p['numeric'] and p.get('integer') and p.get('nonneg'):
        role, conf = 'exposure', 0.85; why.append('수량 이름 + 0 이상 정수')
    elif not p['numeric'] or p.get('datetime'):
        role, conf = ('time', 0.7) if p.get('datetime') else ('category', 0.85); why.append('글자 값' if role == 'category' else '날짜 형식')
    elif p['numeric'] and p.get('integer') and p['nunique'] <= 12 and hit['category'] >= 1.2:
        role, conf = 'category', 0.7; why.append('범주 이름 + 적은 정수 코드')
    elif p['numeric'] and p.get('monotonic') and p['unique_ratio'] > 0.95 and hit['id'] >= 1:
        role, conf = 'id', 0.7; why.append('1씩 늘어나는 번호')

    cs = ColumnSense(name=name, role=role, confidence=conf, unit=u, label=label, why=why)
    if role == 'outcome':
        good = _kw_hit(words, text, GOOD_POLARITY) >= 2 or any(g in text.replace('불합격', '') for g in ('합격', '양품'))
        cs.polarity = -1 if good and _kw_hit(words, text, BAD_POLARITY) < 2 else 1
        if cs.polarity < 0:
            why.append('«좋은 쪽» 이름 → 부호 반대')
    if role != 'param':
        return cs

    # 2) 통계 종류 · 0/1 사건
    if p.get('zero_one'):
        cs.quantity, cs.stat, cs.confidence = 'event', 'event', 0.8
        why.append('0/1 값 — 교체 · 급변 같은 «사건» 표시')
        return cs
    cs.stat = 'spread' if _kw_hit(words, text, SPREAD) >= 2 else 'level'
    if cs.stat == 'spread':
        why.append('변동(산포) 값')
    elif _kw_hit(words, text, ACCUM) >= 2 or p.get('sawtooth'):
        cs.stat = 'accum'
        why.append('적산 값 — ' + ('톱니 모양(늘다가 교체 때 0)' if p.get('sawtooth') else '누적 · 사용량 이름'))
    # 3) 물리량: 단위 + 사전 + 문자 임베딩 + 값 범위
    e = embed(' '.join([str(name), str(label)]))
    sc = {}
    for q, (_, kws) in QUANTITY.items():
        s = _kw_hit(words, text, kws) + 1.5 * float(e @ _PROTO[q])
        if u and UNIT.get(u) == q:
            s += 1.0 if u == '%' else 3.0          # % 는 여러 물리량에 두루 쓰여 약한 근거
        sc[q] = s
    if u and u in UNIT:
        why.append(f'단위 «{u}»')
    # 값 범위로 흔한 오해 줄이기: 켈빈 온도 · 0~1 비율
    if p.get('min', 0) >= 0 and p.get('max', 0) <= 1.0001 and not p['binary']:
        sc['ratio'] += 0.6
    best = sorted(sc.items(), key=lambda kv: -kv[1])
    (q1, s1), (_, s2) = best[0], best[1]
    if s1 < 1.0:
        cs.quantity, cs.confidence = 'unknown', 0.3
        why.append('이름으로 물리량을 알 수 없음 — 값 분포로만 사용')
    else:
        cs.quantity = q1
        cs.confidence = round(float(min(0.98, 0.45 + 0.12 * s1 + 0.15 * (s1 - s2))), 2)
        why.append(f'«{QUANTITY[q1][0]}» 점수 {s1:.1f} (다음 {s2:.1f})')
    return cs


# ── 표 전체
def sense_table(df: pd.DataFrame, meta: dict | None = None, overrides: dict | None = None, llm: bool = False, process: str = '') -> dict[str, ColumnSense]:
    """meta: {칼럼: {'label':…, 'unit':…}} · overrides: {칼럼: {'role':…, 'quantity':…}} (사람 확정)"""
    meta = meta or {}
    out = {c: sense(c, df[c], **{k: v for k, v in meta.get(c, {}).items() if k in ('label', 'unit')}) for c in df.columns}
    # 결과 칼럼이 여러 개면: 이름 점수가 가장 높은 하나만 결과, 나머지 이진 결과는 «세부»
    outs = [c for c, s in out.items() if s.role == 'outcome']
    if len(outs) > 1:
        def rank(c):
            t = (c + ' ' + meta.get(c, {}).get('label', '')).lower()
            return (('failure' in t or 'fail' in t or 'defect' in t or '불량' in t or 'band' in t or 'label' in t or 'pass' in t or 'ok' == t.strip()), -len(c))
        keep = max(outs, key=rank)
        for c in outs:
            if c != keep:
                out[c].role, out[c].why = 'outcome_detail', out[c].why + [f'결과는 «{keep}» 하나로 — 이것은 세부']
    if llm:
        low = {c: s for c, s in out.items() if s.role == 'param' and s.confidence < 0.55}
        if low:
            for c, r in llm_resolve(process, {c: meta.get(c, {}) for c in low}, df).items():
                if c in out and r.get('quantity') in QUANTITY:
                    out[c].quantity, out[c].source, out[c].confidence = r['quantity'], 'llm', 0.7
                    out[c].why.append('LLM 보완: ' + str(r.get('why', ''))[:80])
    for c, o in (overrides or {}).items():
        if c in out:
            for k in ('role', 'quantity', 'polarity'):
                if k in o:
                    setattr(out[c], k, o[k])
            out[c].source, out[c].confidence = 'human', 1.0
            out[c].why.append('사람 확인')
    return out


def leakage_guard(df: pd.DataFrame, senses: dict[str, ColumnSense], y: np.ndarray) -> list[str]:
    """결과와 거의 같은 정보를 가진 칼럼(예: 마모 측정값으로 정한 불량)을 찾아 입력에서 뺀다 — AUC 0.97 이상이면 누설 의심"""
    out = []
    pos = y > 0
    if pos.sum() < 2 or (~pos).sum() < 3:
        return out
    th = 0.97 if pos.sum() >= 5 else 0.995              # 사건이 적으면 우연히 높게 나올 수 있어 더 엄격하게
    for c, s in senses.items():
        if s.role != 'param':
            continue
        x = pd.to_numeric(df[c], errors='coerce').to_numpy(float)
        ok = np.isfinite(x)
        if ok.sum() < 10:
            continue
        r = pd.Series(x[ok]).rank().to_numpy()
        n1 = pos[ok].sum(); n0 = ok.sum() - n1
        if n1 == 0 or n0 == 0:
            continue
        auc = (r[pos[ok]].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
        if max(auc, 1 - auc) >= th:
            s.role, s.why = 'outcome_detail', s.why + [f'결과와 거의 같음 (AUC {max(auc, 1 - auc):.2f}) — 누설 의심으로 제외']
            out.append(c)
    return out


def llm_resolve(process: str, cols: dict, df: pd.DataFrame) -> dict:
    """(선택) 신뢰도가 낮은 칼럼만 Claude 에 물어본다 — ANTHROPIC_API_KEY 가 없으면 아무것도 하지 않는다.
    보내는 것: 칼럼 이름 · 설명 · 단위 · 값 요약(최소 · 최대 · 고유값 수)만. 원본 행은 보내지 않는다."""
    key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not key or not cols:
        return {}
    items = []
    for c, m in cols.items():
        x = pd.to_numeric(df[c], errors='coerce')
        items.append({'column': c, 'label': m.get('label', ''), 'unit': m.get('unit', ''), 'min': float(x.min()), 'max': float(x.max()), 'nunique': int(x.nunique())})
    prompt = ('제조 공정 데이터의 칼럼이 어떤 물리량인지 고르세요. 공정: ' + process + '\n선택지: ' + ', '.join(QUANTITY) +
              ', unknown\nJSON 배열만 답하세요: [{"column":…, "quantity":…, "why":"한 줄"}]\n' + json.dumps(items, ensure_ascii=False))
    req = urllib.request.Request('https://api.anthropic.com/v1/messages', method='POST',
                                 headers={'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                                 data=json.dumps({'model': os.environ.get('ASK_MODEL', 'claude-haiku-4-5-20251001'), 'max_tokens': 800,
                                                  'messages': [{'role': 'user', 'content': prompt}]}).encode())
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            j = json.loads(r.read())
        txt = ''.join(b.get('text', '') for b in j.get('content', []))
        arr = json.loads(txt[txt.index('['): txt.rindex(']') + 1])
        return {a['column']: a for a in arr if isinstance(a, dict) and 'column' in a}
    except Exception:  # noqa: BLE001 — 실패하면 로컬 해석 그대로
        return {}
