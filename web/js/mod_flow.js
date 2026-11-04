/* 5.0 — 주문부터 출하까지 한 줄로 (대시보드 · 주문 현황)
 * ───────────────────────────────────────────────────────────────
 * MES 의 첫 화면은 «지금 어느 주문이 어디서 막혀 있나» 를 답해야 한다.
 *   주문 → 자재 → 생산 → 품질 → 출하 다섯 단계마다 상태 신호등 · 숫자 하나 · 문제 개수
 *   단계를 누르면 아래 «주문 진행» 과 «처리할 문제» 가 그 단계로 좁혀진다.
 * 주문은 실제 열처리 LOT 에 묶는다 — 주문을 열면 그 주문의 LOT, 품질 판정, 출하 상태가 그대로 보인다.
 * (데모 주문 장부: 최근 열처리 LOT 을 납품 단위로 묶어 만든 수주. 실시간이면 mlng_orders 표로 바꾼다)
 */
(function () {
  'use strict';
  const M = window.MLNG, { esc, f, pct } = M;
  const T = () => window.MLNGTrust || {};
  const H = () => M.S.heat || { lots: [] };
  const DAY = 86400000;
  const CUST = { 'T1-B': '1차 협력사 B', 'T1-E': '1차 협력사 E', 'T1-F': '1차 협력사 F' };
  const CUST_SHORT = { 'T1-B': 'B사', 'T1-E': 'E사', 'T1-F': 'F사' };
  const STAGE = [
    { k: 'order', t: '수주', i: 'deal', go: 'orders' },
    { k: 'mat', t: '자재', i: 'cart', go: 'buy_home' },
    { k: 'prod', t: '생산', i: 'gauge', go: 'line' },
    { k: 'qual', t: '품질', i: 'check', go: 'cases' },
    { k: 'ship', t: '출하', i: 'gate', go: 'gate' },
  ];
  const SN = Object.fromEntries(STAGE.map(s => [s.k, s.t]));
  const RANK = { danger: 0, warn: 1, ok: 2, done: 3 };
  const ymd = t => new Date(t).toLocaleString('sv-SE').slice(0, 10);
  const man = v => v >= 10000 ? `${f(v / 10000, v >= 100000 ? 0 : 1)}만` : f(v, 0);
  M.man = man;
  const dday = due => { const d = Math.round((M.toDate(due).setHours(0, 0, 0, 0) - new Date(M.now()).setHours(0, 0, 0, 0)) / DAY); return d === 0 ? 'D-day' : d > 0 ? `D-${d}` : `D+${-d}`; };

  // ── LOT 출하 상태: 납품 · 보류 · 보류 권고 · 출하 대기 · (게이트 기간 이전) 출하 완료
  function lotState(l, ctx) {
    const d = ctx.del.get(l.id);
    if (d) return d.claim && d.claim.status === 'open' ? { k: 'claim', t: '클레임', lv: 'danger' } : { k: 'shipped', t: '납품 완료', lv: 'done' };
    if (!ctx.cand.has(l.id)) return { k: 'shipped', t: '출하 완료', lv: 'done' };
    const G = M.gate; if (G && G.isHeld(l.id)) return { k: 'held', t: '출하 보류', lv: 'danger' };
    const r = G && T().ready ? G.predict(l.id) : null;
    if (r && r.rec === 'hold') return { k: 'hold_rec', t: '보류 권고', lv: 'warn', why: (r.why || [])[0] || '' };
    return { k: 'ready', t: '출하 대기', lv: 'ok' };
  }

  // ── 주문 장부 (데이터 · 원장 길이가 같으면 다시 계산하지 않는다)
  function book() {
    return M.memo('flow.book', '', () => {
      const lots = H().lots.slice().sort((a, b) => String(a.end).localeCompare(String(b.end))), now = M.now().getTime();
      const del = new Map(); (T().orders || []).forEach(o => o.deliveries.forEach(d => del.set(d.lot, d)));
      const cand = new Set(M.gate ? M.gate.candidates().map(l => l.id) : []);
      const ctx = { del, cand };
      const recent = lots.filter(l => M.toDate(l.end).getTime() >= now - 32 * DAY);
      const d7 = lots.filter(l => M.toDate(l.end).getTime() >= now - 7 * DAY), rate = d7.reduce((s, l) => s + (l.good ?? l.qty - (l.bad || 0)), 0) / 7 || 1;
      const TG = [150000, 180000, 120000, 160000, 200000, 140000, 170000, 130000], CU = ['T1-B', 'T1-E', 'T1-B', 'T1-F', 'T1-B', 'T1-E', 'T1-B', 'T1-F'], SLACK = [3, 2, 4, 2, 3, 1, 3, 2];
      const orders = []; let cur = null, i = 0;
      const open = () => { cur = { n: i, target: TG[i % TG.length], buyer: CU[i % CU.length], lots: [] }; i++; };
      recent.forEach(l => { if (!cur) open(); cur.lots.push(l); const g = cur.lots.reduce((s, x) => s + x.qty, 0); if (g >= cur.target * 0.97) { orders.push(cur); cur = null; } });
      if (cur) orders.push(cur);
      const item = M.S.masters.items[0] || {};
      const out = orders.map((o, k) => {
        const first = M.toDate(o.lots[0].start || o.lots[0].end).getTime(), last = M.toDate(o.lots[o.lots.length - 1].end).getTime(), partial = k === orders.length - 1;
        const good = o.lots.reduce((s, x) => s + (x.good ?? x.qty - (x.bad || 0)), 0);
        const qty = partial ? Math.ceil((good + rate * 1.6) / 10000) * 10000 : Math.floor(good / 1000) * 1000;
        const created = ymd(first - 4 * DAY), due = partial ? ymd(now + 3 * DAY) : ymd(last + SLACK[k % SLACK.length] * DAY);
        return mk({ id: `OR-${created.slice(2).replace(/-/g, '')}-${String(k + 1).padStart(2, '0')}`, buyer: o.buyer, item: item.code, item_name: item.name, qty, created, due, lots: o.lots }, ctx, rate, now);
      });
      // 아직 착수 전인 주문 2건
      [[8, 150000, 'T1-E'], [13, 200000, 'T1-B']].forEach(([dd, q, b], j) => { const cr = ymd(now - (1 - j) * DAY); out.push(mk({ id: `OR-${cr.slice(2).replace(/-/g, '')}-${String(out.length + 1).padStart(2, '0')}`, buyer: b, item: item.code, item_name: item.name, qty: q, created: cr, due: ymd(now + dd * DAY), lots: [] }, ctx, rate, now)); });
      return { orders: out.reverse(), rate, ctx };
    });
  }
  function mk(o, ctx, rate, now) {
    o.ls = o.lots.map(l => Object.assign({ l }, lotState(l, ctx)));
    o.made = o.lots.reduce((s, l) => s + (l.good ?? l.qty - (l.bad || 0)), 0);
    o.remain = Math.max(0, o.qty - o.made);
    const cnt = k => o.ls.filter(x => x.k === k);
    o.shippedQty = o.ls.filter(x => x.k === 'shipped' || x.k === 'claim').reduce((s, x) => s + x.l.qty, 0);
    o.daysLeft = (M.toDate(o.due).setHours(23, 59) - now) / DAY;
    o.needDays = o.remain / rate;
    const bad = cnt('held').concat(cnt('hold_rec'), cnt('claim'));
    o.stage = !o.lots.length ? 'order' : o.remain > 0 ? 'prod' : bad.length ? 'qual' : cnt('ready').length ? 'ship' : 'done';
    const late = o.daysLeft < 0 && o.stage !== 'done';
    const tight = o.remain > 0 && o.needDays > o.daysLeft;
    o.status = late ? 'danger' : (tight || bad.length) ? 'warn' : o.stage === 'done' ? 'done' : 'ok';
    o.statusT = late ? '납기 지연' : tight ? '지연 우려' : bad.length ? '품질 보류' : o.stage === 'done' ? '출하 완료' : '정상';
    o.issue = bad.length ? `보류 권고 ${bad.length} LOT (${bad.map(x => x.l.id).join(', ')})`
      : o.remain > 0 ? `잔량 ${man(o.remain)}개 · 소요 ${f(Math.max(0.1, o.needDays), 1)}일 / 납기 잔여 ${f(Math.max(0, o.daysLeft), 1)}일`
      : cnt('ready').length ? `출하 대기 ${cnt('ready').length} LOT` : '';
    o.issueStage = bad.length ? 'qual' : o.stage;
    // 단계별 진척 (0~1)
    const madeLots = o.lots.length, okLots = o.ls.filter(x => !['held', 'hold_rec', 'claim'].includes(x.k)).length;
    o.prog = { order: 1, mat: o.lots.length || o.daysLeft < 6 ? 1 : 0, prod: Math.min(1, o.made / o.qty), qual: madeLots ? okLots / madeLots * Math.min(1, o.made / o.qty) : 0, ship: Math.min(1, o.shippedQty / o.qty) };
    return o;
  }

  // ── 단계 요약 + 처리할 문제 (대시보드)
  function stages() {
    const B = book(), al = M.alerts(), cs = M.caseApi && T().ready ? M.caseApi.open() : [], DX = M.dx;
    const active = B.orders.filter(o => o.stage !== 'done');
    const issues = [];
    const caseStage = { tool: 'prod', press: 'prod', heat: 'qual', weld: 'qual', insp: 'qual', plan: 'order' };
    const procStage = p => ({ buy: 'mat', plan: 'order', gate: 'ship', deal: 'ship', insp: 'qual', case: 'qual' })[p] || 'prod';
    cs.forEach(c => issues.push({ st: caseStage[c.tpl] || procStage(c.proc), lv: c.level, t: c.name, s: `${c.id} · 영향 LOT ${c.lots.length} · 관련 알람 ${c.alerts.length}건 · ${M.caseApi.STAGES[c.st.stage]}`, won: c.money ? c.money.won : 0, go: 'case:' + c.id, btn: '조치' }));
    const inCase = new Set([].concat(...cs.map(c => c.alerts.map(a => a.title))));
    al.filter(a => a.level !== 'info' && !inCase.has(a.title)).forEach(a => issues.push({ st: procStage(a.proc), lv: a.level, t: a.title, s: a.body || '', won: DX ? (DX.money(a) || {}).won || 0 : 0, go: a.go, btn: '확인' }));
    active.filter(o => o.status === 'danger' || (o.status === 'warn' && o.stage !== 'qual')).forEach(o => issues.push({ st: o.issueStage === 'qual' ? 'qual' : 'order', lv: o.status, t: `수주 ${o.id} (${CUST_SHORT[o.buyer]}) ${o.statusT}`, s: o.issue, won: 0, go: 'ord:' + o.id, btn: '수주' }));
    const by = k => issues.filter(x => x.st === k), worst = xs => xs.some(x => x.lv === 'danger') ? 'danger' : xs.some(x => x.lv === 'warn') ? 'warn' : 'ok';
    const buy = M.buyApi, recs = buy ? buy.recs() : [], pend = buy ? buy.orders().filter(x => x.status === 'pending') : [];
    recs.filter(r => !issues.some(x => x.st === 'mat' && x.t.includes(r.it.name))).forEach(r => issues.push({ st: 'mat', lv: r.level, t: `${r.it.name} 재고 부족 — 발주 필요 ${r.qty}${r.it.unit || ''}`, s: r.reason, won: 0, go: 'buy_home', btn: '발주' }));
    const hl = H().lots, now = M.now().getTime(), last7 = hl.filter(l => M.toDate(l.end).getTime() >= now - 7 * DAY);
    const q7 = last7.reduce((s, l) => s + l.qty, 0), b7 = last7.reduce((s, l) => s + (l.bad || 0), 0);
    const today = hl.filter(l => ymd(M.toDate(l.end)) === ymd(M.now())), tq = today.reduce((s, l) => s + (l.good ?? l.qty), 0);
    const insp = M.S.insp ? (() => { const ps = new Map(); M.S.insp.records.forEach(r => ps.set(r.sn, (ps.get(r.sn) ?? true) && r.ok)); return ps.size ? 1 - [...ps.values()].filter(Boolean).length / ps.size : null; })() : null;
    const cnt = k => [].concat(...B.orders.map(o => o.ls)).filter(x => x.k === k).length;
    const S = {
      order: { big: active.length, unit: '건', lab: '진행 수주', sub: `납기 지연 ${active.filter(o => o.status === 'danger').length} · 지연 우려 ${active.filter(o => o.status === 'warn').length}` },
      mat: { big: recs.length, unit: '종', lab: '재고 부족', sub: `발주 승인 대기 ${pend.length}건` },
      prod: { big: man(tq), unit: '개', lab: '금일 생산 실적', sub: `일 평균 ${man(B.rate)}개` },
      qual: { big: f(q7 ? b7 / q7 * 1e6 : 0, 0), unit: 'PPM', lab: '열처리 공정 불량률 (7일)', sub: insp != null ? `검사 불합격률 ${pct(insp, 1)}` : '' },
      ship: { big: cnt('ready'), unit: 'LOT', lab: '출하 대기', sub: `보류 권고 ${cnt('hold_rec')} · 출하 보류 ${cnt('held')} · 클레임 ${cnt('claim')}` },
    };
    STAGE.forEach(s => { const xs = by(s.k); S[s.k].n = xs.length; S[s.k].lv = worst(xs); });
    // 주문 단계 신호등은 납기로 판단한다 (원인이 품질이어도 고객에게는 납기 지연)
    S.order.lv = active.some(o => o.status === 'danger') ? 'danger' : active.some(o => o.status === 'warn') || S.order.lv === 'warn' ? 'warn' : S.order.lv;
    // 순서: 큰돈(100만 원 이상)이 걸린 문제 → 긴급 → 주의, 같은 줄에서는 금액 순
    const pri = x => x.won >= 1e6 ? -1 : RANK[x.lv];
    issues.sort((a, b) => pri(a) - pri(b) || (b.won || 0) - (a.won || 0));
    return { B, S, issues, active };
  }

  // ── 그림 조각
  const progBar = o => `<div class="opg" title="${STAGE.map(s => `${s.t} ${Math.round(o.prog[s.k] * 100)}%`).join(' · ')}">${STAGE.map(s => { const v = o.prog[s.k], bad = s.k === o.issueStage && o.status !== 'ok' && o.status !== 'done';
    return `<i class="${v >= 0.999 ? 'full' : v > 0 ? 'part' : ''} ${bad ? o.status : ''}"><b style="width:${Math.round(v * 100)}%"></b></i>`; }).join('')}</div>`;
  const stBadge = o => `<span class="ost ${o.status}">${esc(o.statusT)}</span>`;
  // ── 주문 현황판: 행 = 주문, 열 = 주문 → 자재 → 생산 → 품질 → 출하 (현장 현황판 형태)
  function cells(o) {
    const q = o.ls.filter(x => ['held', 'hold_rec', 'claim'].includes(x.k)), shipN = o.ls.filter(x => x.k === 'shipped' || x.k === 'claim').length;
    const tight = o.remain > 0 && o.needDays > o.daysLeft;
    return {
      order: { c: 'done', t: `수주 ${o.created.slice(5)}` },
      mat: o.prog.mat ? { c: 'done', t: '확보' } : { c: 'wait', t: '미확보' },
      prod: !o.lots.length ? { c: 'wait', t: '미착수' } : o.remain > 0 ? { c: tight ? (o.daysLeft < 0 ? 'danger' : 'warn') : 'now', t: `${pct(Math.min(1, o.made / o.qty), 0)}`, s: `잔량 ${man(o.remain)}`, v: o.made / o.qty } : { c: 'done', t: '완료', v: 1 },
      qual: !o.lots.length ? { c: 'wait', t: '—' } : q.length ? { c: o.status === 'danger' ? 'danger' : 'warn', t: `${q.length} LOT 보류`, s: q.map(x => x.l.id).slice(0, 2).join(', ') } : { c: o.remain > 0 ? 'now' : 'done', t: `합격 ${o.lots.length} LOT` },
      ship: !o.shippedQty ? { c: o.stage === 'ship' ? 'now' : 'wait', t: o.stage === 'ship' ? '출하 대기' : '—' } : o.stage === 'done' ? { c: 'done', t: '완료', v: 1 } : { c: 'now', t: pct(Math.min(1, o.shippedQty / o.qty), 0), s: `${shipN}/${o.lots.length} LOT`, v: o.shippedQty / o.qty },
    };
  }
  const cellH = x => `<div class="mc ${x.c}">${x.v != null && x.v < 1 ? `<i style="width:${Math.round(x.v * 100)}%"></i>` : ''}<b>${esc(x.t)}</b>${x.s ? `<em>${esc(x.s)}</em>` : ''}</div>`;
  function board(os, S, opt = {}) {
    if (!os.length) return `<div class="empty">${opt.empty || '해당 주문이 없습니다'}</div>`;
    return `<div class="tbx"><table class="tb mtx ${opt.issue ? 'mtx-wide' : ''}"><colgroup><col class="c-id"><col class="c-qty"><col class="c-due">${STAGE.map(() => '<col class="c-st">').join('')}<col class="c-state">${opt.issue ? '<col class="c-iss">' : ''}</colgroup>
      <thead><tr><th>수주 번호 · 고객</th><th class="n">수주 수량</th><th>납기</th>${STAGE.map(s => { const x = S && S[s.k];
        return `<th class="sth ${sel === s.k ? 'on' : ''}" ${S ? `data-fsel="${s.k}" title="${esc(x ? x.lab + ' ' + x.big + x.unit : '')}"` : ''}>${s.t}${x && x.n ? `<span class="sn ${x.lv}">${x.n}</span>` : ''}</th>`; }).join('')}<th>진행 상태</th>${opt.issue ? '<th>이슈 사항</th>' : ''}</tr></thead>
      <tbody>${os.map(o => { const c = cells(o);
        return `<tr class="click" data-row="ord:${esc(o.id)}"><td><b>${esc(o.id)}</b><div class="muted sm1">${esc(CUST_SHORT[o.buyer])} · ${esc(o.item)}</div></td><td class="n">${f(o.qty, 0)}</td>
          <td>${esc(o.due.slice(5))}<div class="dd ${o.status === 'danger' ? 'late' : o.daysLeft < 3 && o.stage !== 'done' ? 'soon' : ''}">${o.stage === 'done' ? '' : dday(o.due)}</div></td>
          ${STAGE.map(s => `<td class="stc">${cellH(c[s.k])}</td>`).join('')}<td>${stBadge(o)}</td>${opt.issue ? `<td><span class="oiss">${esc(o.issue || '—')}</span></td>` : ''}</tr>`; }).join('')}</tbody></table></div>`;
  }

  // ── 대시보드 (현장 현황판): KPI 한 줄 → 주문 현황판 → 생산 실적 | 공정 상태 → 처리할 문제
  let sel = null;
  const V = {};
  const PROCS = [['press', '프레스 성형', 'press_home'], ['weld', '점용접', 'weld_home'], ['heat', '열처리', 'heat_home'], ['mach', 'MCT 가공', 'mach_home'], ['insp', '정밀 검사', 'insp_home']];
  V.overview = () => {
    const { B, S, issues, active } = stages(), DX = M.dx, al = M.alerts();
    const iss = issues.filter(x => !sel || x.st === sel), ords = active.filter(o => !sel || o.stage === sel || o.issueStage === sel)
      .sort((a, b) => RANK[a.status] - RANK[b.status] || a.daysLeft - b.daysLeft);
    // 일별 생산 (열처리 양품) 14일 + 필요량 (진행 주문을 납기에 맞추려면 하루에 얼마)
    // 최근 14 작업일 (가동하지 않은 날은 건너뛴다)
    const hl = H().lots, now = M.now(), byD = new Map();
    hl.forEach(l => { const d = ymd(M.toDate(l.end)); byD.set(d, (byD.get(d) || 0) + (l.good ?? l.qty)); });
    const days = [...byD.keys()].sort().slice(-14).map(d => ({ d, v: byD.get(d) }));
    if (!days.length || days[days.length - 1].d !== ymd(now)) days.push({ d: ymd(now), v: 0 });
    const need = active.filter(o => o.remain > 0).reduce((s, o) => s + o.remain / Math.max(1, o.daysLeft), 0);
    const late = active.filter(o => o.status === 'danger').length, onTime = active.length ? 1 - active.filter(o => o.status !== 'ok').length / active.length : 1;
    const today = days[days.length - 1].v, cs = M.caseApi && T().ready ? M.caseApi.open() : [];
    const kpi = (lab, val, unit, foot, lv, go) => `<div class="kpi ${go ? 'click' : ''}" ${go ? `data-go="${go}"` : ''}><div class="lab">${lab}</div><div class="val" ${lv ? `style="color:var(--${lv}-ink)"` : ''}>${val}<small>${unit}</small></div><div class="foot">${foot}</div></div>`;
    const procRow = ([p, nm, go]) => { const m = M.mods.find(x => x.proc === p), sm = m && m.summary ? (() => { try { return m.summary(M.S); } catch (e) { return null; } })() : null;
      const a = al.filter(x => x.proc === p && x.level !== 'info'), st = a.some(x => x.level === 'danger') ? 'danger' : a.length || (sm && sm.st === 'warn') ? 'warn' : sm ? (sm.st === 'danger' ? 'danger' : 'ok') : 'off';
      const eqId = (M.S.masters.routings.find(r => r.proc === p) || {}).equipment_id || '';
      return { p, nm, go, eqId, sm, a, st }; };
    const pr = PROCS.map(procRow);
    return `<div class="kstrip kbar dashboard-kpis">
      ${kpi('금일 생산 실적', man(today), '개', `일 필요량 ${man(need)}개 · ${today >= need ? '충족' : '미달'}`, today < need ? 'warn' : '', 'heat_lots')}
      ${kpi('진행 수주', active.length, '건', `납기 지연 ${late} · 지연 우려 ${active.filter(o => o.status === 'warn').length}`, late ? 'danger' : '', 'orders')}
      ${kpi('납기 준수율 (예상)', pct(onTime, 0), '', '진행 수주 중 정상 진행', onTime < 0.8 ? 'warn' : '', 'orders')}
      ${kpi('공정 불량률', S.qual.big, 'PPM', '열처리 · 최근 7일 · 관리 기준 450', '', 'heat_home')}
      ${kpi('출하 대기', S.ship.big, 'LOT', S.ship.sub, '', 'gate')}
      ${kpi('미결 품질 이슈' + (M.asmTag ? M.asmTag('loss') : ''), cs.length, '건', DX ? `예상 손실 ${DX.wonShort(cs.reduce((t, c) => t + (c.money ? c.money.won : 0), 0))}원` : '', cs.some(c => c.level === 'danger') ? 'danger' : '', 'cases')}
    </div>
    <div class="card dsec"><h2>수주별 진행 현황 <span class="h2sub">${ords.length}건</span>${sel ? `<span class="fchip">${SN[sel]} 단계만 <button data-fsel="">×</button></span>` : ''}<a class="more" href="#" data-go="orders">수주 현황 →</a></h2>
      ${board(ords, S, { issue: false, empty: sel ? `${SN[sel]} 단계에 해당하는 수주가 없습니다` : '진행 중인 수주가 없습니다' })}</div>
    <div class="dgrid">
      <div class="card"><h2>일별 생산 실적 <span class="h2sub">최근 14 가동일</span><a class="more" href="#" data-go="heat_lots">작업 실적 →</a></h2>
        ${M.barChart(days.map(d => ({ x: d.d.slice(5), v: d.v, t: `${d.d} · ${f(d.v, 0)}개` })), { h: 236, ref: need, refLabel: `일 필요량 ${man(need)}` })}
        ${M.legend([['l-barc', '양품 수량 (열처리 완료 기준)'], ['l-lim', '일 필요 생산량 = 수주 잔량 ÷ 납기 잔여 일수']])}</div>
      <div class="card"><h2>공정별 가동 · 품질 현황<a class="more" href="#" data-go="line">라인 밸런스 →</a></h2>
        <div class="tbx"><table class="tb ptab"><colgroup><col class="c-pn"><col class="c-pe"><col class="c-pk"><col class="c-pa"></colgroup>
          <thead><tr><th>공정</th><th>설비</th><th>주요 지표</th><th class="n">알람</th></tr></thead>
          <tbody>${pr.map(x => `<tr class="click" data-row="${x.go}"><td><span class="dot ${x.st}"></span><b>${esc(x.nm)}</b></td><td class="muted">${esc(x.eqId)}</td>
            <td>${x.sm ? `<b class="${x.st}">${esc(x.sm.kpi)}</b>` : '<span class="muted">—</span>'}</td><td class="n">${x.a.length ? `<b class="${x.st}">${x.a.length}</b>` : '<span class="muted">0</span>'}</td></tr>`).join('')}</tbody></table></div></div>
    </div>
    <div class="card dsec"><h2>이상 발생 · 조치 현황 <span class="h2sub">미결 ${iss.length}건</span><a class="more" href="#" data-go="alerts">알람 이력 →</a></h2>
      ${iss.length ? `<div class="tbx"><table class="tb itab"><colgroup><col class="c-lv"><col class="c-sg"><col><col class="c-won"><col class="c-go"></colgroup>
        <thead><tr><th>등급</th><th>구분</th><th>이상 내용</th><th class="n">예상 손실${M.asmTag ? M.asmTag('loss') : ''}</th><th></th></tr></thead>
        <tbody>${iss.slice(0, 8).map(x => `<tr class="click ${x.lv}" ${x.go ? `data-row="${esc(x.go)}"` : ''}><td>${M.gradeTag(x.lv === 'danger' ? 'A' : x.lv === 'warn' ? 'B' : 'C', true)}</td><td>${SN[x.st]}</td>
          <td><div class="it1">${esc(x.t)}</div><div class="it2">${esc(x.s)}</div></td><td class="n">${x.won > 0 && DX ? DX.wonShort(x.won) + '원' : '<span class="muted">—</span>'}</td><td class="n"><span class="golink">${esc(x.btn || '보기')} ›</span></td></tr>`).join('')}</tbody></table></div>`
        : `<div class="empty">${sel ? SN[sel] + ' 단계에 ' : ''}미결 이상이 없습니다</div>`}</div>`;
  };

  // ── 주문 현황
  let of = 'act';
  V.orders = () => {
    const B = book(), os = B.orders, act = os.filter(o => o.stage !== 'done');
    const F = { act: ['진행 중', o => o.stage !== 'done'], risk: ['지연 · 지연 우려', o => o.status === 'danger' || o.status === 'warn'], done: ['출하 완료', o => o.stage === 'done'], all: ['전체', () => true] };
    const rows = os.filter(F[of][1]);
    const onTime = act.length ? act.filter(o => o.status === 'ok').length / act.length : 1;
    return `<div class="kstrip kbar">
      <div class="kpi"><div class="lab">진행 수주</div><div class="val">${act.length}<small>건</small></div><div class="foot">수량 ${f(act.reduce((s, o) => s + o.qty, 0), 0)}개</div></div>
      <div class="kpi"><div class="lab">납기 준수율 (예상)</div><div class="val" style="color:${onTime < 0.8 ? 'var(--warn-ink)' : 'inherit'}">${pct(onTime, 0)}</div><div class="foot">진행 수주 중 정상 진행</div></div>
      <div class="kpi"><div class="lab">수주 잔량</div><div class="val">${man(act.reduce((s, o) => s + o.remain, 0))}<small>개</small></div><div class="foot">일 평균 ${man(B.rate)}개 기준 소요 ${f(act.reduce((s, o) => s + o.remain, 0) / B.rate, 1)}일</div></div>
      <div class="kpi"><div class="lab">출하 실적</div><div class="val">${man(os.reduce((s, o) => s + o.shippedQty, 0))}<small>개</small></div><div class="foot">출하 완료 수주 ${os.filter(o => o.stage === 'done').length}건 · 최근 1개월</div></div>
    </div>
    <div class="card dsec"><div class="tools">${M.seg('of', of, Object.entries(F).map(([k, v]) => [k, `${v[0]} ${os.filter(v[1]).length}`]))}</div>
      ${board(rows, null, { issue: true })}</div>`;
  };

  function orderDrawer(id) {
    const o = book().orders.find(x => x.id === id); if (!o) return;
    const cases = M.caseApi && T().ready ? [].concat(...o.lots.map(l => M.caseApi.casesOfLot(l.id))) : [];
    const uc = [...new Map(cases.map(c => [c.id, c])).values()];
    M.drawer(`${CUST[o.buyer] || o.buyer} · ${o.item}`, `수주 ${o.id}`, `
      <div class="odh">${stBadge(o)} <span class="muted">${esc(o.issue || '')}</span></div>
      <div class="mrow">${(c => STAGE.map(s => `<div><span>${s.t}</span>${cellH(c[s.k])}</div>`).join(''))(cells(o))}</div>
      ${M.dl([['품목', `${esc(o.item)} ${esc(o.item_name || '')}`], ['수주 수량', f(o.qty, 0) + '개'], ['수주일', esc(o.created)], ['납기', `${esc(o.due)} (${dday(o.due)})`], ['생산 실적', `${f(o.made, 0)}개 (${pct(Math.min(1, o.made / o.qty), 0)})`], ['출하 실적', `${f(o.shippedQty, 0)}개`]])}
      ${uc.length ? `<h3 class="dh">관련 품질 이슈</h3><div class="lst">${uc.map(c => `<div class="it click" data-row="case:${esc(c.id)}"><span class="dot danger"></span><div class="m"><div class="t">${esc(c.name)}</div><div class="s">${esc(c.id)}</div></div></div>`).join('')}</div>` : ''}
      <h3 class="dh">투입 LOT ${o.lots.length}개</h3>
      ${M.table([['LOT', x => `<b>${esc(x.l.id)}</b>`], ['완료 시각', x => esc(String(x.l.end).slice(5, 16))], ['수량', x => f(x.l.qty, 0), 'n'], ['불량', x => f(x.l.bad, 0), 'n'], ['출하 상태', x => `<span class="ost ${x.lv}">${esc(x.t)}</span>`]], o.ls.slice().reverse(), { wrap: false, click: x => 'trace:' + x.l.id, empty: '미착수 — 작업지시 전입니다' })}
      <div class="pacts" style="margin-top:12px">${o.ls.some(x => ['ready', 'hold_rec', 'held'].includes(x.k)) ? '<button class="btn pri" data-go="gate">출하 승인 화면</button>' : ''}${o.remain > 0 ? '<button class="btn" data-go="plan_wo">작업지시</button>' : ''}</div>`);
  }

  // ── 전체 공정 흐름: 원자재 입고 → 프레스 → 점용접 → 열처리 → MCT 가공 → 정밀 검사 → 출하
  const NODES = [
    { k: 'mat', nm: '원자재', full: '원자재 입고', go: 'trace' },
    { k: 'press', nm: '프레스', full: '프레스 성형', go: 'press_home' },
    { k: 'weld', nm: '점용접', full: '점용접', go: 'weld_home', part: true },
    { k: 'heat', nm: '열처리', full: '염욕 소입 열처리', go: 'heat_home' },
    { k: 'mach', nm: 'MCT', full: 'MCT 가공', go: 'mach_home', part: true },
    { k: 'insp', nm: '검사', full: '정밀 검사', go: 'insp_home', part: true },
    { k: 'ship', nm: '출하', full: '출하', go: 'gate' },
  ];
  function procStat(p) {
    const al = M.alerts().filter(x => x.proc === p && x.level !== 'info'), m = M.mods.find(x => x.proc === p);
    const sm = m && m.summary ? (() => { try { return m.summary(M.S); } catch (e) { return null; } })() : null;
    const st = al.some(x => x.level === 'danger') ? 'danger' : al.length || (sm && sm.st === 'warn') ? 'warn' : sm && sm.st === 'danger' ? 'danger' : 'ok';
    return { al, sm, st };
  }
  function nodeData(B) {
    const since = M.sinceDay(M.CFG.RECENT_DAYS || 7).getTime(), L = M.S.lots || [], rt = M.S.masters.routings;
    const out = {};
    NODES.forEach(n => {
      if (n.k === 'mat') { const ms = L.filter(l => l.kind === 'material' && M.toDate(l.last || l.first).getTime() >= since), last = L.filter(l => l.kind === 'material').slice(-1)[0];
        out.mat = { eq: '입고 · 자재 창고', n: ms.length, qty: ms.reduce((s, l) => s + (l.qty || 0), 0), bad: null, last: last ? { id: last.id, t: String(last.last || last.first).slice(5, 10) } : null, st: 'ok', al: [], kpi: `원자재 LOT ${ms.length}개` }; return; }
      if (n.k === 'ship') { const ls = [].concat(...B.orders.map(o => o.ls)), cnt = k => ls.filter(x => x.k === k).length, sq = ls.filter(x => (x.k === 'shipped' || x.k === 'claim') && M.toDate(x.l.end).getTime() >= since).reduce((s, x) => s + x.l.qty, 0);
        const al = M.alerts().filter(x => (x.proc === 'gate' || x.proc === 'deal') && x.level !== 'info');
        out.ship = { eq: '출하장', n: cnt('ready') + cnt('hold_rec') + cnt('held'), qty: sq, bad: null, last: null, st: cnt('claim') || cnt('held') ? 'danger' : cnt('hold_rec') ? 'warn' : 'ok', al, kpi: `출하 대기 ${cnt('ready')} · 보류 권고 ${cnt('hold_rec')} LOT` }; return; }
      const ls = L.filter(l => l.kind === 'process' && l.proc === n.k && M.toDate(l.end_at || l.start_at).getTime() >= since), last = L.filter(l => l.kind === 'process' && l.proc === n.k).slice(-1)[0];
      const qty = ls.reduce((s, l) => s + (l.qty || 0), 0), bad = ls.some(l => l.bad != null) ? ls.reduce((s, l) => s + (l.bad || 0), 0) : null, ps = procStat(n.k);
      out[n.k] = { eq: (rt.find(r => r.proc === n.k) || {}).equipment_id || '', n: ls.length, qty, bad, rate: bad != null && qty ? bad / qty : null, last: last ? { id: last.id, t: String(last.end_at || last.start_at).slice(5, 16) } : null, st: ps.st, al: ps.al, kpi: ps.sm ? ps.sm.kpi : '' };
    });
    return out;
  }
  const rateTxt = r => r == null ? '—' : r >= 0.001 ? pct(r, r >= 0.01 ? 1 : 2) : `${f(r * 1e6, 0)} ppm`;
  V.procmap = () => {
    const B = book(), D = nodeData(B), lm = M.lineApi ? M.lineApi.model() : null;
    const gap = (from) => { const g = lm && lm.gaps[from]; if (!g) return ''; const c = Math.abs(g.d) < Math.max(1, g.dem * 0.02) ? 'ok' : g.d > 0 ? 'up' : 'down';
      return `<span class="pg ${c}" title="${esc(g.label)} — 앞 공정 공급 ${f(g.sup, 0)} · 뒤 공정 소요 ${f(g.dem, 0)} (개/일)">${c === 'ok' ? '균형' : `재공 ${g.d > 0 ? '+' : '−'}${man(Math.abs(g.d))}/일`}</span>`; };
    const GAP_AFTER = { press: 'press', weld: 'weld', heat: 'heat', mach: 'mach' };
    const stT = { ok: '정상', warn: '주의', danger: '이상' };
    // 최근 열처리 LOT 8개의 앞뒤 공정 LOT
    const hl = H().lots.slice(-8).reverse(), lotSt = l => lotState(l, B.ctx);
    const chain = hl.map(h => { const lg = M.lineage(h.id), pick = p => [].concat(lg.up, lg.down).filter(l => l.proc === p || (p === 'mat' && l.kind === 'material'));
      return { h, mat: pick('mat'), press: pick('press'), weld: pick('weld'), mach: pick('mach'), insp: pick('insp'), ship: lotSt(h) }; });
    const lc = xs => xs.length ? `<a href="#" data-trace="${esc(xs[0].id)}"><b>${esc(xs[0].id)}</b></a>${xs.length > 1 ? `<span class="muted"> 외 ${xs.length - 1}</span>` : ''}` : '<span class="muted">—</span>';
    return `<div class="card"><h2>공정 흐름 <span class="h2sub">최근 ${M.CFG.RECENT_DAYS || 7}일 · 공정을 누르면 상세 화면</span><a class="more" href="#" data-go="line">라인 밸런스 →</a></h2>
      <div class="pcard-scroll"><div class="pmap">${NODES.map((n, i) => i ? `<div class="pgl" style="grid-column:${2 * i - 1} / ${2 * i + 2}">${gap(GAP_AFTER[NODES[i - 1].k]) || ''}</div>` : '').join('')}
        ${NODES.map((n, i) => { const d = D[n.k];
        return `${i ? `<div class="pcon" style="grid-column:${2 * i}"></div>` : ''}<div class="pnode ${d.st}" data-go="${n.go}" style="grid-column:${2 * i + 1}" title="${esc(n.full)}${n.part ? ' — 일부 사양만 거치는 공정' : ''}">
          <div class="ph"><span class="dot ${d.st}"></span><b>${esc(n.nm)}</b><span class="pst2 ${d.st}">${stT[d.st]}</span></div>
          <div class="pe">${esc(d.eq)}${n.part ? ' · 일부 사양' : ''}</div>
          <div class="pv">${d.qty ? man(d.qty) : '—'}<small>${n.k === 'ship' ? '개 출하' : '개'}</small></div>
          <dl><dt>${n.k === 'ship' ? '대기' : '작업'}</dt><dd>${n.k === 'ship' ? `${d.n} LOT` : n.k === 'mat' ? `${d.n} LOT` : `${d.n}건`}</dd>
            <dt>불량률</dt><dd>${rateTxt(d.rate)}</dd><dt>알람</dt><dd class="${d.al.length ? d.st : ''}">${d.al.length}건</dd>
            <dt>최근</dt><dd>${d.last ? esc(d.last.t.slice(0, 5)) : '—'}</dd></dl></div>`; }).join('')}</div></div>
      ${M.legend([['l-ok', '정상'], ['l-warn', '주의 (경고 알람)'], ['l-danger', '이상 (긴급 알람)']])}</div>
    <div class="card"><h2>공정별 실적 <span class="h2sub">최근 ${M.CFG.RECENT_DAYS || 7}일</span></h2>
      <div class="tbx"><table class="tb pmtab"><colgroup><col class="c-1"><col class="c-2"><col class="c-3"><col class="c-4"><col class="c-5"><col class="c-6"><col class="c-7"><col class="c-8"><col></colgroup>
        <thead><tr><th>공정</th><th>설비</th><th>상태</th><th class="n">작업</th><th class="n">처리량</th><th class="n">불량</th><th class="n">불량률</th><th class="n">알람</th><th>주요 지표</th></tr></thead>
        <tbody>${NODES.filter(n => n.k !== 'mat' && n.k !== 'ship').map(n => { const d = D[n.k];
          return `<tr class="click" data-row="${n.go}"><td><b>${esc(n.full)}</b></td><td class="muted">${esc(d.eq)}</td><td><span class="pst2 ${d.st}">${stT[d.st]}</span></td><td class="n">${f(d.n, 0)}</td><td class="n">${f(d.qty, 0)}</td>
            <td class="n">${d.bad == null ? '—' : f(d.bad, 0)}</td><td class="n">${rateTxt(d.rate)}</td><td class="n">${d.al.length}</td><td>${esc(d.kpi || '—')}</td></tr>`; }).join('')}</tbody></table></div></div>
    <div class="card"><h2>LOT 흐름 <span class="h2sub">최근 열처리 LOT 기준 앞뒤 공정 연결</span><a class="more" href="#" data-go="trace">LOT 추적 →</a></h2>
      <div class="tbx"><table class="tb lftab"><thead><tr><th>원자재 LOT</th><th>프레스</th><th>점용접</th><th>열처리</th><th>MCT 가공</th><th>정밀 검사</th><th>출하</th></tr></thead>
        <tbody>${chain.map(r => `<tr><td>${lc(r.mat)}</td><td>${lc(r.press)}</td><td>${lc(r.weld)}</td>
          <td><a href="#" data-trace="${esc(r.h.id)}"><b>${esc(r.h.id)}</b></a><div class="muted sm1">${f(r.h.qty, 0)}개 · ${f(r.h.ppm, 0)} PPM</div></td>
          <td>${lc(r.mach)}</td><td>${lc(r.insp)}</td><td><span class="ost ${r.ship.lv}">${esc(r.ship.t)}</span></td></tr>`).join('')}</tbody></table></div></div>`;
  };

  M.flow = { book, stages, STAGE };
  M.mod({
    id: 'flow', group: '대시보드', order: -1,
    menu: [['overview', '종합 현황', 'home'], ['orders', '수주 현황', 'deal'], ['procmap', '전체 공정', 'trace']],
    views: V,
    drawers: { 'ord:': orderDrawer },
    bind(root) {
      root.querySelectorAll('[data-fsel]').forEach(b => b.onclick = e => { e.stopPropagation(); const k = b.dataset.fsel; sel = !k || sel === k ? null : k; M.render(); });
      root.querySelectorAll('[data-of]').forEach(b => b.onclick = () => { of = b.dataset.of; M.render(); });
    },
  });
})();
