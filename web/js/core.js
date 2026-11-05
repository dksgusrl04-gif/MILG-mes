/* MILG MES 공용 화면 틀
 * ───────────────────────────────────────────────────────────────
 * 공정 모듈은 MLNG.mod({...}) 로 자기를 등록만 한다. 메뉴·알람·검사 기록·테마·상세 서랍은 여기서 같이 쓴다.
 *
 *   MLNG.mod({
 *     id: 'heat', proc: 'heat', group: '열처리', order: 30,
 *     menu: [['heat_home', '열처리 현황', 'flame'], ...],
 *     views: { heat_home: () => html, ... },
 *     alerts: S => [{ level, title, body, proc, at, go }],   // 현장 알람
 *     summary: S => ({ kpi, note, st }),                     // 공장 현황의 공정 카드
 *     drawers: { 'heat:': id => ... },                       // data-row="heat:HT-1" → 상세 서랍
 *   })
 */
(function () {
  'use strict';
  const CFG = window.MLNG_CONFIG || {};
  const D = window.MLNGData;
  const M = window.MLNG = { S: null, cur: CFG.START_VIEW || 'overview', mods: [], CFG, D };
  const $ = M.$ = s => document.querySelector(s);

  // ── 형식
  M.esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const NF = {};   // 숫자 형식기는 자릿수별로 한 번만 만든다 (toLocaleString 은 호출마다 새로 만들어 느림)
  M.f = (v, d = 1) => (v == null || v === '' || isNaN(v)) ? '—' : (NF[d] || (NF[d] = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: d, maximumFractionDigits: d }))).format(Number(v));
  M.pct = (v, d = 1) => (v == null || isNaN(v)) ? '—' : (Number(v) * 100).toFixed(d) + '%';
  M.median = a => { const b = a.filter(x => x != null && !isNaN(x)).sort((x, y) => x - y); if (!b.length) return null; const m = b.length >> 1; return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2; };
  M.toDate = s => new Date(String(s || '').replace(' ', 'T'));
  // 데모는 «데이터의 마지막 시각» 을 지금으로 본다 (스냅샷). 실시간은 실제 지금.
  M.now = () => (D.LIVE || !M.S || !M.S.asof) ? new Date() : M.toDate(M.S.asof);
  M.daysAgo = n => new Date(M.now().getTime() - n * 86400000);
  M.sinceDay = n => { const t = M.daysAgo(n); t.setHours(0, 0, 0, 0); return t; };   // «최근 N일» = N일 전 0시부터 (모든 화면 공통)
  const { esc, f } = M;

  // ── 공정 표시
  M.PROC = { press: '프레스', weld: '용접', heat: '열처리', mach: 'MCT 가공', insp: '검사', data: '데이터', mct: 'MCT', plan: '계획', buy: '구매', net: '연결', deal: '고객 발주', agent: '에이전트', gate: '출하 승인', case: '품질 이슈', flow: '수주' };
  M.procTag = p => `<span class="proc p-${esc(p)}">${esc(M.PROC[p] || p)}</span>`;
  M.tag = (t, cls) => `<span class="tag ${cls || 't-' + esc(t)}">${esc(t)}</span>`;
  M.GRADE = { A: ['A', '즉시 조치', 'danger'], B: ['B', '당일 조치', 'warn'], C: ['C', '관찰', 'info'] };
  M.gradeTag = (g, full) => { const x = M.GRADE[g] || M.GRADE.C; return `<span class="grd g-${x[0]}" title="${x[0]}등급 — ${x[1]}"><b>${x[0]}</b>${full ? `<em>${x[1]}</em>` : ''}</span>`; };
  M.dot = (st, t) => `<span class="dot ${st}"></span>${esc(t)}`;

  const IC = {
    home: '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>', bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    trace: '<circle cx="5" cy="6" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="12" r="2"/><path d="M7 6h4a3 3 0 0 1 3 3v0a3 3 0 0 0 3 3M7 18h4a3 3 0 0 0 3-3v0a3 3 0 0 1 3-3"/>',
    book: '<path d="M4 4h11a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3z"/><path d="M4 17a3 3 0 0 1 3-3h11"/>', check: '<path d="M4 12l5 5L20 6"/>',
    press: '<path d="M5 3h14v4H5zM12 7v6M7 13h10l-1 4H8zM4 21h16"/>',
    wave: '<path d="M2 12c3-6 5-6 8 0s5 6 8 0 3-4 4-2"/>',
    spark: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    flame: '<path d="M12 3c1 4 5 5 5 10a5 5 0 0 1-10 0c0-3 2-4 2-7 2 1 3 3 3 5 1-1 1-3 0-8z"/>', list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    range: '<path d="M3 12h18M7 8v8M17 8v8"/>', gauge: '<path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 18l4-6"/>', out: '<path d="M14 3h7v7M21 3l-9 9M10 5H5v14h14v-5"/>',
    cart: '<path d="M3 4h2l2.4 11h11l2-8H6.2"/><circle cx="9" cy="19" r="1.5"/><circle cx="17" cy="19" r="1.5"/>',
    sign: '<path d="M4 20h16"/><path d="M14.5 4.5l5 5L9 20H4v-5z"/>',
    chat: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    chain: '<rect x="3" y="4" width="7" height="6" rx="1"/><rect x="14" y="14" width="7" height="6" rx="1"/><path d="M6.5 10v4h11v0"/><path d="M17.5 14v-2"/>',
    shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    passport: '<rect x="5" y="3" width="14" height="18" rx="2"/><circle cx="12" cy="10" r="3"/><path d="M9 17h6"/>',
    badge: '<circle cx="12" cy="9" r="5"/><path d="M9 13.5L8 21l4-2 4 2-1-7.5"/>',
    leaf: '<path d="M5 19c8 0 14-6 14-14-8 0-14 5-14 13z"/><path d="M5 19c3-5 6-8 10-10"/>',
    deal: '<path d="M3 12l4-4 5 3 5-3 4 4"/><path d="M7 8v8l5 3 5-3V8"/>',
    agent: '<rect x="5" y="7" width="14" height="11" rx="3"/><path d="M12 3v4M9 12h.01M15 12h.01M9 15h6"/>',
    sync: '<path d="M20 11a8 8 0 0 0-14-5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14 5l2-2"/><path d="M20 20v-4h-4"/>',
    fed: '<circle cx="12" cy="12" r="3"/><circle cx="4" cy="6" r="2"/><circle cx="20" cy="6" r="2"/><circle cx="4" cy="18" r="2"/><circle cx="20" cy="18" r="2"/><path d="M6 7l4 3M18 7l-4 3M6 17l4-3M18 17l-4-3"/>',
    case: '<path d="M4 7h16v12H4z"/><path d="M9 7V5h6v2"/><path d="M4 12h16"/><path d="M11 12v2h2v-2"/>',
    won: '<path d="M4 6l3 12 5-10 5 10 3-12"/><path d="M3 10h18M3 14h18"/>',
    gate: '<path d="M3 21V8l9-5 9 5v13"/><path d="M8 21v-8h8v8"/><path d="M8 17h8"/>',
    whatif: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    replay: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
    shift: '<circle cx="8" cy="7" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M2 20c0-4 3-6 6-6s6 2 6 6"/><path d="M14 15c2.5-.8 7 0 7 4"/>',
  };
  M.ic = k => `<svg class="ic" viewBox="0 0 24 24">${IC[k] || ''}</svg>`;

  // ── 모듈 등록
  M.mod = m => { M.mods.push(m); M.mods.sort((a, b) => (a.order || 0) - (b.order || 0)); };
  M.views = () => Object.assign({}, ...M.mods.map(m => m.views || {}));
  // LOT 이력: mlng_trace 를 따라 위(조상)·아래(자손)로
  // 데이터(S)가 같으면 결과도 같다 → S 별로 기억 (시점 조회의 잘린 S 는 따로)
  const LIN = new WeakMap();
  M.lineage = id => { let m = LIN.get(M.S); if (!m) LIN.set(M.S, m = new Map()); if (m.has(id)) return m.get(id); const r = lineage0(id); m.set(id, r); return r; };
  // 데이터 버전에 묶인 계산 캐시 — 같은 S · 같은 원장 길이면 다시 계산하지 않는다
  const MEMO = new WeakMap();
  M.memo = (name, key, fn) => { let m = MEMO.get(M.S); if (!m) MEMO.set(M.S, m = new Map()); const T = window.MLNGTrust, k = name + '|' + key + '|' + (T ? T.ledger.length : 0); if (m.has(k)) return m.get(k); const v = fn(); m.set(k, v); return v; };
  const lineage0 = id => {
    const T = M.S.trace || [], up = [], down = [], seen = new Set([id]);
    const walk = (cur, dir, acc) => T.filter(t => (dir === 'up' ? t.child_lot : t.parent_lot) === cur).forEach(t => {
      const nx = dir === 'up' ? t.parent_lot : t.child_lot; if (seen.has(nx)) return; seen.add(nx); acc.push(nx); walk(nx, dir, acc); });
    walk(id, 'up', up); walk(id, 'down', down);
    const L = id2 => (M.S.lots || []).find(l => l.id === id2);
    return { up: up.map(L).filter(Boolean), down: down.map(L).filter(Boolean) };
  };
  M.inspections = () => D.LIVE ? (M.S.inspections || []) : D.demoInspections();

  // 알람 = 저장된 알람(mlng_alerts) + 각 모듈이 데이터에서 찾은 현장 알람
  let alertCache = null;   // 한 번 그리는 동안 여러 곳에서 부르므로 같은 작업 안에서는 한 번만 계산
  let computing = false;   // 알람 계산 중에 다른 모듈이 다시 알람을 부르면(이슈·출하 승인) 빈 목록 — 무한 반복 방지
  M.alerts = () => {
    if (alertCache) return alertCache.slice();
    if (computing) return [];
    computing = true; try { alertCache = computeAlerts(); } finally { computing = false; } queueMicrotask(() => { alertCache = null; });
    return alertCache.slice();
  };
  // 3.0 과거 시점 조회: 데이터를 그 시점까지로 자른 S 로 알람을 다시 계산 (화면의 M.S 는 잠시 바꿨다가 되돌린다)
  M.withS = (S2, fn) => { const keep = M.S; M.S = S2; try { return fn(); } finally { M.S = keep; } };
  M.alertsFor = S2 => M.withS(S2, () => computeAlerts());
  const computeAlerts = () => {
    const rank = { danger: 0, warn: 1, info: 2 };
    const stored = (M.S.alerts || []).map(a => Object.assign({ proc: a.proc || 'data' }, a));
    const found = [].concat(...M.mods.map(m => { try { return m.alerts ? m.alerts(M.S) : []; } catch (e) { console.warn(e); return []; } }));
    // 등급 A/B/C 를 붙이고, 화면 신호등(level)은 등급을 따른다 — A 빨강 · B 노랑 · C 회색(관찰)
    const GR = CFG.ALARM_GRADE || {}, LV = { A: 'danger', B: 'warn', C: 'info' };
    const all = stored.concat(found).map(a => { const g = GR[a.key] || (a.level === 'danger' ? 'A' : a.level === 'warn' ? 'B' : 'C'); return Object.assign(a, { grade: g, level: LV[g], level0: a.level0 || a.level }); });
    return all.sort((a, b) => (rank[a.level] ?? 3) - (rank[b.level] ?? 3) || String(b.at || '').localeCompare(String(a.at || '')));
  };

  // ── 표
  M.table = (head, rows, opt = {}) => {
    if (!rows.length) return `<div class="empty">${opt.empty || '표시할 자료가 없습니다'}</div>`;
    return `<div class="${opt.wrap === false ? 'tbx' : 'tbwrap'}"><table class="tb"><thead><tr>${head.map(h => `<th class="${h[2] || ''}">${h[0]}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(r => `<tr class="${opt.hl && opt.hl(r) ? 'hl' : ''} ${opt.click ? 'click' : ''}" ${opt.click ? `data-row="${esc(opt.click(r))}"` : ''}>${head.map(h => `<td class="${h[2] || ''}">${typeof h[1] === 'function' ? h[1](r) : esc(r[h[1]])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  };
  M.legend = items => `<div class="legend">${items.map(([c, t]) => `<span><i class="${c}"></i>${t}</span>`).join('')}</div>`;
  M.seg = (attr, cur, opts) => `<div class="seg sm">${opts.map(([k, t]) => `<button data-${attr}="${esc(k)}" class="${String(k) === String(cur) ? 'on' : ''}">${esc(t)}</button>`).join('')}</div>`;

  // ── 그림: 시계열 선 (여러 줄 + 허용 띠)
  //   series: [{ v:[..], cls:'s1', label }], band:{lo,hi}, opt:{ h, d, xlab:[[i,'text']], yfix:[lo,hi] }
  M.lineChart = (series, band, opt = {}) => {
    const W = opt.w || 640, H = opt.h || 200, L = 52, R = 10, T = 10, B = opt.xlab ? 24 : 10;
    const all = [].concat(...series.map(s => s.v)).filter(v => v != null && !isNaN(v));
    if (!all.length) return '<div class="empty">값이 없습니다</div>';
    let lo = Math.min(...all), hi = Math.max(...all);
    if (band) { lo = Math.min(lo, band.lo); hi = Math.max(hi, band.hi); }
    (opt.refs || []).forEach(r => { lo = Math.min(lo, r.y); hi = Math.max(hi, r.y); });
    if (opt.yfix) { lo = opt.yfix[0]; hi = opt.yfix[1]; }
    const pad = (hi - lo) * 0.08 || 1; const nonneg = lo >= 0; lo -= pad; hi += pad; if (nonneg && lo < 0) lo = 0;
    const n = Math.max(...series.map(s => s.v.length));
    const X = i => L + (W - L - R) * i / Math.max(1, n - 1), Y = y => T + (H - T - B) * (1 - (Math.min(hi, Math.max(lo, y)) - lo) / (hi - lo));
    const ticks = [0, 1, 2, 3].map(i => lo + (hi - lo) * i / 3);
    const path = v => { let d = '', pen = false; v.forEach((y, i) => { if (y == null || isNaN(y)) { pen = false; return; } d += (pen ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(y).toFixed(1); pen = true; }); return d; };
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">
      ${ticks.map(t => `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(t)}" y2="${Y(t)}"/><text class="ax" x="${L - 7}" y="${Y(t) + 4}" text-anchor="end">${f(t, opt.d ?? 1)}</text>`).join('')}
      ${band ? `<rect class="band" x="${L}" width="${W - L - R}" y="${Y(band.hi)}" height="${Math.max(1, Y(band.lo) - Y(band.hi))}"/>
        <line class="lim" x1="${L}" x2="${W - R}" y1="${Y(band.hi)}" y2="${Y(band.hi)}"/><line class="lim" x1="${L}" x2="${W - R}" y1="${Y(band.lo)}" y2="${Y(band.lo)}"/>` : ''}
      ${series.map(s => `<path class="ln ${s.cls || ''}" d="${path(s.v)}"/>`).join('')}
      ${(opt.refs || []).map(r => `<line class="lim ${r.cls || ''}" x1="${L}" x2="${W - R}" y1="${Y(r.y)}" y2="${Y(r.y)}"/>${r.label ? `<text class="ax ref" x="${W - R}" y="${Y(r.y) - 5}" text-anchor="end">${esc(r.label)}</text>` : ''}`).join('')}
      ${(opt.dots || []).map(p => `<circle class="pt ${p.cls || ''}" cx="${X(p.i).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="${p.r || 2.4}"><title>${esc(p.t || '')}</title></circle>`).join('')}
      ${(opt.xlab || []).map(([i, t], k, a) => `<text class="ax" x="${X(i)}" y="${H - 7}" text-anchor="${k === 0 ? 'start' : k === a.length - 1 ? 'end' : 'middle'}">${esc(t)}</text>`).join('')}
    </svg>`;
  };
  // ── 그림: 막대 (값 + 기준선)
  M.barChart = (rows, opt = {}) => {
    const W = opt.w || 640, H = opt.h || 190, L = 52, R = 10, T = 12, B = 24;
    const vals = rows.map(r => r.v || 0), hi = Math.max(opt.ref || 0, ...vals) * 1.12 || 1;
    const bw = (W - L - R) / Math.max(1, rows.length), Y = y => T + (H - T - B) * (1 - y / hi);
    const ticks = [0, 1, 2, 3].map(i => hi * i / 3), every = Math.ceil(rows.length / 8);
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">
      ${ticks.map(t => `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(t)}" y2="${Y(t)}"/><text class="ax" x="${L - 7}" y="${Y(t) + 4}" text-anchor="end">${f(t, 0)}</text>`).join('')}
      ${rows.map((r, i) => `<rect class="bar ${r.cls || ''}" x="${(L + i * bw + bw * 0.18).toFixed(1)}" width="${(bw * 0.64).toFixed(1)}" y="${Y(r.v || 0).toFixed(1)}" height="${Math.max(0, Y(0) - Y(r.v || 0)).toFixed(1)}"><title>${esc(r.t || '')}</title></rect>`).join('')}
      ${opt.ref ? `<line class="lim" x1="${L}" x2="${W - R}" y1="${Y(opt.ref)}" y2="${Y(opt.ref)}"/><text class="ax ref" x="${W - R}" y="${Y(opt.ref) - 5}" text-anchor="end">${esc(opt.refLabel || '')}</text>` : ''}
      ${rows.map((r, i) => i % every ? '' : `<text class="ax" x="${(L + i * bw + bw / 2).toFixed(1)}" y="${H - 7}" text-anchor="middle">${esc(r.x)}</text>`).join('')}
    </svg>`;
  };

  // ── 메뉴: 주문 → 자재 → 생산 → 품질 → 출하 흐름대로 7개. 화면은 메뉴 안의 탭, 세부는 작은 탭.
  //   모듈이 만든 화면 중 여기 없는 것은 «더보기» 아래로 자동으로 들어간다 (아무 화면도 잃지 않는다).
  M.NAV = [
    { id: 'home', t: '대시보드', i: 'home', tabs: [['overview', '종합 현황'], ['alerts', '알람 이력'], ['shift', '교대 인수인계']] },
    { id: 'order', t: '수주', i: 'deal', tabs: [['orders', '수주 현황'], ['deal_home', '고객 발주 · 납품']] },
    { id: 'mat', t: '자재 · 구매', i: 'cart', tabs: [['buy_home', '구매 요청'], ['buy_orders', '발주 승인']] },
    { id: 'plan', t: '생산 계획', i: 'range', tabs: [['line', '라인 밸런스'], ['plan_home', '생산 계획'], ['plan_wo', '작업지시'], ['whatif', '계획 시뮬레이션'], ['sync', '수급 계획']] },
    { id: 'pop', t: '실적 입력 (POP)', i: 'sign', tabs: [['pop', '실적 입력']] },
    { id: 'proc', t: '공정 실적', i: 'trace', tabs: [
      ['procmap', '전체 공정'],
      ['daily', '생산 일보'],
      ['press_home', '프레스', [['press_home', '현황'], ['press_lots', '작업 실적'], ['press_recipe', '공정 조건'], ['press_equip', '설비']]],
      ['weld_home', '점용접', [['weld_home', '현황'], ['weld_days', '작업 실적'], ['weld_window', '공정 조건'], ['weld_queue', '검사 우선순위']]],
      ['heat_home', '열처리', [['heat_home', '현황'], ['heat_lots', '작업 실적'], ['heat_recipe', '공정 조건'], ['heat_equip', '설비']]],
      ['mach_home', 'MCT 가공', [['mach_home', '현황'], ['mach_tools', '공구 수명'], ['mach_cond', '가공 조건']]]] },
    { id: 'equip', t: '설비 · OEE', i: 'gauge', tabs: [['oee', 'OEE 현황'], ['downtime', '비가동 관리']] },
    { id: 'qual', t: '품질', i: 'check', tabs: [['cases', '품질 이슈'], ['spc', 'SPC 관리도'], ['capa', '공정능력'], ['strat', '불량 층별 분석'], ['insp_home', '검사', [['insp_home', '현황'], ['insp_parts', '부품 이력'], ['insp_signal', '가공 신호']]], ['trace', 'LOT 추적'], ['pass_home', '품질 성적서']] },
    { id: 'tx', t: '불량 원인 분석', i: 'spark', tabs: [['tx_report', '원인 분석 보고서'], ['tx_lots', '불량 LOT 추적']] },
    { id: 'ship', t: '출하', i: 'gate', tabs: [['gate', '출하 승인']] },
  ];
  M.NAV_MORE = [
    ['분석', [['cost', '품질 비용(COPQ)'], ['replay', '과거 시점 조회'], ['models', 'AI 모델 상태'], ['mval', '예측 모델 검증'], ['xai', '공정 통합 AI'], ['prove', 'AI 판정 이력']]],
    ['AI', [['ask', 'AI 질의응답'], ['agent', 'AI 자동 조치'], ['fed', '공동 학습']]],
    ['데이터 신뢰', [['net_contracts', '데이터 공유 계약'], ['net_ledger', '신뢰 원장'], ['net_security', '보안 구조'], ['pass_cred', '공정 능력 성적서'], ['pass_pcf', '탄소 배출량(PCF)']]],
    ['시스템', [['assume', '가정값 관리'], ['masters', '기준 정보'], ['data', '데이터 상태']]],
  ];
  const keysOf = tab => tab[2] ? tab[2].map(x => x[0]) : [tab[0]];
  // 화면 키 → { item, tab, sub, title } (메뉴 강조 · 제목 · 탭 그리기에 같이 쓴다)
  M.where = k => {
    for (const it of M.NAV) for (const tb of it.tabs) if (keysOf(tb).includes(k)) return { item: it, tab: tb, sub: tb[2] ? tb[2].find(x => x[0] === k) : null };
    for (const [g, xs] of moreGroups()) for (const x of xs) if (x[0] === k) return { more: g, tab: x };
    return null;
  };
  function moreGroups() {
    const known = new Set([].concat(...M.NAV.map(it => [].concat(...it.tabs.map(keysOf))), ...M.NAV_MORE.map(([, xs]) => xs.map(x => x[0]))));
    const vs = M.views(), rest = [];
    M.mods.forEach(m => (m.menu || []).forEach(([k, t]) => { if (!known.has(k) && vs[k]) rest.push([k, t]); }));
    return M.NAV_MORE.map(([g, xs]) => [g, xs.filter(x => vs[x[0]])]).concat(rest.length ? [['기타', rest]] : []).filter(([, xs]) => xs.length);
  }
  let moreOpen = (() => { try { return localStorage.getItem('mlng_more') === 'on'; } catch (e) { return false; } })();
  function pill() {   // 대시보드 옆 긴급 개수만 갱신
    const b = $('#nav button[data-k="overview"]'); if (!b) return;
    const n = M.alerts().filter(a => a.level === 'danger').length; let el = b.querySelector('.pill-n');
    if (!n) { if (el) el.remove(); return; }
    if (!el) { el = document.createElement('span'); el.className = 'pill-n'; b.appendChild(el); }
    if (el.textContent !== String(n)) el.textContent = n;
  }
  function rail() {
    const vs = M.views(), w = M.where(M.cur) || {};
    $('#plant').textContent = `${M.S.factory.name}${M.S.factory.line ? ' · ' + M.S.factory.line : ''}`;
    const main = M.NAV.filter(it => it.tabs.some(tb => keysOf(tb).some(k => vs[k]))).map(it => { const first = it.tabs.map(tb => tb[0]).find(k => vs[k]);
      return `<button data-k="${first}" data-item="${it.id}" class="${w.item === it ? 'on' : ''}">${M.ic(it.i)}<span>${esc(it.t)}</span></button>`; }).join('');
    const more = moreGroups().map(([g, xs]) => `<div class="ngrp">${esc(g)}</div>` + xs.map(([k, t]) => `<button data-k="${k}" class="sm ${k === M.cur ? 'on' : ''}"><span>${esc(t)}</span></button>`).join('')).join('');
    $('#nav').innerHTML = `<div class="nmain">${main}</div>
      <button class="nmore ${moreOpen || w.more ? 'open' : ''}" id="nmore"><span>더보기</span><small>분석 · AI · 데이터 신뢰 · 시스템</small><i>›</i></button>
      <div class="nsub ${moreOpen || w.more ? 'open' : ''}">${more}</div>`
      + Object.entries(CFG.LINKS || {}).filter(([, u]) => u).map(([p, u]) => `<a class="ext" href="${esc(u)}" target="_blank" rel="noopener">${M.ic('out')}<span>${esc(M.PROC[p] || p)} 열기</span></a>`).join('');
    $('#nav').querySelectorAll('button[data-k]').forEach(b => b.onclick = () => { document.body.classList.remove('navon'); M.go(b.dataset.k); });
    $('#nmore').onclick = () => { moreOpen = !$('#nmore').classList.contains('open'); try { localStorage.setItem('mlng_more', moreOpen ? 'on' : 'off'); } catch (e) { /* 무시 */ }
      $('#nmore').classList.toggle('open', moreOpen); $('.nsub').classList.toggle('open', moreOpen); };
    pill();
    const u = D.user();
    $('#who').textContent = u ? `${u.name} · ${u.role === 'manager' ? '관리자' : '작업자'}` : '';
  }
  M.rail = rail;
  // 메뉴는 다시 그리지 않고 선택 표시만 옮긴다
  function markNav() { const w = M.where(M.cur) || {}; $('#nav').querySelectorAll('button[data-k]').forEach(b => b.classList.toggle('on', b.dataset.item ? !!(w.item && w.item.id === b.dataset.item) : b.dataset.k === M.cur));
    if (w.more) { $('#nmore').classList.add('open'); $('.nsub').classList.add('open'); } }
  M.go = k => { M.cur = k; const nav = $('#nav'); if (nav && nav.firstChild) markNav(); else rail(); M.render(); $('#view').scrollTop = 0; };

  // 메뉴 안의 탭 줄 (화면이 하나뿐이면 감춘다)
  function tabs() {
    const el = $('#tabs'); if (!el) return;
    const w = M.where(M.cur), vs = M.views();
    if (!w || !w.item) { el.innerHTML = ''; el.hidden = true; return; }
    const ts = w.item.tabs.filter(tb => keysOf(tb).some(k => vs[k]));
    const subs = w.tab[2] ? w.tab[2].filter(x => vs[x[0]]) : [];
    el.hidden = ts.length < 2 && subs.length < 2;
    el.innerHTML = (ts.length > 1 ? `<div class="tabs">${ts.map(tb => `<button data-k="${keysOf(tb).find(k => vs[k])}" class="${tb === w.tab ? 'on' : ''}">${esc(tb[1])}</button>`).join('')}</div>` : '')
      + (subs.length > 1 ? `<div class="subtabs">${subs.map(([k, t]) => `<button data-k="${k}" class="${k === M.cur ? 'on' : ''}">${esc(t)}</button>`).join('')}</div>` : '');
    el.querySelectorAll('button[data-k]').forEach(b => b.onclick = () => M.go(b.dataset.k));
  }

  M.render = () => {
    // AI 결과가 공정 데이터보다 먼저 도착해도 초기 로딩 화면을 유지한다.
    if (!M.S) return;
    const w = M.where(M.cur);
    let title = M.cur, grp = '';
    if (w && w.item) { title = w.item.tabs.length > 1 || w.tab[2] ? w.tab[1] : w.item.t; grp = w.item.tabs.length > 1 || w.tab[2] ? w.item.t : ''; }
    else if (w) { title = w.tab[1]; grp = '더보기 · ' + w.more; }
    $('#ttl').textContent = title; $('#crumb').textContent = grp;
    updBar(); pill(); tabs();
    const v = M.views()[M.cur];
    $('#view').innerHTML = v ? v() : '';
    tidy($('#view'));
    bind($('#view'));
  };
  // 화면 정돈: 현업 MES 처럼 — 설명 문장은 화면에 두지 않는다. 숫자 · 표 · 상태 · 결과 메시지만 남긴다
  const SENT = /(다|요|니다|음|함|됨|임|있음|없음|습니다)[.)]?$|—|→|\. /;   // 문장 · 해설 모양
  function tidy(root) {
    root.querySelectorAll('h2 > .h2sub, h3 > .h2sub').forEach(s => { const t = s.textContent.trim();
      if (!t || t.length > 14) s.remove(); });   // 짧은 부제(번호 · 개수 · 단위)만 남긴다
    root.querySelectorAll('.kstrip').forEach(k => k.classList.add('kbar'));
    // 표가 있는 카드마다 「엑셀」 단추 (현업 요구 1순위 — 화면에 보이는 표 그대로 .xlsx)
    if (window.MLNG_XLSX) root.querySelectorAll('.card').forEach(card => {
      const t = card.querySelector('table.tb'), h = card.querySelector(':scope > h2'); if (!t || !h || h.querySelector('.xbtn') || t.querySelectorAll('tbody tr').length < 2) return;
      const b = document.createElement('button'); b.className = 'xbtn'; b.type = 'button'; b.title = '이 표를 엑셀로 내려받기'; b.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>엑셀';
      if (!h.querySelector(':scope > .more')) b.style.marginLeft = 'auto';
      b.onclick = e => { e.stopPropagation(); const title = (h.firstChild && h.firstChild.textContent || '표').trim() || '표', X = window.MLNG_XLSX; X.download(`${title}_${(M.S.asof || '').slice(0, 10)}.xlsx`, [X.fromTable(t, title)]); };
      h.appendChild(b); });   // 모든 화면의 요약 줄을 같은 상자 · 같은 칸 폭으로
    root.querySelectorAll('.kstrip .kpi .foot').forEach(x => { if (!x.title) x.title = x.textContent.trim(); });
    // 안내 · 해설 문단은 지운다 — 결과 메시지(.note.msg)와 색으로 상태를 알리는 줄만 남긴다
    root.querySelectorAll('p.note:not(.msg):not([style*="color"])').forEach(n => n.remove());
    // 회색 설명 줄: 표 칸 · 요약 칸 밖에서 문장처럼 생긴 것은 지운다
    root.querySelectorAll('.muted').forEach(n => {
      if (n.closest('td,th,.kpi,.tag,button,label,select,option,.empty')) return;
      const t = n.textContent.replace(/\s+/g, ' ').trim();
      if (t.length > 24 && SENT.test(t)) n.remove(); });
    root.querySelectorAll('.hint').forEach(n => n.remove());
  }
  M.tidy = tidy;
  // 화면 안의 공통 동작: data-go(화면 이동) · data-row(상세) · data-insp(검사 기록) · data-set(모듈 상태 바꾸고 다시 그리기)
  function bind(root) {
    root.querySelectorAll('[data-go]').forEach(a => a.onclick = e => { e.preventDefault(); M.nav(a.dataset.go); });
    root.querySelectorAll('[data-row]').forEach(r => r.onclick = e => { if (e.target.closest('button,a,input,select')) return; M.nav(r.dataset.row); });
    root.querySelectorAll('[data-insp]').forEach(b => b.onclick = e => { e.stopPropagation(); M.openInsp(...b.dataset.insp.split('|')); });
    M.mods.forEach(m => m.bind && m.bind(root));
  }
  M.bind = bind;
  M.nav = k => {
    if (k.startsWith('link:')) { const u = (CFG.LINKS || {})[k.slice(5)]; if (u) window.open(u, '_blank', 'noopener'); return; }
    for (const m of M.mods) for (const [pre, fn] of Object.entries(m.drawers || {})) if (k.startsWith(pre)) return fn(k.slice(pre.length));
    if (M.views()[k]) M.go(k);
  };

  // ── 상세 서랍
  M.drawer = (sub, title, html) => {
    $('#dsub').textContent = sub; $('#dttl').textContent = title; $('#dbody').innerHTML = html;
    $('#drawer').classList.add('on'); $('#shade').classList.add('on'); $('#dbody').scrollTop = 0;
    tidy($('#dbody')); bind($('#dbody'));
  };
  // 요약 띠 항목 상세 — 제목·부제 + 블록 [{ h, html }]
  M.kdrawer = (sub, title, blocks) => M.drawer(sub, title, blocks.filter(Boolean).map(b => `${b.h ? `<h3 class="dh">${esc(b.h)}</h3>` : ''}${b.html}`).join(''));
  M.dl = (pairs, cls) => `<dl class="stat ${cls || ''}">${pairs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>`;
  M.closeDrawer = () => { $('#drawer').classList.remove('on'); $('#shade').classList.remove('on'); };

  // ── 검사 기록 창 (모든 공정 공용 → mlng_inspections)
  M.openInsp = (proc, lot, target) => {
    const procs = [...new Set(M.S.masters.equipment.map(e => e.proc))];
    $('#d-proc').innerHTML = procs.map(p => `<option value="${p}">${esc(M.PROC[p] || p)}</option>`).join('');
    $('#d-proc').value = proc || procs[0];
    const codes = () => { const p = $('#d-proc').value; $('#d-code').innerHTML = '<option value="">—</option>' + M.S.masters.defect_codes.filter(c => c.proc === p).map(c => `<option value="${esc(c.code)}">${esc(c.name)} (${esc(c.code)})</option>`).join(''); };
    $('#d-proc').onchange = codes; codes();
    $('#d-lot').value = lot || ''; $('#d-target').value = target || ''; $('#d-res').value = '불량'; $('#d-qty').value = 1; $('#d-note').value = ''; $('#d-msg').textContent = '';
    $('#dlg').style.display = 'flex';
  };
  async function saveInsp() {
    const rec = { proc: $('#d-proc').value, lot_id: $('#d-lot').value.trim() || null, target: $('#d-target').value.trim() || null, result: $('#d-res').value, defect_code: $('#d-code').value || null,
      qty: Math.max(1, parseInt($('#d-qty').value, 10) || 1), note: $('#d-note').value.trim() || null };
    if (!rec.lot_id) { $('#d-msg').textContent = 'LOT 을 적어 주세요'; return; }
    try { const r = await D.addInspection(rec); if (D.LIVE) M.S.inspections = [r].concat(M.S.inspections || []); $('#dlg').style.display = 'none'; M.closeDrawer(); rail(); M.render(); }
    catch (e) { $('#d-msg').textContent = '저장 실패: ' + e.message; }
  }

  // ── 테마: 시스템 → 라이트 → 다크
  const THEME_IC = { system: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
    light: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    dark: '<svg viewBox="0 0 24 24"><path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/></svg>' };
  const THEME_TXT = { system: '시스템', light: '라이트', dark: '다크' };
  const getTheme = () => { try { const t = localStorage.getItem('mlng_theme'); return t === 'light' || t === 'dark' ? t : 'system'; } catch (e) { return 'system'; } };
  function setTheme(t) {
    if (t === 'system') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', t);
    try { t === 'system' ? localStorage.removeItem('mlng_theme') : localStorage.setItem('mlng_theme', t); } catch (e) { /* 저장 못 해도 화면은 바뀐다 */ }
    $('#themebtn').innerHTML = THEME_IC[t]; $('#themebtn').title = `화면 테마: ${THEME_TXT[t]} (누르면 바꿈)`;
  }

  // ── 실시간 반영: 켜면 서버에서 주기적으로 다시 읽어 화면 수치를 바꾸고, 끄면 지금 화면을 멈춘 채로 둔다
  const LIVE_SEC = () => Math.max(10, CFG.LIVE_SEC || 30);
  let liveOn = (() => { try { return localStorage.getItem('mlng_live') !== 'off'; } catch (e) { return true; } })();
  let liveTimer = null, lastPull = null, pulling = false;
  function updBar() {
    const S = M.S || {}, t = lastPull ? lastPull.toTimeString().slice(0, 8) : '';
    $('#upd').innerHTML = D.LIVE ? (liveOn ? `<span class="dot ok pulse"></span>실시간 · 갱신 ${esc(t || String(S.built || '').slice(11, 19))}` : `<span class="dot warn"></span>멈춤 · ${esc(t || String(S.built || '').slice(11, 19))} 기준`)
      : `<span class="demo-chip" title="${esc(`공개 데이터셋(${S.demo_from ? String(S.demo_from).slice(0, 10) : '원본'})을 ${S.demo_shift || 0}일 옮겨 오늘 기준으로 보여 주는 시연용 데이터입니다. 실제 설비 연결 시 자동으로 실시간 데이터로 바뀝니다.`)}">데모 데이터</span><span class="dot ok"></span>${esc(S.asof || '')} 기준`;
    const b = $('#livebtn'); if (!b) return;
    b.disabled = !D.CONNECTED; b.hidden = !D.CONNECTED;
    b.classList.toggle('on', D.CONNECTED && liveOn);
    b.innerHTML = !D.CONNECTED ? '실시간 · 서버 미연결' : liveOn ? '<i></i>실시간 반영 중' : '<i></i>멈춤 (누르면 실시간)';
    b.title = !D.CONNECTED ? 'config.js 에 Supabase 주소·키를 넣으면 실시간 반영을 쓸 수 있습니다'
      : liveOn ? `${LIVE_SEC()}초마다 서버에서 다시 읽습니다 — 누르면 지금 화면을 멈춥니다` : '지금 화면을 멈춰 두었습니다 — 누르면 실시간으로 다시 읽습니다';
  }
  async function pull() {
    if (pulling || !liveOn || !D.CONNECTED || document.hidden) return;
    const a = document.activeElement; if (a && /INPUT|TEXTAREA|SELECT/.test(a.tagName) && $('#view').contains(a)) return;   // 입력 중이면 이번 차례는 건너뜀
    pulling = true;
    try {
      if (!D.LIVE && !(await D.probe())) return;          // 표가 아직 비어 있으면 데모 그대로
      const S = await D.load();
      if (S && D.LIVE) { const keep = M.S && M.S.purchase && !S.purchase.orders.length ? M.S.purchase : null; M.S = S; if (keep) M.S.purchase = keep; lastPull = new Date(); rail(); M.render(); }
    } catch (e) { console.warn('[mlng] 실시간 갱신 실패', e.message); }
    finally { pulling = false; updBar(); }
  }
  function setLive(on) {
    liveOn = on; try { localStorage.setItem('mlng_live', on ? 'on' : 'off'); } catch (e) { /* 무시 */ }
    clearInterval(liveTimer); liveTimer = null;
    if (on && D.CONNECTED) { liveTimer = setInterval(pull, LIVE_SEC() * 1000); pull(); }
    updBar();
  }
  M.live = { on: () => liveOn, set: setLive };

  M.start = async () => {
    if (D.needLogin()) { $('#login').style.display = 'flex'; return; }
    $('#login').style.display = 'none';
    $('#view').innerHTML = '<div class="empty" role="status">데이터를 불러오는 중입니다…</div>';
    try { M.S = await D.load(); } catch (e) {
      $('#view').innerHTML = `<div class="empty" role="alert">데이터를 불러오지 못했습니다 — ${esc(e.message)}<p><button id="data-retry" class="btn">다시 불러오기</button></p></div>`;
      $('#data-retry').onclick = () => M.start();
      return;
    }
    lastPull = D.LIVE ? new Date() : null; rail(); M.render(); setLive(liveOn);
    // 2.0 신뢰 계층(키 생성·서명)은 첫 화면을 그린 뒤 이어서 준비 — 끝나면 다시 그린다
    if (window.MLNGTrust) window.MLNGTrust.init().then(() => { rail(); M.render(); }).catch(e => console.warn('[trust]', e));
  };
  M.init = () => {
    $('#l-go').onclick = async () => { $('#l-err').textContent = ''; try { await D.login($('#l-id').value.trim(), $('#l-pw').value); M.start(); } catch (e) { $('#l-err').textContent = e.message; } };
    $('#l-pw').onkeydown = e => { if (e.key === 'Enter') $('#l-go').click(); };
    $('#logout').style.display = 'none';
    $('#d-cancel').onclick = () => { $('#dlg').style.display = 'none'; };
    $('#d-save').onclick = saveInsp;
    setTheme(getTheme());
    $('#themebtn').onclick = () => setTheme({ system: 'light', light: 'dark', dark: 'system' }[getTheme()]);
    if (!$('#livebtn')) { const lb = document.createElement('button'); lb.id = 'livebtn'; lb.className = 'livebtn'; $('#themebtn').before(lb); }
    $('#livebtn').onclick = () => setLive(!liveOn);
    $('#dclose').onclick = M.closeDrawer; $('#shade').onclick = M.closeDrawer;
    document.addEventListener('keydown', e => { if (e.key === 'Escape') { M.closeDrawer(); $('#dlg').style.display = 'none'; } });
    M.start();
  };
})();
