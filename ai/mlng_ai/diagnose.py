"""데이터 진단 — 고객 공장의 CSV 하나로 «칼럼 해석 · 데이터 품질 · 불량 원인 후보 · 손실 금액» 리포트를 만든다.

영업 PoC 용: 학습 없이(공통 효과만으로) 바로 돌아가고, 결과는 JSON(dict) 과 인쇄 가능한 HTML 두 가지.

  diagnose(df, meta=None, unit_cost=None, model=None)   → dict
  render_html(result)                                    → str (단독 HTML, 브라우저에서 PDF 저장)

원인 후보는 «칼럼 하나씩» 본다 — 숫자는 4분위 구간, 범주는 값별 불량률을 전체와 비교하고,
가장 나쁜 구간과 나머지의 차이가 우연(두 비율 z 검정)보다 큰 것만 남긴다. 상관이지 인과는 아니다.
"""
from __future__ import annotations

import html
import math
from datetime import datetime

import numpy as np
import pandas as pd

from .datasets import Table
from .lexicon import QUANTITY
from .features import SLOT_KO, SLOTS, encode, outcome_vector, standardize
from .schema import leakage_guard, sense_table

ROLE_KO = {'id': '식별자', 'time': '시간', 'outcome': '결과(불량)', 'outcome_detail': '결과 상세', 'exposure': '수량', 'category': '범주',
           'param': '공정 조건', 'setpoint': '설정값', 'ignore': '제외', 'text': '메모 · 문장'}
MIN_LEVEL = 20          # 범주 값 · 구간이 이보다 적으면 비교하지 않는다


def read_table(src, name: str = '') -> pd.DataFrame:
    """국내 현장 파일: UTF-8(BOM) · CP949(엑셀 «CSV 저장» 기본) · .xlsx/.xls 를 모두 받는다. src = 경로 또는 bytes"""
    import io
    raw = src if isinstance(src, (bytes, bytearray)) else open(src, 'rb').read()
    nm = (name or (src if isinstance(src, str) else str(getattr(src, 'name', '')))).lower()
    if nm.endswith(('.xlsx', '.xlsm', '.xls')) or raw[:4] == b'PK\x03\x04':
        return pd.read_excel(io.BytesIO(raw))
    for enc in ('utf-8-sig', 'cp949', 'euc-kr'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode('utf-8', errors='replace')
    sep = '\t' if text[:2000].count('\t') > text[:2000].count(',') else ','
    return pd.read_csv(io.StringIO(text), sep=sep)


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors='coerce')


def _ztest(e1, n1, e0, n0) -> float:
    """두 비율 차이의 z (구간 불량률 vs 나머지)"""
    if n1 <= 0 or n0 <= 0:
        return 0.0
    p = (e1 + e0) / (n1 + n0)
    se = math.sqrt(max(p * (1 - p), 1e-12) * (1 / n1 + 1 / n0))
    return (e1 / n1 - e0 / n0) / se if se > 0 else 0.0


def _auc(score, y) -> float | None:
    m = ~np.isnan(score)
    score, y = score[m], y[m]
    pos, neg = y > 0, y <= 0
    if pos.sum() < 2 or neg.sum() < 2:
        return None
    r = pd.Series(score).rank().to_numpy()
    return float((r[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * neg.sum()))


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return '-'
    a = abs(v)
    return f'{v:,.0f}' if a >= 100 else f'{v:,.1f}' if a >= 10 else f'{v:,.2f}' if a >= 1 else f'{v:.3g}'


# ── 데이터 품질
def quality(df: pd.DataFrame, S: dict) -> dict:
    miss = df.isna().mean()
    const = [c for c in df if df[c].nunique(dropna=True) <= 1]
    tcol = next((c for c, s in S.items() if s.role == 'time'), '')
    trange = None
    if tcol:
        t = pd.to_datetime(df[tcol], errors='coerce')
        if t.notna().any():
            trange = [str(t.min())[:16], str(t.max())[:16]]
    return {'rows': int(len(df)), 'cols': int(df.shape[1]), 'missing': float(df.isna().to_numpy().mean()),
            'missing_cols': {c: float(v) for c, v in miss.sort_values(ascending=False).items() if v >= 0.2},
            'constant_cols': const, 'duplicate_rows': int(df.duplicated().sum()), 'time_col': tcol, 'time_range': trange,
            'id_col': next((c for c, s in S.items() if s.role == 'id'), '')}


# ── 원인 후보 (칼럼 하나씩)
def factor(c: str, s, x: pd.Series, y: np.ndarray, n: np.ndarray) -> dict | None:
    tot_e, tot_n = y.sum(), n.sum()
    if s.role == 'category' or (s.role == 'param' and _num(x).notna().mean() < 0.9):
        lv = x.astype(str).fillna('(빈 값)')
        groups = [(v, m) for v, m in ((v, (lv == v).to_numpy()) for v in lv.value_counts().index[:30]) if m.sum() >= MIN_LEVEL]
        kind = 'category'
    else:
        v = _num(x)
        if v.nunique() < 4:
            groups = [(f'{u:g}', (v == u).to_numpy()) for u in sorted(v.dropna().unique())]
        else:
            try:
                b = pd.qcut(v, 4, duplicates='drop')
            except ValueError:
                return None
            groups = [(f'{_fmt(float(iv.left))} ~ {_fmt(float(iv.right))}', (b == iv).to_numpy()) for iv in b.cat.categories]
        groups = [(g, m) for g, m in groups if m.sum() >= MIN_LEVEL]
        kind = 'numeric'
    if len(groups) < 2:
        return None
    rows = []
    for g, m in groups:
        e1, n1 = y[m].sum(), n[m].sum()
        rows.append({'level': str(g), 'rows': int(m.sum()), 'rate': float(e1 / n1) if n1 else 0.0,
                     'z': _ztest(e1, n1, tot_e - e1, tot_n - n1), 'events': float(e1), 'n': float(n1)})
    w = max(rows, key=lambda r: r['z'])
    if w['z'] < 2.0 or w['events'] < 3:
        return None
    rest_rate = (tot_e - w['events']) / max(tot_n - w['n'], 1e-9)
    out = {'column': c, 'label': s.label or c, 'kind': kind, 'quantity': s.quantity, 'worst': w['level'], 'worst_rate': w['rate'],
           'rest_rate': float(rest_rate), 'lift': float(w['rate'] / max(rest_rate, 1e-9)), 'z': w['z'],
           'excess': float(max(w['rate'] - rest_rate, 0) * w['n']), 'levels': rows}
    if kind == 'numeric':
        out['auc'] = _auc(_num(x).to_numpy(float), (y / n > 0).astype(float) if n.max() <= 1 else (y / n > np.median(y / n)).astype(float))
    return out


# ── AI 신호 (학습 0건: 다른 공정에서 배운 공통 효과만으로)
def ai_signals(df: pd.DataFrame, meta: dict, model, process: str, overrides=None) -> dict | None:
    try:
        e = encode(Table(process, process, df.reset_index(drop=True), meta or {}, {}, '', {}, 'row'), overrides=overrides)
    except Exception as ex:          # noqa: BLE001 — 결과 칼럼이 없거나 모양이 특이해도 리포트는 계속
        return {'error': str(ex)}
    Xs = standardize(e.X.reindex(columns=SLOTS, fill_value=0.0))[SLOTS]
    beta = np.asarray(model.m[:model.K]) if model is not None else np.zeros(len(SLOTS))
    z = Xs.to_numpy() @ beta
    yb = (e.y > 0).astype(float) if e.n.max() <= 1 else (e.y / e.n > np.median(e.y / e.n)).astype(float)
    bad, good = yb > 0, yb <= 0
    sig = []
    if bad.any() and good.any():
        diff = Xs[bad].mean() - Xs[good].mean()
        for s, d in diff.sort_values(ascending=False).items():
            if d <= 0.15 or len(sig) >= 6:
                continue
            src = ''
            if s in e.detail and not s.startswith('up:'):
                cols, am = e.detail[s]
                vals, cnt = np.unique(np.asarray(am)[bad], return_counts=True)
                c = cols[int(vals[cnt.argmax()])] if len(vals) else ''
                src = (e.senses[c].label or c) if c in e.senses else str(c)
            sig.append({'slot': s, 'name': SLOT_KO.get(s, s), 'gap_sd': float(d), 'source': src})
    return {'zero_shot_auc': _auc(z, yb), 'signals': sig, 'accum_cols': e.info.get('accum_cols', []), 'entity': e.info.get('entity', ''),
            'texts': e.info.get('texts', []), 'leak': e.leak}


def readiness(q: dict, has_outcome: bool, events: float, n_param: int) -> dict:
    checks = [('결과(불량) 칼럼', has_outcome, 30), ('행 200개 이상', q['rows'] >= 200, 15), ('불량 20건 이상', events >= 20, 15),
              ('공정 조건 칼럼 3개 이상', n_param >= 3, 15), ('빈 값 20% 미만', q['missing'] < 0.2, 10), ('시간 칼럼', bool(q['time_col']), 10),
              ('중복 행 1% 미만', q['duplicate_rows'] <= 0.01 * max(q['rows'], 1), 5)]
    return {'score': int(sum(w for _, ok, w in checks if ok)), 'checks': [{'item': k, 'ok': bool(ok), 'weight': w} for k, ok, w in checks]}


def diagnose(df: pd.DataFrame, meta: dict | None = None, unit_cost: float | None = None, model=None,
             process: str = 'customer', overrides: dict | None = None, name: str = '') -> dict:
    if len(df) == 0:
        raise ValueError('행이 없습니다')
    df = df.reset_index(drop=True)
    S = sense_table(df, meta or {}, overrides=overrides)
    q = quality(df, S)
    cols = [{'column': c, 'role': s.role, 'role_ko': ROLE_KO.get(s.role, s.role), 'quantity': s.quantity, 'stat': s.stat,
             'confidence': round(float(s.confidence), 2), 'why': '; '.join(s.why)[:120]} for c, s in S.items()]
    oc = next((c for c, s in S.items() if s.role == 'outcome'), '')
    res = {'name': name or 'data.csv', 'created': datetime.now().strftime('%Y-%m-%d %H:%M'), 'quality': q, 'columns': cols,
           'outcome': oc, 'factors': [], 'ai': None, 'unit_cost': unit_cost}
    n_param = sum(1 for s in S.values() if s.role == 'param')
    if not oc:
        res['readiness'] = readiness(q, False, 0, n_param)
        res['next'] = ['결과(불량 · 합격 여부) 칼럼이 있는 데이터를 추가로 받아야 원인 분석을 할 수 있습니다.']
        return res
    y = outcome_vector(df[oc], S[oc])
    ex = next((c for c, s in S.items() if s.role == 'exposure'), '')
    n = _num(df[ex]).fillna(1).clip(lower=1).to_numpy(float) if ex else np.ones(len(df))
    if not ex and y.max() > 1:
        n = np.maximum(n, y)
    yb = (y > 0).astype(float) if y.max() <= 1 else (y / n > np.median(y / n)).astype(float)
    leak = set(leakage_guard(df, S, yb))
    res.update({'exposure': ex, 'events': float(y.sum()), 'units': float(n.sum()), 'defect_rate': float(y.sum() / n.sum()), 'leak': sorted(leak)})
    fs = []
    for c, s in S.items():
        if c in leak or s.role not in ('param', 'category') or c == oc:
            continue
        f = factor(c, s, df[c], y, n)
        if f:
            fs.append(f)
    fs.sort(key=lambda f: -f['z'])
    res['factors'] = fs[:10]
    if unit_cost:
        res['copq'] = {'total': float(y.sum() * unit_cost), 'top_excess': float(fs[0]['excess'] * unit_cost) if fs else 0.0}
    res['ai'] = ai_signals(df, meta or {}, model, process, overrides)
    res['readiness'] = readiness(q, True, float(y.sum()), n_param)
    nxt = []
    if fs:
        f = fs[0]
        nxt.append(f"«{f['label']}» {f['worst']} 구간의 불량률이 나머지의 {f['lift']:.1f}배 — 현장에서 이 구간의 작업 조건을 먼저 확인")
    if res['readiness']['score'] < 70:
        nxt.append('부족한 항목을 보완한 뒤 결과 칼럼·수량·단위·측정 시점을 현장 담당자와 확인하세요.')
    else:
        nxt.append('파일의 기본 점검 항목을 충족했습니다. 현장 담당자의 칼럼 확인과 별도 기간 검증 뒤 적용 범위를 결정하세요. 준비도 점수는 예측 성능을 뜻하지 않습니다.')
    res['next'] = nxt
    res['report_8d'] = eight_d(res)
    return res


def eight_d(r: dict) -> dict | None:
    """품질 클레임 대응용 원인 분석 보고서 초안 (8D 양식의 D2 문제 · D4 원인 · D5 대책 · D6 검증 · D7 재발 방지)

    자동차 부품 협력사가 고객사(OEM)에 내는 8D 보고서에서 데이터로 채울 수 있는 칸만 채운다.
    D1 팀 · D3 임시 조치 · D8 종결은 사람이 쓴다. 원인은 «후보» — 현장 확인 뒤 확정."""
    fs = r.get('factors') or []
    if not r.get('outcome'):
        return None
    q = r['quality']
    d2 = {'period': ' ~ '.join(q['time_range']) if q.get('time_range') else '', 'units': r.get('units'), 'events': r.get('events'),
          'rate': r.get('defect_rate'), 'loss': (r.get('copq') or {}).get('total'), 'outcome': r['outcome']}
    d4, d5, d6 = [], [], []
    for f in fs[:3]:
        best = min(f['levels'], key=lambda x: x['rate'])
        d4.append({'column': f['label'], 'worst': f['worst'], 'worst_rate': f['worst_rate'], 'rest_rate': f['rest_rate'], 'lift': f['lift'], 'z': f['z'],
                   'excess': f['excess']})
        d5.append({'column': f['label'], 'avoid': f['worst'], 'target': None, 'target_rate': None,
                   'comparison_level': best['level'], 'comparison_rate': best['rate'],
                   'action': f"«{f['label']}» {f['worst']} 구간의 품목·설비·작업 이력을 비교하고 원인 가설을 확인. 운전 조건은 공정 담당자 승인 후 검증"})
        d6.append({'column': f['label'], 'metric': '해당 구간 불량률', 'before': f['worst_rate'], 'goal': None,
                   'comparison_rate': f['rest_rate'], 'plan': '같은 품목·설비의 별도 기간 또는 승인된 비교 시험으로 재검증'})
    sig = [s['name'] + (f" ({s['source']})" if s.get('source') else '') for s in ((r.get('ai') or {}).get('signals') or [])[:3]]
    return {'d2': d2, 'd4': d4, 'd4_ai': sig, 'd5': d5, 'd6': d6, 'goal_rate': None,
            'status': '원인 미확정 · 현장 검증 필요',
            'd7': ['검증된 원인과 승인된 조치만 작업 표준에 반영', '조치 전후의 품목·설비·생산량·불량 수를 기록', '재발 여부와 실제 비용 변화를 담당자가 확인한 뒤 종결'],
            'manual': ['D1 팀 구성', 'D3 임시 조치 (격리 · 전수 검사)', 'D8 종결 · 승인']}


# ── HTML 리포트 (단독 파일 · 인쇄용)
CSS = """
*{box-sizing:border-box}body{margin:0;font:14px/1.55 'Pretendard','Noto Sans KR','Malgun Gothic',system-ui,sans-serif;color:#1d2330;background:#fff}
.wrap{max-width:960px;margin:0 auto;padding:32px 24px 56px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid #d9dde5}
.sub{color:#5b6475;font-size:13px}table.sum{margin-top:18px}table.sum td{font-size:18px;font-weight:600;border-bottom:0;padding-top:2px}table.sum th{background:none;border-bottom:0;color:#5b6475;font-weight:400;font-size:12px;padding-bottom:0}
th{white-space:nowrap}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 10px;border-bottom:1px solid #eceef2;text-align:left;vertical-align:top}
th{background:#f5f6f8;font-weight:600;color:#3a4252}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:8px;background:#e7ebf3;border-radius:4px;overflow:hidden;min-width:80px}.bar i{display:block;height:100%;background:#2f6fde}
.ok{color:#18794e;font-weight:600}.no{color:#b42318;font-weight:600}.tag{display:inline-block;padding:1px 8px;border-radius:10px;background:#eef2fb;color:#2b4c9b;font-size:12px}
.note{color:#5b6475;font-size:12px;margin-top:6px}ol{margin:6px 0 0;padding-left:20px}
@media print{.wrap{padding:0}h2{break-after:avoid}table{break-inside:auto}tr{break-inside:avoid}}
@media (max-width:640px){table.sum td{font-size:15px}.wrap{padding:24px 16px 40px}}
"""


def render_html(r: dict, brand: str = 'MILG') -> str:
    e = html.escape
    q = r['quality']
    pct = lambda v: '-' if v is None else f'{v * 100:.1f}%'   # noqa: E731
    k = [('행', f"{q['rows']:,}"), ('칼럼', f"{q['cols']}"), ('불량률', pct(r.get('defect_rate'))), ('준비도', f"{r['readiness']['score']} / 100")]
    if r.get('copq'):
        k.insert(3, ('불량 비용 추정', f"{r['copq']['total']:,.0f}원"))
    out = [f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>{e(brand)} 데이터 진단</title><style>{CSS}</style></head><body><div class='wrap'>",
           f"<div class='sub'>{e(brand)} 데이터 진단 리포트 · {e(r['created'])}</div><h1>{e(r['name'])}</h1>",
           f"<div class='sub'>기간 {e(' ~ '.join(q['time_range'])) if q.get('time_range') else '-'} · 결과 칼럼 {e(r['outcome'] or '없음')}</div>",
           "<table class='sum'><tr>" + ''.join(f'<th>{e(a)}</th>' for a, _ in k) + '</tr><tr>' + ''.join(f'<td>{e(b)}</td>' for _, b in k) + '</tr></table>']
    out.append('<h2>요약 · 다음 할 일</h2><ol>' + ''.join(f'<li>{e(t)}</li>' for t in r.get('next', [])) + '</ol>')
    out.append("<p>탐색적 연관 분석 · 원인 미확정. 비용은 불량 수 × 입력한 건당 비용이며 실제 절감액이 아닙니다.</p>")
    d = eight_d(r)
    if d and d['d4']:
        d2 = d['d2']
        out.append("<h2>원인 분석 보고서 초안 (8D)</h2><table><tr><th>항목</th><th>내용</th></tr>"
                   f"<tr><td>D2 문제 기술</td><td>{e(d2['period'] or '기간 미상')} · {_fmt(d2['units'])}개 중 불량 {_fmt(d2['events'])}건 ({pct(d2['rate'])})"
                   + (f" · 손실 {d2['loss']:,.0f}원" if d2.get('loss') else '') + '</td></tr>'
                   + ''.join(f"<tr><td>D4 근본 원인 후보 {i}</td><td>«{e(x['column'])}» {e(x['worst'])} 구간 불량률 {pct(x['worst_rate'])} — 나머지 {pct(x['rest_rate'])}의 {x['lift']:.1f}배 (z {x['z']:.1f})</td></tr>"
                             for i, x in enumerate(d['d4'], 1))
                   + (f"<tr><td>D4 AI 신호</td><td>{e(' · '.join(d['d4_ai']))}</td></tr>" if d['d4_ai'] else '')
                   + ''.join(f"<tr><td>D5 대책 후보 {i}</td><td>{e(x['action'])}</td></tr>" for i, x in enumerate(d['d5'], 1))
                   + ''.join(f"<tr><td>D6 검증 계획 {i}</td><td>{e(x['column'])}: {e(x['plan'])}. 현재 구간 {pct(x['before'])}, 나머지 {pct(x['comparison_rate'])} (관측값). 목표·절감액은 검증 후 확정</td></tr>" for i, x in enumerate(d['d6'], 1))
                   + ''.join(f"<tr><td>D7 재발 방지</td><td>{e(t)}</td></tr>" for t in d['d7'])
                   + f"<tr><td>사람이 작성</td><td>{e(' · '.join(d['manual']))}</td></tr></table>"
                   "<div class='note'>데이터로 채울 수 있는 칸만 자동 작성. 원인은 후보이며 현장 확인 뒤 확정합니다.</div>")
    fs = r.get('factors') or []
    if fs:
        mx = max(f['lift'] for f in fs)
        out.append("<h2>불량 원인 후보</h2><table><tr><th>순위</th><th>칼럼</th><th>나쁜 구간</th><th class='n'>구간 불량률</th><th class='n'>나머지</th><th class='n'>배수</th><th></th>"
                   + ("<th class='n'>구간 차이 비용 환산</th>" if r.get('unit_cost') else "<th class='n'>구간 차이 수량</th>") + '</tr>')
        for i, f in enumerate(fs, 1):
            ex = f"{f['excess'] * r['unit_cost']:,.0f}원" if r.get('unit_cost') else _fmt(f['excess'])
            out.append(f"<tr><td>{i}</td><td>{e(f['label'])}</td><td>{e(f['worst'])}</td><td class='n'>{pct(f['worst_rate'])}</td><td class='n'>{pct(f['rest_rate'])}</td>"
                       f"<td class='n'>{f['lift']:.1f}×</td><td><div class='bar'><i style='width:{100 * f['lift'] / mx:.0f}%'></i></div></td><td class='n'>{ex}</td></tr>")
        out.append("</table><p>칼럼별 탐색 비교(z ≥ 2, 다중 비교 미보정)입니다. 구간 차이는 (해당 구간 불량률 − 나머지 불량률) × 구간 수량. 후보 간 중복되므로 합산할 수 없으며, 조치 효과나 절감액을 예측하지 않습니다.</p>")
    elif r['outcome']:
        out.append("<h2>불량 원인 후보</h2><div class='note'>이 분석에서 뚜렷한 차이를 찾지 못했습니다. 표본·누락된 조건·품목 구성을 확인해야 하며 원인이 없다는 뜻은 아닙니다.</div>")
    ai = r.get('ai') or {}
    if ai.get('signals'):
        za = ai.get('zero_shot_auc')
        out.append(f"<h2>AI 신호 (학습 0건)</h2><div class='sub'>다른 공정에서 배운 공통 효과만으로 계산 · 불량 구분력 AUC {za:.2f}</div>" if za is not None else "<h2>AI 신호 (학습 0건)</h2>")
        out.append("<table><tr><th>신호</th><th>주로 나온 칼럼</th><th class='n'>불량 행이 평소보다 (σ)</th></tr>"
                   + ''.join(f"<tr><td>{e(s['name'])}</td><td>{e(s['source'] or '-')}</td><td class='n'>+{s['gap_sd']:.2f}</td></tr>" for s in ai['signals']) + '</table>')
        extra = []
        if ai.get('accum_cols') or ai.get('entity'):
            extra.append(f"적산: {e(', '.join(ai.get('accum_cols') or []) or '-')} (묶음 단위 {e(ai.get('entity') or '-')})")
        if ai.get('texts'):
            extra.append(f"메모 · 문장 칼럼: {e(', '.join(map(str, ai['texts'])))}")
        if extra:
            out.append("<div class='note'>" + ' · '.join(extra) + '</div>')
    out.append("<h2>칼럼 해석</h2><table><tr><th>칼럼</th><th>역할</th><th>물리량</th><th class='n'>확신도</th><th>근거</th></tr>"
               + ''.join(f"<tr><td>{e(c['column'])}</td><td><span class='tag'>{e(c['role_ko'])}</span></td><td>{e(QUANTITY.get(c['quantity'], (c['quantity'] or '-',))[0])}</td>"
                         f"<td class='n'>{c['confidence']:.2f}</td><td>{e(c['why'])}</td></tr>" for c in r['columns']) + '</table>')
    if r.get('leak'):
        out.append(f"<div class='note'>결과를 사실상 그대로 담은 칼럼은 원인 분석에서 뺐습니다: {e(', '.join(r['leak']))}</div>")
    rd = r['readiness']
    out.append("<h2>데이터 준비도</h2><table><tr><th>항목</th><th>상태</th><th class='n'>배점</th></tr>"
               + ''.join(f"<tr><td>{e(c['item'])}</td><td class='{'ok' if c['ok'] else 'no'}'>{'충족' if c['ok'] else '부족'}</td><td class='n'>{c['weight']}</td></tr>" for c in rd['checks']) + '</table>')
    dq = []
    if q['missing_cols']:
        dq.append('빈 값 20% 이상: ' + ', '.join(f'{c} {v * 100:.0f}%' for c, v in list(q['missing_cols'].items())[:8]))
    if q['constant_cols']:
        dq.append('값이 하나뿐인 칼럼: ' + ', '.join(q['constant_cols'][:8]))
    if q['duplicate_rows']:
        dq.append(f"중복 행 {q['duplicate_rows']:,}개")
    if dq:
        out.append("<div class='note'>" + e(' · '.join(dq)) + '</div>')
    out.append(f"<div class='note' style='margin-top:32px'>{e(brand)} Trace · 분석 대상은 제출된 파일입니다. 데이터 처리·보관 범위는 계약된 실행 환경의 정책을 따릅니다.</div></div></body></html>")
    return ''.join(out)
