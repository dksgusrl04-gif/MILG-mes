/* 점용접 모듈 — SP-01 로봇 점용접 (보강 브래킷 부착)
 * 용접 품질 웹(weldweb)의 점용접 화면을 MILG 셸로 옮긴 것. 아크용접은 상업 데모에서 뺐다 (Polito 데이터, CC BY-NC).
 * 화면: 점용접 현황 · 검사 우선순위 · 안정 공정조건 · 작업 실적   /  상세: 타점 서랍 · 작업일 서랍
 */
(function () {
  'use strict';
  const M = window.MLNG, { esc, f, pct } = M;
  const W = () => M.S.weld || { lots: [], preds: [], windows: [], summary: {}, types: [] };
  const RD = () => M.CFG.RECENT_DAYS || 7;
  const detail = () => W().lots.find(l => l.has_detail) || {};
  const rowsByIdx = () => W().preds.slice().sort((a, b) => a.idx - b.idx);
  const ptId = r => `${detail().id}#${r.idx}`;
  const doneSet = () => new Set(M.inspections().filter(i => i.proc === 'weld').map(i => `${i.lot_id}#${i.target}`));

  // ── 공정 모니터 (용접 웹에서 옮김)
  const MON = [
    { k: 'current', name: '전류(kA)', label: '전류', unit: 'kA', d: 2, get: r => r.current },
    { k: 'force', name: '가압력(bar)', label: '가압력', unit: 'bar', d: 2, get: r => r.force },
    { k: 'i2t', name: '발열 I²t(kA²·ms)', label: '발열 I²t', unit: 'kA²·ms', d: 0, get: r => r.current * r.current * r.weld_time },
    { k: 'res', name: '등가저항 V/I(mΩ)', label: '등가저항', unit: 'mΩ', d: 3, get: r => r.voltage / r.current },
  ];
  let monK = 'current';
  const winOf = name => { const w = W().windows.find(x => x.variable === name); return w && String(w.judgment || '').startsWith('제안') ? w : null; };
  const noCaution = w => String(w.judgment || '').includes('주의 범위 = 관측 전체');
  function axis(vals, w) {
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (w) { const span = (w.caution_hi - w.caution_lo) || (hi - lo); lo = Math.max(lo, w.caution_lo - 2 * span); hi = Math.min(hi, w.caution_hi + 2 * span); }
    const pad = (hi - lo) * 0.06 || 1; return [lo - pad, hi + pad];
  }
  const tickVals = (lo, hi, n = 4) => Array.from({ length: n + 1 }, (_, i) => lo + (hi - lo) * i / n);
  function runChart(rows, v) {
    const Wd = 760, H = 250, L = 56, R = 10, T = 10, B = 26, w = winOf(v.name);
    const xs = rows.map(v.get), [lo, hi] = axis(xs, w), n = rows.length;
    const X = i => L + (Wd - L - R) * i / Math.max(1, n - 1), Y = y => T + (H - T - B) * (1 - (Math.min(hi, Math.max(lo, y)) - lo) / (hi - lo));
    const segs = [[]], k = 50; let sum = 0;
    xs.forEach((x, i) => { sum += x; if (i >= k) sum -= xs[i - k]; if (i < k - 1) return; const m = sum / k;
      if (m < lo || m > hi) { if (segs[segs.length - 1].length) segs.push([]); return; }
      segs[segs.length - 1].push(`${X(i).toFixed(1)},${Y(m).toFixed(1)}`); });
    const outC = x => w && !noCaution(w) && (x < w.caution_lo || x > w.caution_hi);
    const pts = rows.map((r, i) => { const x = xs[i], clip = x > hi || x < lo, cls = r.pred_defect ? 'bad' : outC(x) ? 'out' : '';
      return clip ? `<path class="pt ${cls || 'out'}" d="M${X(i).toFixed(1)} ${x > hi ? T + 1 : H - B - 1}l3 ${x > hi ? 5 : -5}h-6z"/>` : `<circle class="pt ${cls}" cx="${X(i).toFixed(1)}" cy="${Y(x).toFixed(1)}" r="${cls === 'bad' ? 3.6 : 1.8}"/>`; });
    const bad = rows.map((r, i) => r.pred_defect ? pts[i] : '').join('');
    const yt = tickVals(lo, hi), xt = [0, .25, .5, .75, 1].map(t => Math.round(t * (n - 1)));
    return `<svg class="chart" viewBox="0 0 ${Wd} ${H}" role="img" aria-label="${esc(v.label)} 추이">
      ${yt.map(t => `<line class="grid" x1="${L}" x2="${Wd - R}" y1="${Y(t)}" y2="${Y(t)}"/><text class="ax" x="${L - 8}" y="${Y(t) + 4}" text-anchor="end">${f(t, v.d)}</text>`).join('')}
      ${w ? `<rect class="band" x="${L}" width="${Wd - L - R}" y="${Y(w.stable_hi)}" height="${Math.max(1.5, Y(w.stable_lo) - Y(w.stable_hi))}"/>` : ''}
      ${w && !noCaution(w) ? [w.caution_lo, w.caution_hi].filter(c => c > lo && c < hi).map(c => `<line class="lim" x1="${L}" x2="${Wd - R}" y1="${Y(c)}" y2="${Y(c)}"/>`).join('') : ''}
      ${pts.filter((_, i) => !rows[i].pred_defect).join('')}
      ${segs.filter(q => q.length > 1).map(q => `<polyline class="ma" points="${q.join(' ')}"/>`).join('')}${bad}
      ${xt.map(i => `<text class="ax" x="${X(i)}" y="${H - 8}" text-anchor="${i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}">#${rows[i].idx}</text>`).join('')}
    </svg>`;
  }
  function lobeChart(rows) {
    const Wd = 380, H = 300, L = 48, R = 12, T = 12, B = 38, wc = winOf('전류(kA)'), wf = winOf('가압력(bar)');
    const [x0, x1] = axis(rows.map(r => r.current), wc), [y0, y1] = axis(rows.map(r => r.force), wf);
    const X = x => L + (Wd - L - R) * (Math.min(x1, Math.max(x0, x)) - x0) / (x1 - x0), Y = y => T + (H - T - B) * (1 - (Math.min(y1, Math.max(y0, y)) - y0) / (y1 - y0));
    const dots = rows.map(r => r.force > y1 ? '' : `<circle class="pt ${r.pred_defect ? 'bad' : ''}" cx="${X(r.current).toFixed(1)}" cy="${Y(r.force).toFixed(1)}" r="${r.pred_defect ? 3.6 : 1.8}"/>`);
    const order = rows.map((r, i) => [r.pred_defect, i]).sort((a, b) => a[0] - b[0]).map(([, i]) => dots[i]).join('');
    return `<svg class="chart lobe-c" viewBox="0 0 ${Wd} ${H}" role="img" aria-label="전류-가압력 용접 로브">
      ${tickVals(y0, y1).map(t => `<line class="grid" x1="${L}" x2="${Wd - R}" y1="${Y(t)}" y2="${Y(t)}"/><text class="ax" x="${L - 6}" y="${Y(t) + 4}" text-anchor="end">${f(t, 1)}</text>`).join('')}
      ${tickVals(x0, x1, 3).map((t, i) => `<text class="ax" x="${X(t)}" y="${H - 22}" text-anchor="${i === 0 ? 'start' : i === 3 ? 'end' : 'middle'}">${f(t, 2)}</text>`).join('')}
      ${wc && wf ? `<rect class="lobe" x="${X(wc.stable_lo)}" y="${Y(wf.stable_hi)}" width="${X(wc.stable_hi) - X(wc.stable_lo)}" height="${Y(wf.stable_lo) - Y(wf.stable_hi)}"/>` : ''}
      ${order}${rows.filter(r => r.force > y1).map(r => `<path class="pt out" d="M${X(r.current).toFixed(1)} ${T + 1}l3 5h-6z"/>`).join('')}
      <text class="ax" x="${(L + Wd - R) / 2}" y="${H - 5}" text-anchor="middle">전류 (kA)</text>
      <text class="ax" transform="translate(12 ${(T + H - B) / 2}) rotate(-90)" text-anchor="middle">가압력 (bar)</text>
    </svg>`;
  }
  const lotPpm = l => l.bad == null ? null : l.bad / l.welds * 1e6;

  function alerts() {
    const out = [], d = detail(), s = W().summary, done = doneSet();
    if (!d.id || M.toDate(d.date).getTime() < M.sinceDay(RD()).getTime()) return out;
    const pend = W().preds.filter(r => r.pred_defect && !done.has(ptId(r)));
    if (pend.length) out.push({ key: 'weld.pred', data: { lot: d.id, n: pend.length, expected: s.expected }, proc: 'weld', level: 'danger', title: `${d.id} 불량 판정 ${pend.length}타점 — 1순위 검사`,
      body: `예상 불량 ${f(s.expected, 1)}건 (95% ${f(s.ci[0], 1)}~${f(s.ci[1], 1)}) · 위험 상위 타점부터 검사하고 결과를 기록하세요`, by: '점용접', at: d.date + ' 23:00', go: 'weld_queue' });
    if (s.excursion) out.push({ key: 'weld.pressure', data: { lot: d.id, n: s.excursion }, proc: 'weld', level: 'warn', title: `${d.id} 가압 이상 구간 ${f(s.excursion, 0)}타점`,
      body: '가압력 3 bar 초과 구간 — 가압 실린더·전극 팁 점검', by: '점용접', at: d.date + ' 23:00', go: 'weld_home' });
    return out;
  }
  function summary() {
    const d = detail(), s = W().summary;
    return { kpi: `예상 ${f(s.expected / s.welds * 1e6, 0)} PPM`, sub: `${(d.date || '').slice(5)} · ${f(s.welds, 0)}타점 · 1순위 검사 ${s.pred_defect}`, st: s.pred_defect ? 'warn' : 'ok', go: 'weld_home' };
  }

  const V = {};
  V.weld_home = () => {
    const d = detail(), s = W().summary, rows = rowsByIdx();
    const wc = winOf('전류(kA)'), wf = winOf('가압력(bar)');
    const inWin = (r, kind) => [[wc, r.current], [wf, r.force]].every(([w, x]) => !w || (kind === 's' ? x >= w.stable_lo && x <= w.stable_hi : x >= w.caution_lo && x <= w.caution_hi));
    const confC = rows.filter(r => inWin(r, 'c')).length / Math.max(1, rows.length), confS = rows.filter(r => inWin(r, 's')).length / Math.max(1, rows.length);
    const v = MON.find(m => m.k === monK), xs = rows.map(v.get), w = winOf(v.name);
    const mean = xs.reduce((t, x) => t + x, 0) / (xs.length || 1), sd = Math.sqrt(xs.reduce((t, x) => t + (x - mean) ** 2, 0) / (xs.length || 1));
    const inS = w ? xs.filter(x => x >= w.stable_lo && x <= w.stable_hi).length / xs.length : null;
    const outC = w && !noCaution(w) ? xs.filter(x => x < w.caution_lo || x > w.caution_hi).length : null;
    const past = W().lots.filter(l => l.bad != null), pastPpm = past.reduce((t, l) => t + l.bad, 0) / Math.max(1, past.reduce((t, l) => t + l.welds, 0)) * 1e6;
    const days = W().lots.map(l => ({ x: l.date.slice(5), v: l.bad == null ? l.est / l.welds * 1e6 : lotPpm(l), cls: l.bad == null ? 'est' : '', t: `${l.id} · ${f(l.welds, 0)}타점 · ${l.bad == null ? `예상 ${f(l.est, 1)}건` : `불량 ${l.bad}건`}` }));
    return `
    <div class="kstrip">
      <div class="kpi click" data-go="weld_window"><div class="lab">관리 기준 적합률</div><div class="val">${f(confC * 100, 1)}<small>%</small></div><div class="foot">전류·가압력 기준 · 목표 구간 안 ${pct(confS, 0)}</div></div>
      <div class="kpi click" data-go="weld_queue"><div class="lab">예상 불량률 · ${esc((d.date || '').slice(5))}</div><div class="val">${f(s.expected / s.welds * 1e6, 0)}<small>PPM</small></div><div class="foot">${f(s.expected, 1)}건 / ${f(s.welds, 0)}타점 · 95% ${f(s.ci[0], 1)}~${f(s.ci[1], 1)}건</div></div>
      <div class="kpi click" data-go="weld_queue"><div class="lab">1순위 검사</div><div class="val" style="color:${s.pred_defect ? 'var(--danger-ink)' : 'inherit'}">${s.pred_defect}<small>타점</small></div><div class="foot">경보 ${f(s.tiers['경보'], 0)} · 가압 이상 ${f(s.excursion, 0)}타점</div></div>
      <div class="kpi click" data-go="weld_days"><div class="lab">이전 ${past.length}작업일 실측 불량률</div><div class="val">${f(pastPpm, 0)}<small>PPM</small></div><div class="foot">불량 ${past.reduce((t, l) => t + l.bad, 0)}건 / ${f(past.reduce((t, l) => t + l.welds, 0), 0)}타점</div></div>
    </div>
    <div class="grid g21" style="margin-top:16px">
      <div class="card"><h2>공정 모니터 <span class="h2sub">SP-01 · ${esc(d.id || '')} · ${f(rows.length, 0)}타점</span>
          <div style="margin-left:auto">${M.seg('mon', monK, MON.map(m => [m.k, m.label]))}</div></h2>
        ${runChart(rows, v)}
        <div class="mstrip"><div><span>평균</span><b>${f(mean, v.d)}<em>${esc(v.unit)}</em></b></div><div><span>표준편차</span><b>${f(sd, v.d)}</b></div>
          <div><span>목표 구간</span><b>${w ? (String(w.judgment).includes('하한 = 관측 최솟값') ? `≤ ${f(w.stable_hi, v.d)}` : `${f(w.stable_lo, v.d)} ~ ${f(w.stable_hi, v.d)}`) : '—'}</b></div>
          <div><span>목표 구간 안</span><b>${pct(inS, 1)}</b></div><div><span>관리 기준 밖</span><b style="color:${outC ? 'var(--warn-ink)' : 'inherit'}">${outC == null ? '—' : f(outC, 0) + '타점'}</b></div></div>
        ${M.legend([['l-band', '목표 운전 구간'], ['l-lim', '관리 기준'], ['l-ma', '이동평균(50타점)'], ['l-out', '관리 기준 밖'], ['l-bad', '불량 판정']].concat((() => { const [lo2, hi2] = axis(xs, w), nc = xs.filter(x => x > hi2 || x < lo2).length; return nc ? [['l-tri', `축 밖 ${f(nc, 0)}타점 (끝에 표시)`]] : []; })()))}</div>
      <div class="card"><h2>용접 로브 <span class="h2sub">전류 × 가압력</span></h2>${lobeChart(rows)}
        ${M.legend([['l-lobe', '목표 운전 구간'], ['l-bad', '불량 판정'], ...(() => { const top = wf ? axis(rows.map(x => x.force), wf)[1] : Infinity, nTop = rows.filter(r => r.force > top).length; return nTop ? [['l-tri', `${f(top, 1)} bar 초과 ${f(nTop, 0)}타점 (위쪽 끝)`]] : []; })()])}</div>
    </div>
    <div class="grid g21" style="margin-top:16px">
      <div class="card"><h2>작업일별 불량률 <span class="h2sub">실측 · 마지막 날은 모델 예상</span> <a class="more" href="#" data-go="weld_days">작업 실적 →</a></h2>${M.barChart(days, {})}
        ${M.legend([['l-barok', '실측 (검사 결과)'], ['l-barest', '예상 (검사 전)']])}</div>
      <div class="card"><h2>불량 유형 <span class="h2sub">학습 기간 ${W().types.reduce((t, x) => t + x.n, 0)}건</span></h2>
        <div class="hbars">${W().types.map(t => `<div class="hb"><span>${esc(t.name)}</span><div><i style="width:${t.n / Math.max(...W().types.map(x => x.n)) * 100}%"></i></div><b>${t.n}</b><em></em></div>`).join('')}</div>
        <p class="note">${W().types.map(t => `${esc(t.name)} — ${esc(t.top)}`).join('<br>')}</p></div>
    </div>`;
  };

  let qTier = 'all', qText = '';
  V.weld_queue = () => {
    const s = W().summary, done = doneSet(), d = detail();
    let rows = W().preds.slice().sort((a, b) => a.inspection_rank - b.inspection_rank);
    if (qTier === 'defect') rows = rows.filter(r => r.pred_defect); else if (qTier !== 'all') rows = rows.filter(r => r.risk_tier === qTier);
    if (qText) rows = rows.filter(r => String(r.idx).includes(qText));
    return `<div class="kstrip">
      <div class="kpi click" data-row="k:wd:exp"><div class="lab">예상 불량 · ${esc(d.id || '')}</div><div class="val">${f(s.expected, 1)}<small>건</small></div><div class="foot">95% 구간 ${f(s.ci[0], 1)}~${f(s.ci[1], 1)}</div></div>
      <div class="kpi click" data-row="k:wd:exp"><div class="lab">불량 판정 (1순위 검사)</div><div class="val" style="color:var(--danger-ink)">${s.pred_defect}<small>타점</small></div><div class="foot">예상 불량 수만큼 위험 상위 타점</div></div>
      <div class="kpi click" data-row="k:wd:tier"><div class="lab">경보 · 주의 (2순위)</div><div class="val">${s.tiers['경보']}<small>/ ${s.tiers['주의']}</small></div><div class="foot">위험 상위 10% / 70~90%</div></div>
      <div class="kpi click" data-row="k:wd:insp"><div class="lab">검사 기록</div><div class="val">${W().preds.filter(r => done.has(ptId(r))).length}<small>타점</small></div><div class="foot">기록이 쌓이면 타점 단위 학습</div></div></div>
    <div class="card" style="margin-top:16px"><div class="tools">${M.seg('wqt', qTier, [['all', '전체'], ['defect', '불량 판정'], ['경보', '경보'], ['주의', '주의'], ['안정', '안정']])}
      <input class="in" id="wqtext" placeholder="타점 번호" value="${esc(qText)}" style="width:160px"><span class="tsum">${rows.length}타점</span></div>
      ${M.table([['순위', 'inspection_rank', 'n'], ['타점', r => `<b>#${r.idx}</b>`], ['가압력', r => f(r.force, 2), 'n'], ['전류', r => f(r.current, 2), 'n'], ['전압', r => f(r.voltage, 3), 'n'],
        ['통전', r => f(r.weld_time, 0), 'n'], ['불량확률', r => pct(r.defect_prob, 2), 'n'], ['위험비', r => f(r.risk_ratio, 2) + '×', 'n'],
        ['등급', r => M.tag(r.risk_tier)], ['판정', r => r.pred_defect ? '<b style="color:var(--danger-ink)">불량</b>' : ''],
        ['검사', r => done.has(ptId(r)) ? '<span class="tag t-안정">기록됨</span>' : `<button class="btn sm" data-insp="weld|${esc(d.id)}|${r.idx}">기록</button>`]],
        rows.slice(0, 400), { hl: r => r.pred_defect, click: r => 'weldpt:' + r.idx })}
      <p class="note">위험 순 상위 400개까지. 행을 누르면 타점 상세가 열립니다.</p></div>`;
  };

  const g = v => { if (v == null || isNaN(v)) return '—'; const x = Math.abs(v); return f(v, x >= 1000 ? 0 : x >= 100 ? 1 : x >= 10 ? 2 : x >= 1 ? 3 : 4); };
  V.weld_window = () => {
    const ok = r => String(r.judgment || '').startsWith('제안'), lowOpen = r => String(r.judgment || '').includes('하한 = 관측 최솟값'), allC = r => String(r.judgment || '').includes('주의 범위 = 관측 전체');
    return `<div class="card"><h2>안정 공정조건 <span class="h2sub">SP-01 · CH-001 보강판 용접</span></h2>
      ${M.table([['변수', r => `<b>${esc(r.variable)}</b>`], ['평소 운전점', r => g(r.median), 'n'],
        ['목표 운전 구간', r => !ok(r) ? '—' : lowOpen(r) ? `${g(r.stable_hi)} 이하` : `${g(r.stable_lo)} ~ ${g(r.stable_hi)}`],
        ['관리 기준', r => !ok(r) ? '—' : allC(r) ? '<span class="muted">경계 없음</span>' : lowOpen(r) ? `${g(r.caution_hi)} 이하` : `${g(r.caution_lo)} ~ ${g(r.caution_hi)}`],
        ['목표 안 비율', r => ok(r) ? pct(r.stable_share, 0) : '—', 'n'], ['밖/안 위험비', r => r.ratio == null ? '—' : f(r.ratio, 2) + '×', 'n'],
        ['판단', r => ok(r) ? '<span class="tag j-ok">제안</span>' : '<span class="chip c-off">영향 작음</span>']], W().windows, { wrap: false })}
      <p class="note">목표 운전 구간 = 설정값을 맞출 기준, 관리 기준 = 벗어나면 점검하는 기준. 밖/안 위험비 = 구간 밖 타점의 불량 위험이 안쪽의 몇 배인지.</p></div>`;
  };

  V.weld_days = () => {
    const rows = W().lots.slice().reverse();
    return `<div class="card">${M.table([['작업', r => `<b>${esc(r.id)}</b>`], ['작업일', 'date'], ['타점', r => f(r.welds, 0), 'n'],
      ['불량 (실측)', r => r.bad == null ? '<span class="muted">검사 전</span>' : r.bad, 'n'], ['예상 불량', r => f(r.est, 1), 'n'],
      ['불량률', r => r.bad == null ? `<span class="muted">예상 ${f(r.est / r.welds * 1e6, 0)} PPM</span>` : `${f(lotPpm(r), 0)} PPM`, 'n'],
      ['관리 기준 밖 비율', r => pct(r.out_rate, 0), 'n'], ['가압 이상', r => r.excursion ? '<span class="tag j-warn">있음</span>' : '<span class="muted">없음</span>'],
      ['다음 공정', r => { const hs = M.lineage(r.id).down.filter(x => x.proc === 'heat'); return hs.length ? hs.slice(0, 2).map(h => `<a href="#" data-row="heat:${esc(h.id)}">${esc(h.id)}</a>`).join(' · ') + (hs.length > 2 ? ` 외 ${hs.length - 2}` : '') : '—'; }],
      ['이력', r => `<a href="#" data-trace="${esc(r.id)}">보기</a>`]], rows, { wrap: false, click: r => r.has_detail ? 'weld_queue' : 'trace:' + r.id })}
      <p class="note">타점별 공정값은 마지막 작업일만 있습니다 (그날은 검사 전이라 모델 예상으로 우선순위를 냄). 이전 작업일은 작업일 단위 불량 수만 있습니다.</p></div>`;
  };

  function ptDrawer(idx) {
    const r = W().preds.find(p => String(p.idx) === String(idx)); if (!r) return;
    const d = detail(), rec = M.inspections().find(i => i.proc === 'weld' && i.lot_id === d.id && String(i.target) === String(r.idx));
    const VARS = [['force', '가압력(bar)', 2], ['current', '전류(kA)', 2], ['voltage', '전압(V)', 3], ['weld_time', '통전시간(ms)', 0]];
    const gauges = VARS.map(([k, name, dd]) => {
      const w = W().windows.find(x => x.variable === name) || {}, xs = W().preds.map(p => p[k]);
      const lo = Math.min(...xs), hi = Math.max(...xs), sc = v => ((v - lo) / ((hi - lo) || 1) * 100);
      const has = w.stable_lo != null && String(w.judgment || '').startsWith('제안');
      const inS = has && r[k] >= w.stable_lo && r[k] <= w.stable_hi, inC = w.caution_lo != null && r[k] >= w.caution_lo && r[k] <= w.caution_hi;
      const st = !has ? '' : inS ? '<span class="tag j-ok">목표 안</span>' : inC ? '<span class="tag j-warn">관리 기준 안</span>' : '<span class="tag j-danger">관리 기준 밖</span>';
      return `<div style="margin-bottom:14px"><div style="display:flex;justify-content:space-between;font-size:13px"><span>${esc(name)}</span><span><b>${f(r[k], dd)}</b> ${st}</span></div>
        <div class="gauge">${has ? `<div class="band" style="left:${sc(w.stable_lo)}%;width:${Math.max(1, sc(w.stable_hi) - sc(w.stable_lo))}%"></div>` : ''}<div class="pin" style="left:calc(${sc(r[k])}% - 1px)"></div></div>
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--faint);margin-top:3px"><span>${f(lo, dd)}</span><span>${f(hi, dd)}</span></div></div>`;
    }).join('');
    M.drawer(`점용접 · SP-01 · ${d.date}`, `타점 #${r.idx}`, `
      <div class="blk"><h4>판정</h4><dl class="stat"><dt>검사 순위</dt><dd>${r.inspection_rank} / ${W().preds.length}</dd>
        <dt>판정</dt><dd>${r.pred_defect ? '<span style="color:var(--danger-ink)">불량 (1순위 검사)</span>' : M.tag(r.risk_tier)}</dd>
        <dt>불량 확률</dt><dd>${pct(r.defect_prob, 2)}</dd><dt>평균 대비 위험비</dt><dd>${f(r.risk_ratio, 2)}×</dd><dt>관리범위 이탈</dt><dd>${r.out_of_control ? '예' : '아니오'}</dd></dl></div>
      <div class="blk"><h4>이력</h4><dl class="stat"><dt>작업 LOT</dt><dd><a href="#" data-trace="${esc(d.id)}">${esc(d.id)}</a></dd></dl></div>
      <div class="blk"><h4>공정값 — 초록 = 목표 운전 구간 (그날 관측 범위 위)</h4>${gauges}</div>
      <div class="blk"><h4>검사</h4>${rec ? `<dl class="stat"><dt>결과</dt><dd>${esc(rec.result)} ${esc(rec.defect_code || '')}</dd><dt>기록</dt><dd>${esc(rec.by)} · ${esc(rec.at)}</dd></dl>`
        : `<button class="btn pri" data-insp="weld|${esc(d.id)}|${r.idx}">검사 결과 기록</button>`}</div>`);
  }

  // ── 요약 띠 상세
  function kd(k) {
    const s = W().summary, d = detail(), P = W().preds.slice().sort((a, b) => a.inspection_rank - b.inspection_rank);
    const cols = [['순위', 'inspection_rank', 'n'], ['타점', r => `<b>#${r.idx}</b>`], ['불량확률', r => pct(r.defect_prob, 2), 'n'], ['위험비', r => f(r.risk_ratio, 2) + '×', 'n'], ['등급', r => M.tag(r.risk_tier)]];
    if (k === 'exp') return M.kdrawer(`점용접 · ${d.id || ''}`, `예상 불량 ${f(s.expected, 1)}건 — 1순위 검사 ${s.pred_defect}타점`, [
      { html: M.dl([['타점 수', f(s.welds, 0)], ['예상 불량 (확률 합)', f(s.expected, 1) + '건'], ['95% 구간', `${f(s.ci[0], 1)} ~ ${f(s.ci[1], 1)}`], ['1순위 검사', s.pred_defect + '타점']]) },
      { h: '위험 상위 타점', html: M.table(cols, P.slice(0, 12), { click: r => 'weldpt:' + r.idx, wrap: false }) },
      { html: '<p class="note">타점별 불량확률을 모두 더한 값이 예상 불량 수입니다. 그 수만큼 위험 상위 타점을 1순위 검사로 지정합니다. 행을 누르면 타점 상세가 열립니다.</p>' }]);
    if (k === 'tier') { const t = s.tiers || {};
      return M.kdrawer(`점용접 · ${d.id || ''}`, '위험 등급별 타점', [
        { html: M.table([['등급', r => M.tag(r[0])], ['타점', r => f(r[1], 0), 'n'], ['기준', r => r[2]]], [['경보', t['경보'] || 0, '위험 상위 10%'], ['주의', t['주의'] || 0, '위험 상위 70~90%'], ['안정', t['안정'] || 0, '나머지']], { wrap: false }) },
        { h: '경보 타점', html: M.table(cols, P.filter(r => r.risk_tier === '경보').slice(0, 15), { click: r => 'weldpt:' + r.idx, wrap: false, empty: '경보 타점이 없습니다' }) }]); }
    if (k === 'insp') { const ins = M.inspections().filter(i => i.proc === 'weld');
      return M.kdrawer('점용접 · 검사 기록', `기록 ${ins.length}건`, [
        { html: M.table([['시각', 'at'], ['LOT', 'lot_id'], ['타점', i => i.target ? '#' + esc(i.target) : '—'], ['결과', i => i.result === '불량' ? '<b style="color:var(--danger-ink)">불량</b>' : esc(i.result)], ['메모', 'note']], ins, { wrap: false, empty: '아직 검사 기록이 없습니다 — 검사 우선순위 표의 「기록」 단추로 남깁니다' }) },
        { html: '<p class="note">현장 검사 결과가 쌓이면 타점 단위 정답이 생겨 예측 모델이 다시 학습됩니다.</p>' }]); }
  }

  M.mod({
    id: 'weld', proc: 'weld', group: '점용접', order: 20,
    menu: [['weld_home', '점용접 현황', 'spark'], ['weld_queue', '검사 우선순위', 'list'], ['weld_window', '안정 공정조건', 'range'], ['weld_days', '작업 실적', 'book']],
    views: V, alerts, summary, drawers: { 'k:wd:': kd, 'weldpt:': ptDrawer, 'weld:': id => { const l = W().lots.find(x => x.id === id); if (l && l.has_detail) M.go('weld_queue'); else M.go('weld_days'); } },
    bind(root) {
      root.querySelectorAll('[data-mon]').forEach(b => b.onclick = () => { monK = b.dataset.mon; M.render(); });
      root.querySelectorAll('[data-wqt]').forEach(b => b.onclick = () => { qTier = b.dataset.wqt; M.render(); });
      const q = root.querySelector('#wqtext');
      if (q) q.oninput = () => { qText = q.value.trim(); const pos = q.selectionStart; M.render(); const n = M.$('#wqtext'); n.focus(); n.setSelectionRange(pos, pos); };
    },
  });
})();
