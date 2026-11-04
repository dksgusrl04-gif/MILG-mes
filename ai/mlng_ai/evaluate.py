"""평가 — 운영 모델 교체 판단 · 4방식 비교 실험이 같이 쓰는 기준.

불량이 드문 현장에서는 전체 정확도보다 «알림 허용량 안에서 불량을 얼마나 잡는가» 가 중요하다.
  alert_metrics   위험도 상위 budget(기본 10%)만 알림 → 잡은 불량 비율 · 알림 적중률 · 무작위 대비 배수
  compare4        같은 평가 구간 · 같은 알림 허용량에서
                    ① 규칙(가장 크게 벗어난 조건 |z|) ② 자체 학습(그 공정 데이터만)
                    ③ 공통 모델 그대로(학습 0건) ④ 공통 모델 + 그 공정 추가 학습
                  을 학습량별로 비교한다. 평가 구간은 학습에 쓰지 않은 «이후 기간».
"""
from __future__ import annotations

import numpy as np

from .engine import auc, chunks, is_binary, new_model, poisson_dev, rate_to_mu, spearman, _fit
from .model import CumulativeModel


def alert_metrics(score, y, n=None, budget: float = 0.1) -> dict:
    score = np.asarray(score, float)
    y = np.asarray(y, float)
    n = np.ones_like(y) if n is None else np.asarray(n, float)
    k = max(1, int(round(len(y) * budget)))
    order = np.argsort(-score, kind='stable')[:k]
    ev = y.sum()
    cap = float(y[order].sum() / ev) if ev > 0 else float('nan')
    prec = float((y[order] > 0).mean())
    base = float((y > 0).mean())
    return {'budget': budget, 'alerts': k, 'capture': cap, 'precision': prec, 'lift': prec / base if base > 0 else float('nan')}


def rank_quality(score, y, n) -> float:
    return auc(score, y) if np.max(n) <= 1 else spearman(score, np.asarray(y) / np.asarray(n))


def score_model(M: CumulativeModel, p: str, X, n):
    """모델의 위험도 — 기대 불량 / 수량 (게이트 없이: 모델끼리 같은 조건으로 비교)"""
    mu = M.predict(p, X, n)[0]
    return np.asarray(mu, float) / np.maximum(np.asarray(n, float), 1e-9)


def skill(M: CumulativeModel, p: str, X, y, n) -> float | None:
    """기준율(그 공정 평균 불량률) 대비 예측 오차 감소율"""
    bi = bool(np.max(n) <= 1 and np.all((y == 0) | (y == 1)))
    d0 = poisson_dev(y, M.base_rate(p) * n, bi)
    if d0 <= 0:
        return None
    return float(1 - poisson_dev(y, M.predict(p, X, n)[0], bi) / d0)


def compare4(E, target: str, sizes=(0.05, 0.1, 0.2, 0.4), test_frac: float = 0.4, budget: float = 0.1) -> dict:
    e = E[target]
    Xs = e.Xs.to_numpy()
    N = len(e.y)
    cut = int(N * (1 - test_frac))
    te = np.arange(cut, N)
    yte, nte, Xte = e.y[te], e.n[te], Xs[te]
    others = [k for k in E if k != target]
    common = _fit(new_model(E), E, others)
    snap = common.to_dict()
    beta = common.m[:common.K]
    rule = e.X['all_max'].to_numpy()[te] if 'all_max' in e.X else np.zeros(len(te))

    def pack(score, sk=None):
        r = alert_metrics(score, yte, nte, budget)
        r['rank'] = rank_quality(score, yte, nte)
        r['skill'] = sk
        return r

    rows = [{'n_train': 0, 'rule': pack(rule), 'common': pack(Xte @ beta)}]
    bi = is_binary(e)
    seen = set()
    for f in sizes:
        k = max(5, int(N * f))
        if k > cut:
            break
        if k in seen:
            continue
        seen.add(k)
        tr = np.arange(k)
        r0 = (e.y[tr].sum() + 0.5) / (e.n[tr].sum() + 1)
        row = {'n_train': k, 'events_train': float(e.y[tr].sum())}
        for name, M in (('local', new_model(E)), ('common_ft', CumulativeModel.from_dict(snap))):
            M.add_process(target, r0)
            for ix in chunks(k, max(1, min(5, k // 5))):
                M.update([(target, Xs[ix], e.y[ix], e.n[ix])])
            row[name] = pack(score_model(M, target, Xte, nte), skill(M, target, Xte, yte, nte))
        rows.append(row)
    return {'target': target, 'name': e.table.process, 'rows_total': N, 'test_rows': len(te), 'test_events': float(yte.sum()),
            'budget': budget, 'results': rows}


def compare4_md(res: list[dict]) -> str:
    pct = lambda v: '—' if v is None or v != v else f'{v * 100:.0f}%'   # noqa: E731
    out = ['# 4방식 비교 실험 — 같은 평가 구간 · 같은 알림 허용량', '',
           '알림 허용량: 위험도 상위 10%만 알림. 값 = 그 알림 안에 들어온 불량 비율(잡은 불량). 평가 구간은 학습에 쓰지 않은 이후 기간.', '']
    for r in res:
        if r['test_rows'] < 50 or r['test_events'] < 20:
            out += [f"## {r['name']} — 평가 {r['test_rows']}행 · 불량 {r['test_events']:.0f}", '', '평가 표본이 적어 판단 보류', '']
            continue
        out += [f"## {r['name']} — 평가 {r['test_rows']}행 · 불량 {r['test_events']:.0f}", '',
                '| 학습 행 | ① 규칙 | ② 자체 학습 | ③ 공통 그대로 | ④ 공통 + 추가 학습 |', '|---|---|---|---|---|']
        z = r['results'][0]
        out.append(f"| 0 | {pct(z['rule']['capture'])} | — | {pct(z['common']['capture'])} | — |")
        for x in r['results'][1:]:
            out.append(f"| {x['n_train']} | {pct(z['rule']['capture'])} | {pct(x['local']['capture'])} | {pct(z['common']['capture'])} | {pct(x['common_ft']['capture'])} |")
        out.append('')
    return '\n'.join(out)
