"""학습 → 검증 → 결과물

  ai/state/model.json        누적 학습 상태 (서버가 이어서 갱신)
  web/data/ai_model.json     MES 화면용 요약 (칼럼 해석 · 검증 결과 · 공통 효과 · LOT 별 원인 설명)
  ai/reports/report.md       사람이 읽는 검증 보고서
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

from . import __version__
from .datasets import ROOT
from .engine import build, cold_start, injection, prequential
from .features import SLOT_KO, SLOTS
from .holdout import evaluate as holdout_eval
from .truth import score, truth

STATE = ROOT / 'ai' / 'state' / 'model.json'
WEB = ROOT / 'web' / 'data' / 'ai_model.json'
REPORT = ROOT / 'ai' / 'reports' / 'report.md'
COLD = ['ai4i', 'insp', 'bands', 'secom']
LOT_PROCS = ['heat', 'press', 'weld']


def _r(x, d=3):
    if isinstance(x, (float, np.floating)):
        x = float(x)
        if not np.isfinite(x):
            return None
        return float(f'{x:.3g}') if 0 < abs(x) < 0.01 else round(x, d)
    if isinstance(x, dict):
        return {k: _r(v, d) for k, v in x.items()}
    if isinstance(x, list):
        return [_r(v, d) for v in x]
    return x


def schema_block(E):
    out, ok, tot = {}, 0, 0
    for k, e in E.items():
        rows = []
        for c, s in e.senses.items():
            lab = truth(k, c)
            good = score(s, lab) if lab else None
            if lab:
                tot += 1; ok += int(good)
            if k == 'secom' and c.startswith('f') and len(rows) > 12:
                continue
            rows.append({'col': c, 'label': s.label, 'unit': s.unit, 'role': s.role, 'quantity': s.quantity, 'stat': s.stat, 'conf': s.confidence,
                         'why': s.why[:3], 'source': s.source, 'leak': c in e.leak, 'ok': good})
        out[k] = {'n_cols': len(e.senses), 'rows': rows, 'roles': {r: sum(1 for s in e.senses.values() if s.role == r) for r in ('param', 'category', 'outcome', 'outcome_detail', 'exposure', 'id', 'time', 'setpoint', 'ignore')}}
    return out, ok / max(tot, 1), tot


def explain_rows(M, E, k, w):
    e = E[k]
    X = e.Xs.to_numpy()
    mu, sd = M.predict(k, X, e.n)
    g = M.gate_weight(k)
    base = M.base_rate(k) * e.n
    out = []
    idc = next((c for c, sv in e.senses.items() if sv.role == 'id' and c != e.table.lot_col), e.table.lot_col)
    ids = e.table.df[idc].astype(str).tolist()
    lots = e.table.df[e.table.lot_col].astype(str).tolist() if e.table.lot_col else [''] * len(ids)
    pname = {kk: ee.table.process for kk, ee in E.items()}
    for i in range(len(e.y)):
        con = []
        for r in M.explain(k, X[i])[:5]:
            s = r['slot']
            src = ''
            if s in e.detail and not s.startswith('up:'):
                cols, am = e.detail[s]
                c = cols[int(am[i])]
                src = (e.senses[c].label or c)
            elif s.startswith('up:'):
                src = ' · '.join(pname.get(x, x) for x in e.detail.get('_up', {}).get(i, []))
            con.append({'slot': s, 'x': r['x'], 'c': r['contrib'], 'lo': r['lo'], 'hi': r['hi'], 'src': src})
        ex = float(g * mu[i] + (1 - g) * base[i])
        r = {'id': ids[i], 'lot': lots[i], 'n': float(e.n[i]), 'y': float(e.y[i]), 'exp': ex, 'ratio': float(e.y[i] / max(ex, 1e-9)), 'top': con}
        if e.n.max() <= 1:
            r['p'] = float(1 - np.exp(-ex))          # 한 개 단위 데이터: 기대 사건 수 → 불량 확률
        out.append(r)
    return out


def run(fast: bool = False, write: bool = True) -> dict:
    E = build()
    M, pre, drift, _ = prequential(E)
    cs = {} if fast else {k: cold_start(E, k) for k in COLD if k in E}
    inj = [] if fast else injection()
    hold = holdout_eval()
    sch, in_acc, in_n = schema_block(E)
    shared = sorted(M.shared_effects(), key=lambda r: -abs(r['beta']))
    procs = {}
    for k in M.procs:
        pe = M.process_effects(k)
        procs[k] = {'name': E[k].table.process, 'base_rate': pe['base_rate'], 'gate': M.gate_weight(k), 'dispersion': M.dispersion(k),
                    'effects': sorted([r for r in pe['effects'] if r['slot'] != 'up:has' and (r['lo'] > 1 or r['hi'] < 1)], key=lambda r: -abs(r['beta']))[:6]}
    art = {
        'version': __version__, 'model_version': M.version, 'built': dt.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'slots': SLOTS, 'slot_ko': SLOT_KO,
        'sources': [{'key': k, 'name': e.table.process, 'rows': len(e.y), 'events': float(e.y.sum()), 'exposure': float(e.n.sum()), 'unit': e.table.unit_kind,
                     'source': e.table.source, 'linked': int(e.X['up:has'].sum())} for k, e in E.items()],
        'schema': sch, 'schema_eval': {'in_sample': in_acc, 'in_sample_n': in_n, 'holdout_n': hold['n'], 'holdout': hold['accuracy'],
                                        'holdout_wrong': [r for r in hold['rows'] if not r['ok']]},
        'prequential': pre, 'drift': drift[:60], 'drift_n': len(drift), 'history': M.history,
        'cold_start': cs, 'injection': inj, 'shared': shared, 'procs': procs,
        'lots': {k: explain_rows(M, E, k, None) for k in LOT_PROCS if k in E},
        'parts': {'insp': [r for r in explain_rows(M, E, 'insp', None) if r['y'] > 0]} if 'insp' in E else {},
    }
    art = _r(art)
    if write:
        STATE.parent.mkdir(parents=True, exist_ok=True); M.save(STATE)
        WEB.write_text(json.dumps(art, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        REPORT.parent.mkdir(parents=True, exist_ok=True); REPORT.write_text(report_md(art), encoding='utf-8')
    return art


def report_md(a: dict) -> str:
    L = [f"# MLNG 공정 통합 AI — 검증 보고서", '', f"버전 {a['version']} · 누적 갱신 {a['model_version']}회 · 생성 {a['built']}", '',
         '## 1. 학습 데이터', '', '| 공정 | 행 | 사건(불량) | 앞 공정 연결 | 출처 |', '|---|---:|---:|---:|---|']
    for s in a['sources']:
        L.append(f"| {s['name']} | {s['rows']:,} | {s['events']:,.0f} | {s['linked']} | {s['source'][:60]} |")
    se = a['schema_eval']
    L += ['', '## 2. 칼럼 의미 해석', '', f"- 학습 데이터 칼럼(사전을 만들며 본 것): {se['in_sample']:.1%} ({se['in_sample_n']}개)",
          f"- **처음 보는 칼럼 이름 {se['holdout_n']}개 (사출 · 주조 · 도장 · 용접 · 가공 · 조립 · 반도체): {se['holdout']:.1%}**", '', '틀린 것:', '']
    for r in se['holdout_wrong']:
        L.append(f"- `{r['column']}` {r['label']} → {r['role']} {r['quantity']} (정답 {r['truth']})")
    L += ['', '## 3. 시간순 누적 학습 (먼저 예측 → 채점 → 학습)', '', '이탈도 개선 = 1 − 모델 이탈도 / 기준율(지금까지의 불량률) 이탈도. 게이트 = 모델을 믿는 정도 (지금까지 기준율보다 나았으면 1).', '',
          '| 공정 | 평가 행 | 사건 | 모델 | 게이트 적용 | 게이트 | 순위 지표 |', '|---|---:|---:|---:|---:|---:|---:|']
    for k, r in a['prequential'].items():
        rk = f"AUC {r['auc']:.2f}" if r.get('auc') is not None else f"순위상관 {r.get('spearman') or 0:.2f}"
        L.append(f"| {a['procs'][k]['name']} | {r['n_eval']:,} | {r['events']:,.0f} | {r['dev_skill']:+.1%} | {r['gated_skill']:+.1%} | {r['gate']:.2f} | {rk} |")
    if a['cold_start']:
        L += ['', '## 4. 처음 보는 공정 (다른 공정으로 미리 배운 모델 vs 그 공정만으로 처음부터)', '', '시험 = 그 공정의 마지막 40%. 값 = 이탈도 개선.', '']
        for k, rows in a['cold_start'].items():
            z = rows[0].get('zero_shot_auc')
            L.append(f"**{a['procs'][k]['name']}** — 학습 0개 순위 지표 {z if z is not None else '—'}")
            L.append('')
            L.append('| 그 공정 학습 행 | 미리 배운 모델 | 처음부터 |')
            L.append('|---:|---:|---:|')
            for r in rows[1:]:
                L.append(f"| {r['n_train']} | {r['transfer']:+.1%} | {r['fresh']:+.1%} |")
            L.append('')
    if a['injection']:
        L += ['## 5. 공정 간 원인 추적 검증 (정답을 심은 가상 데이터)', '', '앞 공정 로 온도 이탈이 뒤 공정 불량을 키우도록 만든 데이터에서, 모델이 «앞 공정 · 온도 이탈» 효과를 90% 구간이 1 보다 크게 찾아낸 비율.', '',
              '| 심은 효과 (1σ 당 배수) | 찾아낸 비율 | 슬롯 중 순위 (중앙값) |', '|---:|---:|---:|']
        for r in a['injection']:
            L.append(f"| ×{r['effect']} | {r['detected']:.0%} | {r['median_rank']:.0f} |")
        L.append('')
        L.append('×1.0 (효과 없음) 에서 찾아낸 비율 = 없는 원인을 만들어 낸 비율 (거짓 경보).')
    L += ['', '## 6. 모든 공정에 통하는 공통 효과 (90% 구간이 1 을 벗어난 것)', '', '| 슬롯 | 배수 (평소보다 1σ) | 90% 구간 |', '|---|---:|---|']
    for r in a['shared']:
        if r['lo'] > 1 or r['hi'] < 1:
            L.append(f"| {a['slot_ko'].get(r['slot'], r['slot'])} | ×{r['rr']:.2f} | {r['lo']:.2f} ~ {r['hi']:.2f} |")
    L += ['', '## 한계', '', '- MLNG 데모 공장의 공정 연결(LOT 추적)은 서로 다른 공개 데이터셋을 한 흐름으로 이은 것이라, 실제 공정 간 인과는 들어 있지 않다. 공정 간 원인 추적은 5장의 가상 데이터로 검증했다.',
          '- 열처리처럼 조건 기록으로 불량이 설명되지 않는 공정은 게이트가 0 쪽으로 가서 «공정 기준율» 예측으로 물러난다 (모델이 기준보다 나쁘게 예측하지 않도록).',
          '- 칼럼 해석의 학습 데이터 정확도는 사전을 만들면서 본 칼럼이라 높게 나온다. 실제 성능은 «처음 보는 칼럼» 수치로 본다.']
    return '\n'.join(L) + '\n'
