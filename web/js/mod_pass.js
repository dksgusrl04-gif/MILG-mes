/* 2.0 연결 모듈 ② — LOT 디지털 여권 · 공정 능력 증명서 · 탄소 발자국
 * ───────────────────────────────────────────────────────────────
 * LOT 디지털 여권: 출하 단위(열처리 LOT)의 공정 이력을 소재 → 프레스 → 점용접 → 열처리 → MCT → 검사 순으로 묶는다.
 *   단계마다 «앞 단계 해시 + 이 단계 기록» 으로 해시를 만들고(사슬), 마지막 해시에 우리 회사 서명을 붙인다.
 *   소재 단계에는 강판 공급사 D 가 서명한 성적서가 들어간다. QR 하나로 받은 쪽이 직접 검증한다.
 * 공정 능력 증명서: 원본 데이터 없이 «최근 30일 Cpk 1.33 이상» 같은 사실만 서명해 건넨다. 근거 데이터는 해시로만 들어간다.
 * 탄소 발자국: LOT별 설비 가동(히터 출력 × 시간 · 타발 수 · 타점 수 · 가공 시간)과 소재량으로 부품 1개당 배출을 추정한다.
 */
(function () {
  'use strict';
  const M = window.MLNG, T = window.MLNGTrust, { esc, f, pct } = M;
  const PC = () => Object.assign({ EF_KWH: 0.4594, STEEL_KG: 0.35, STEEL_EF: 2.3, HEAT_ZONE_KW: { op1: 60, op2: 60, op3: 60, op4: 60, opd1: 20, opd2: 20 },
    PRESS_KWH_PER_STROKE: 0.0022, WELD_KWH_PER_SPOT: 0.0015, SPOTS_PER_PART: 2, MACH_KW: 7.5, INSP_KWH_PER_PART: 0.002 }, M.CFG.PCF || {});
  const H = () => M.S.heat || { lots: [], recipe: [] };
  const lotOf = id => (M.S.lots || []).find(l => l.id === id);
  const heatLot = id => H().lots.find(l => l.id === id);
  const pressLot = id => ((M.S.press || {}).lots || []).find(l => l.id === id);
  const weldLot = id => ((M.S.weld || {}).lots || []).find(l => l.id === id);
  const machTool = id => ((M.S.mach || {}).tools || []).find(t => t.lot === id);
  const inspLot = id => ((M.S.insp || {}).lots || []).find(l => l.id === id);
  const heatDev = l => { let m = 0; (H().recipe || []).forEach(r => { if ((r.sp != null || r.lo != null) && l.stats[r.key]) m = Math.max(m, l.stats[r.key].out || 0); }); return m; };
  const cpkMin = pl => Math.min(...Object.values(pl.stats || {}).map(s => s.cpk).filter(v => v != null));
  const today = () => M.now().toLocaleString('sv-SE').slice(0, 10);
  const addDays = (d, n) => new Date(M.toDate(d).getTime() + n * 86400000).toLocaleString('sv-SE').slice(0, 10);

  // ── 탄소 발자국 (부품 1개당 kgCO2e, 공정별)
  function pcf(hid) {
    const c = PC(), hl = heatLot(hid), L = lotOf(hid); if (!hl) return null;
    const heatKwh = Object.entries(c.HEAT_ZONE_KW).reduce((s, [k, kw]) => s + kw * ((hl.heater || {})[k] || 0) / 100, 0) * (hl.dur_h || 0);
    const up = M.lineage(hid).up, pr = up.find(l => l.proc === 'press'), wd = up.find(l => l.proc === 'weld');
    const p = pr && pressLot(pr.id), cav = (M.S.press || {}).cav || 1;
    const down = M.lineage(hid).down, mc = down.find(l => l.proc === 'mach'), t = mc && machTool(mc.id);
    const part = {
      소재: c.STEEL_KG * c.STEEL_EF,
      프레스: p ? c.PRESS_KWH_PER_STROKE / cav * c.EF_KWH : 0,
      점용접: wd ? c.WELD_KWH_PER_SPOT * c.SPOTS_PER_PART * c.EF_KWH : 0,
      열처리: hl.qty ? heatKwh / hl.qty * c.EF_KWH : 0,
      'MCT 가공': t ? c.MACH_KW * (t.minutes / Math.max(1, t.runs)) / 60 / ((M.S.mach || {}).parts_per_run || 25) * c.EF_KWH : 0,
      검사: down.some(l => l.proc === 'insp') ? c.INSP_KWH_PER_PART * c.EF_KWH : 0,
    };
    const total = Object.values(part).reduce((s, v) => s + v, 0);
    return { lot: hid, qty: (L || hl).qty, part, total, heatKwh };
  }
  T.pcf = pcf;

  // ── 소재 성적서 (강판 공급사 D 서명)
  const certs = T.millCerts = {};
  async function millCert(mid) {
    if (certs[mid]) return certs[mid];
    const m = lotOf(mid) || { id: mid }, n = parseInt(String(mid).replace(/\D/g, '').slice(-3), 10) || 1;
    const c = { id: 'MC-CERT-' + mid, coil: mid, grade: 'SPFC590 (냉연 고장력)', C: +(0.08 + (n % 5) * 0.004).toFixed(3), Mn: +(1.5 + (n % 7) * 0.02).toFixed(2), TS_MPa: 605 + (n % 9) * 4, EL_pct: 21 + (n % 4), issued: m.first || today(), issuer: 'MAT-D' };
    c.sig = await T.sign('MAT-D', c); certs[mid] = c; return c;
  }

  // ── LOT 디지털 여권
  const PP = T.passports = {};
  function stepsOf(hid) {
    const g = M.lineage(hid), hl = heatLot(hid), L = lotOf(hid), steps = [];
    const up = g.up.slice().reverse();   // 소재 → 프레스 → 점용접
    up.forEach(l => {
      if (l.kind === 'material') steps.push({ proc: 'material', name: '소재 입고', lot: l.id, at: l.first || '', company: 'MAT-D', data: { qty: l.qty, item: l.item_code } });
      else if (l.proc === 'press') { const p = pressLot(l.id); steps.push({ proc: 'press', name: '프레스 성형', lot: l.id, at: l.start_at, company: 'MLNG', equip: l.equipment_id, data: p ? { strokes: p.strokes, qty: p.qty, bad: p.bad, ppm: Math.round(p.ppm), cpk_min: +cpkMin(p).toFixed(2) } : { qty: l.qty } }); }
      else if (l.proc === 'weld') { const w = weldLot(l.id); steps.push({ proc: 'weld', name: '점용접', lot: l.id, at: l.start_at, company: 'MLNG', equip: l.equipment_id, data: w ? { welds: w.welds, expected_defects: w.est, priority_checks: w.pred } : { qty: l.qty } }); }
    });
    if (hl) steps.push({ proc: 'heat', name: '염욕 열처리', lot: hid, at: hl.start, company: 'MLNG', equip: hl.equip, data: { qty: hl.qty, bad: hl.bad, ppm: Math.round(hl.ppm), dur_h: hl.dur_h, deviation_max: +heatDev(hl).toFixed(4), material_lot: hl.mat_lot } });
    g.down.forEach(l => {
      if (l.proc === 'mach') { const t = machTool(l.id); steps.push({ proc: 'mach', name: 'MCT 가공', lot: l.id, at: l.start_at, company: 'MLNG', equip: l.equipment_id, data: t ? { tool: t.id, material: t.material, runs: t.runs, vb_mm: t.vb_last } : { qty: l.qty } }); }
      if (l.proc === 'insp') { const i = inspLot(l.id); steps.push({ proc: 'insp', name: '정밀가공 검사', lot: l.id, at: l.start_at, company: 'MLNG', equip: l.equipment_id, data: i ? { parts: i.parts, fail: i.fail, conflict: i.conflict } : { qty: l.qty } }); }
    });
    return { steps, qty: (L || hl || {}).qty };
  }
  async function chain(steps) { let prev = '0'.repeat(64); for (const s of steps) { s.prev = prev; s.hash = await T.sha256(T.canon({ proc: s.proc, lot: s.lot, at: s.at, company: s.company, equip: s.equip || null, data: s.data, cert: s.cert ? s.cert.sig.hash : null, prev })); prev = s.hash; } return prev; }
  const ppBody = p => ({ id: p.id, lot: p.lot, product: p.product, qty: p.qty, root: p.root, pcf_kg_per_part: p.pcf ? +p.pcf.total.toFixed(4) : null, issued: p.issued });
  async function issue(hid) {
    if (PP[hid]) return PP[hid];
    const { steps, qty } = stepsOf(hid);
    for (const s of steps) if (s.proc === 'material') s.cert = await millCert(s.lot);
    const item = M.S.masters.items[0] || {}, p = { id: 'PP-' + hid, lot: hid, product: `${item.code} ${item.name}`, qty, steps, pcf: pcf(hid), issued: T.stamp(), issuer: 'MLNG' };
    p.root = await chain(steps); p.sig = await T.sign('MLNG', ppBody(p));
    PP[hid] = p;
    await T.append('MLNG', 'passport', p.id, `LOT 디지털 여권 발행 — ${hid} (${steps.length}단계)`, ppBody(p));
    return p;
  }
  T.issuePassport = issue;
  // 3.0 출하 승인: 원장에 남기지 않고 여권 내용만 미리 계산
  T.passportPreview = hid => M.memo('pp', hid, () => { const { steps, qty } = stepsOf(hid); return { id: 'PP-' + hid, lot: hid, steps, qty, pcf: pcf(hid) }; });
  M.heatDev = heatDev;
  // 검증: 사슬 다시 계산 → 루트 비교 → 회사 서명 → 소재 성적서 서명
  async function verify(p) {
    const res = [], copy = JSON.parse(JSON.stringify(p.steps));
    let prev = '0'.repeat(64), broke = null;
    for (let i = 0; i < copy.length; i++) {
      const s = copy[i], h = await T.sha256(T.canon({ proc: s.proc, lot: s.lot, at: s.at, company: s.company, equip: s.equip || null, data: s.data, cert: s.cert ? s.cert.sig.hash : null, prev }));
      const ok = h === p.steps[i].hash && !broke; if (!ok && !broke) broke = i; res.push({ i, ok, name: s.name, lot: s.lot }); prev = h;
    }
    const rootOk = prev === p.root, sig = await T.verify('MLNG', ppBody(p), p.sig), certs = [];
    for (const s of p.steps.filter(x => x.cert)) { const c = Object.assign({}, s.cert); delete c.sig; const v = await T.verify('MAT-D', c, s.cert.sig); certs.push({ lot: s.lot, ok: v.ok, why: v.why }); }
    return { ok: rootOk && sig.ok && certs.every(c => c.ok), steps: res, rootOk, sig, certs, broke };
  }
  T.verifyPassport = verify;

  // ── 화면
  let cur = null, vres = null, tamp = false, viewAs = 'MLNG', vmsg = '';
  const shipLots = () => H().lots.slice(-30).reverse();
  const QRC = {};   // 같은 링크의 QR 은 한 번만 만든다
  const qrSvg = text => QRC[text] || (QRC[text] = qrMake(text));
  const qrMake = text => { try { const q = window.qrcode(0, 'M'); q.addData(text); q.make(); return q.createSvgTag({ cellSize: 3, margin: 2, scalable: true }); } catch (e) { return '<div class="muted">QR 생성 불가</div>'; } };
  const verifyUrl = p => `${location.origin}${location.pathname}#verify=${p.id}:${p.root.slice(0, 16)}`;
  const stepVal = s => Object.entries(s.data).map(([k, v]) => `${({ qty: '수량', bad: '불량', ppm: 'PPM', strokes: '타발', cpk_min: 'Cpk(최소)', welds: '타점', expected_defects: '예상 불량', priority_checks: '1순위 검사', dur_h: '시간(h)', deviation_max: '조건 이탈률', material_lot: '소재 LOT', tool: '공구', material: '재질', runs: '가공', vb_mm: '마모 VB', parts: '검사 부품', fail: '불합격', conflict: '확정 필요', item: '품목' })[k] || k} ${typeof v === 'number' ? f(v, Number.isInteger(v) ? 0 : 2) : esc(v)}`).join(' · ');

  const V = {};
  V.pass_home = () => {
    if (!T.ready) return '<div class="empty">신뢰 계층을 준비하는 중…</div>';
    const lots = shipLots(); if (!cur) cur = lots[0] && lots[0].id;
    const p = PP[cur], n = Object.keys(PP).length;
    return `<div class="kstrip">
      <div class="kpi"><div class="lab">발행한 여권</div><div class="val">${n}<small>건</small></div><div class="foot">출하 LOT 단위 · 우리 회사 서명</div></div>
      <div class="kpi click" data-row="ctr:${esc((T.contracts.find(c => c.provider === 'MLNG' && c.scopes.includes('lot_passport') && T.contractState(c) === 'active') || {}).id || '')}"><div class="lab">여권 공유 계약</div><div class="val">${T.contracts.filter(c => c.provider === 'MLNG' && c.scopes.includes('lot_passport') && T.contractState(c) === 'active').length}<small>곳</small></div><div class="foot">계약된 고객사만 조회 가능</div></div>
      <div class="kpi"><div class="lab">이 여권 검증</div><div class="val" style="color:${vres ? (vres.ok ? 'var(--ok)' : 'var(--danger-ink)') : 'inherit'}">${vres ? (vres.ok ? '진본' : '위변조') : '—'}</div><div class="foot">${vres ? `사슬 ${vres.steps.filter(s => s.ok).length}/${vres.steps.length} · 서명 ${vres.sig.ok ? '일치' : '불일치'}` : '「검증」을 누르세요'}</div></div>
      <div class="kpi"><div class="lab">부품 1개당 탄소${M.asmTag ? M.asmTag('pcf') : ''}</div><div class="val">${p && p.pcf ? f(p.pcf.total * 1000, 0) : '—'}<small>gCO₂e</small></div><div class="foot">소재 포함 · 추정값</div></div>
    </div>
    <div class="card"><div class="tools"><select class="in" id="pp-lot" style="min-width:300px">${lots.map(l => `<option value="${esc(l.id)}" ${l.id === cur ? 'selected' : ''}>${esc(l.id)} · ${esc(l.date)} · ${f(l.qty, 0)}개${PP[l.id] ? ' · 발행됨' : ''}</option>`).join('')}</select>
      ${p ? `<button class="btn pri sm" id="pp-verify">검증</button>${tamp ? '<button class="btn sm" id="pp-untamp">원래대로</button>' : '<button class="btn sm" id="pp-tamp">위변조 시험</button>'}
        <span class="tsum">보는 회사 <select class="in" id="pp-as" style="padding:3px 6px">${T.COMPANIES.map(c => `<option value="${c.id}" ${c.id === viewAs ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></span>`
        : `<button class="btn pri sm" id="pp-issue">여권 발행 (서명)</button>`}</div>
      ${vmsg ? `<div class="note" style="color:var(--danger-ink)">${esc(vmsg)}</div>` : ''}
      ${p ? passHtml(p) : '<div class="empty">아직 발행하지 않은 LOT 입니다 — 「여권 발행」을 누르면 공정 이력을 해시 사슬로 묶고 서명합니다</div>'}</div>`;
  };
  function passHtml(p) {
    const vs = vres && vres.steps;
    return `<div class="pp">
      <div class="pph"><div><div class="ppt">LOT 디지털 여권</div><div class="ppn">${esc(p.id)}</div><div class="muted">${esc(p.product)} · ${f(p.qty, 0)}개 · 발행 ${esc(p.issued)}</div>
        ${M.dl([['발행', 'MILG 데모 공장 · 키 ' + esc(p.sig.kid)], ['사슬 끝 해시', `<span class="mono">${esc(T.short(p.root))}</span>`], ['서명', `<span class="mono">${esc(p.sig.alg)} · ${esc(p.sig.sig.slice(0, 18))}…</span>`]])}</div>
        <div class="qr" title="${esc(verifyUrl(p))}">${qrSvg(verifyUrl(p))}<div class="muted" style="font-size:11px;text-align:center">스캔하면 검증 화면</div></div></div>
      <div class="ppsteps">${p.steps.map((s, i) => `<div class="pps ${vs ? (vs[i].ok ? 'ok' : 'bad') : ''}">
        <div class="ppi">${vs ? (vs[i].ok ? '✓' : '✕') : i + 1}</div>
        <div class="ppb"><div class="ppnm">${esc(s.name)} <span class="muted">· ${esc(s.lot)}${s.equip ? ' · ' + esc(s.equip) : ''} · ${esc(String(s.at || '').slice(0, 16))}</span> <span class="cotag" style="--cc:${T.co(s.company).color}"><b>${esc(T.co(s.company).short)}</b>${esc(T.co(s.company).name)}</span></div>
          <div class="ppd">${stepVal(s)}${s.cert ? ` · 성적서 ${esc(s.cert.grade)} · TS ${s.cert.TS_MPa} MPa · <b>D사 서명</b>` : ''}</div>
          <div class="pph2 mono">해시 ${esc(T.short(s.hash))} ← 앞 ${esc(T.short(s.prev))}</div></div></div>`).join('')}</div>
      ${vres ? `<div class="note" style="color:${vres.ok ? 'var(--ok)' : 'var(--danger-ink)'}">${vres.ok ? '진본입니다 — 공정 사슬, MILG 서명, 소재 성적서(D사) 서명이 모두 맞습니다.'
        : `위변조 탐지 — ${vres.broke != null ? `${vres.broke + 1}단계(${esc(p.steps[vres.broke].name)})부터 사슬이 맞지 않습니다. ` : ''}${vres.sig.ok ? '' : '서명: ' + esc(vres.sig.why)}`}</div>` : ''}
      ${p.pcf ? `<h3 class="dh">탄소 발자국 (부품 1개당)</h3><div class="hbars">${Object.entries(p.pcf.part).filter(([, v]) => v > 0).map(([k, v]) => `<div class="hb"><span>${esc(k)}</span><i style="--w:${Math.max(2, v / p.pcf.total * 100)}%"></i><b>${f(v * 1000, 1)} g</b></div>`).join('')}</div>` : ''}
    </div>`;
  }
  // 상대 회사 시점: 계약이 있어야 열린다
  async function viewAsCo(cid) {
    viewAs = cid; vmsg = '';
    if (cid !== 'MLNG') { const r = await T.access(cid, 'MLNG', 'lot_passport', cur); if (!r.ok) vmsg = `${T.co(cid).name}: 접근 거부 — ${r.why}. (데이터 공유 계약에서 «LOT 디지털 여권» 범위를 계약해야 볼 수 있습니다)`; }
    M.render();
  }

  // ── QR 검증 링크 (#verify=PP-...:해시16)
  async function handleHash() {
    const m = location.hash.match(/^#verify=PP-([^:]+):([0-9a-f]{16})$/); if (!m) return;
    const hid = m[1], p = await issue(hid), v = await verify(p), match = p.root.startsWith(m[2]);
    M.kdrawer('QR 검증', `LOT 디지털 여권 ${p.id}`, [
      { html: `<div class="vbig ${v.ok && match ? 'ok' : 'bad'}">${v.ok && match ? '✓ 진본' : '✕ 확인 실패'}</div>` },
      { html: M.dl([['제품', esc(p.product)], ['LOT', esc(p.lot)], ['수량', f(p.qty, 0) + '개'], ['QR 해시', `<span class="mono">${esc(m[2])}…</span> ${match ? '일치' : '<b style="color:var(--danger-ink)">불일치</b>'}`], ['공정 사슬', `${v.steps.filter(s => s.ok).length}/${v.steps.length} 단계 일치`], ['MILG 서명', v.sig.ok ? '일치' : esc(v.sig.why)], ['소재 성적서', v.certs.map(c => `${esc(c.lot)} ${c.ok ? '✓' : '✕'}`).join(' · ') || '—']]) },
      { html: `<button class="btn" data-go="pass_home">여권 전체 보기</button>` }]);
    cur = hid; history.replaceState(null, '', location.pathname);
  }

  // ── 공정 능력 증명서
  const CR = T.credentials = [];
  const RD30 = () => M.sinceDay(30).getTime();
  const TPL = {
    press_cpk: { name: '프레스 공정능력', text: '최근 30일 프레스 공정능력 Cpk ≥ 1.33', calc: () => { const ls = ((M.S.press || {}).lots || []).filter(l => M.toDate(l.date).getTime() >= RD30()); const v = ls.length ? ls.reduce((s, l) => s + cpkMin(l), 0) / ls.length : null; return { v, ok: v != null && v >= 1.33, raw: ls.map(l => [l.id, cpkMin(l)]), show: v == null ? '—' : f(v, 2) }; } },
    heat_ppm: { name: '열처리 불량률', text: '최근 30일 열처리 불량률 ≤ 450 PPM', calc: () => { const ls = H().lots.filter(l => M.toDate(l.end).getTime() >= RD30()); const q = ls.reduce((s, l) => s + l.qty, 0), b = ls.reduce((s, l) => s + l.bad, 0), v = q ? b / q * 1e6 : null; return { v, ok: v != null && v <= 450, raw: ls.map(l => [l.id, l.qty, l.bad]), show: v == null ? '—' : f(v, 0) + ' PPM' }; } },
    heat_dev: { name: '열처리 조건 준수', text: '최근 30일 열처리 조건 이탈 작업 ≤ 5%', calc: () => { const ls = H().lots.filter(l => M.toDate(l.end).getTime() >= RD30()); const n = ls.filter(l => heatDev(l) >= ((M.CFG.HEAT || {}).DEVIATION_RATE || 0.05)).length, v = ls.length ? n / ls.length : null; return { v, ok: v != null && v <= 0.05, raw: ls.map(l => [l.id, heatDev(l)]), show: v == null ? '—' : pct(v, 1) }; } },
    press_vib: { name: '프레스 설비 안정', text: '최근 7일 프레스 진동 이상 0건', calc: () => { const ev = ((M.S.press || {}).vib || { events: [] }).events.filter(e => M.toDate(e.end).getTime() >= M.sinceDay(7).getTime()); return { v: ev.length, ok: ev.length === 0, raw: ev.map(e => [e.start, e.ratio]), show: ev.length + '건' }; } },
    insp_fail: { name: '정밀가공 검사 합격', text: '최근 검사 불합격률 ≤ 10%', calc: () => { const ls = (M.S.insp || {}).lots || []; const p = ls.reduce((s, l) => s + l.parts, 0), fl = ls.reduce((s, l) => s + l.fail, 0), v = p ? fl / p : null; return { v, ok: v != null && v <= 0.10, raw: ls.map(l => [l.id, l.parts, l.fail]), show: v == null ? '—' : pct(v, 2) }; } },
    press_ppm: { name: '프레스 불량률', text: '최근 30일 프레스 불량률 ≤ 150 PPM', calc: () => { const ls = ((M.S.press || {}).lots || []).filter(l => M.toDate(l.date).getTime() >= RD30()); const q = ls.reduce((s, l) => s + l.qty, 0), b = ls.reduce((s, l) => s + l.bad, 0), v = q ? b / q * 1e6 : null; return { v, ok: v != null && v <= 150, raw: ls.map(l => [l.id, l.qty, l.bad]), show: v == null ? '—' : f(v, 0) + ' PPM' }; } },
    heat_temp: { name: '소입 온도 관리', text: '최근 30일 소입로 평균 온도가 설정 ±5°C 안 (작업 95% 이상)', calc: () => { const R = (H().recipe || []).filter(r => /^hz/.test(r.key) && r.sp != null); const ls = H().lots.filter(l => M.toDate(l.end).getTime() >= RD30());
      const okN = ls.filter(l => R.every(r => !l.stats[r.key] || Math.abs(l.stats[r.key].mean - r.sp) <= 5)).length, v = ls.length ? okN / ls.length : null; return { v, ok: v != null && v >= 0.95, raw: ls.map(l => [l.id, R.map(r => (l.stats[r.key] || {}).mean)]), show: v == null ? '—' : pct(v, 1) }; } },
  };
  const crBody = c => ({ id: c.id, issuer: c.issuer, audience: c.audience, claim: c.claim, result: c.result, evidence: c.evidence, issued: c.issued, expires: c.expires });
  async function issueCred(key, audience) {
    const t = TPL[key], r = t.calc(); if (!r.ok) return { ok: false, r };
    const c = { id: `VC-${String(CR.length + 1).padStart(3, '0')}`, key, issuer: 'MLNG', audience, claim: t.text, result: true, evidence: await T.sha256(T.canon(r.raw)), issued: today(), expires: addDays(today(), 30) };
    c.sig = await T.sign('MLNG', crBody(c)); CR.unshift(c);
    await T.append('MLNG', 'credential', c.id, `공정 능력 증명서 발행 — ${t.name} → ${T.co(audience).name}`, crBody(c));
    return { ok: true, c };
  }
  T.onInit(async () => { if (CR.length) return; await issueCred('heat_ppm', 'T1-B'); await issueCred('press_ppm', 'OEM-A'); await issueCred('heat_temp', 'OEM-A'); await issue((H().lots[H().lots.length - 1] || {}).id); });

  let crmsg = '', crv = {};
  V.pass_cred = () => {
    if (!T.ready) return '<div class="empty">신뢰 계층을 준비하는 중…</div>';
    const rows = Object.entries(TPL).map(([k, t]) => Object.assign({ k, t }, t.calc()));
    return `<div class="kstrip">
      <div class="kpi"><div class="lab">발행한 증명서</div><div class="val">${CR.length}<small>건</small></div><div class="foot">유효 ${CR.filter(c => c.expires >= today()).length}건</div></div>
      <div class="kpi"><div class="lab">지금 발행 가능</div><div class="val">${rows.filter(r => r.ok).length}<small>/ ${rows.length}</small></div><div class="foot">기준을 만족하는 항목만 발행</div></div>
      <div class="kpi"><div class="lab">원본 공유</div><div class="val" style="color:var(--ok)">0<small>건</small></div><div class="foot">근거는 해시로만 포함</div></div>
      <div class="kpi"><div class="lab">유효 기간</div><div class="val">30<small>일</small></div><div class="foot">만료되면 다시 발행</div></div>
    </div>
    <div class="card"><h2>발행할 수 있는 증명 <span class="h2sub">원본 데이터는 넘기지 않고, «기준을 만족한다» 는 사실만 서명해 건넵니다</span></h2>
      ${M.table([['항목', r => `<b>${esc(r.t.name)}</b><div class="muted" style="font-size:12px">${esc(r.t.text)}</div>`], ['현재 값 (우리만 봄)', r => r.show, 'n'], ['기준', r => r.ok ? '<span class="tag j-ok">만족</span>' : '<span class="tag j-danger">미달</span>'],
        ['발행', r => r.ok ? `<select class="in" data-craud="${r.k}" style="padding:3px 6px">${T.COMPANIES.filter(c => c.id !== 'MLNG').map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select> <button class="btn pri sm" data-crissue="${r.k}">발행</button>` : '<span class="muted">기준 미달 — 발행 불가</span>']], rows, { wrap: false })}
      ${crmsg ? `<p class="note msg">${esc(crmsg)}</p>` : ''}</div>
    <div class="card" style="margin-top:16px"><h2>발행한 증명서 <span class="h2sub">받는 회사가 「검증」으로 서명·기간을 직접 확인합니다</span></h2>
      ${M.table([['증명서', c => `<b>${esc(c.id)}</b>`], ['주장', 'claim'], ['받는 회사', c => esc(T.co(c.audience).name)], ['기간', c => `<span class="muted">${esc(c.issued)} ~ ${esc(c.expires)}</span>`], ['근거 해시', c => `<span class="mono">${esc(T.short(c.evidence))}</span>`],
        ['검증', c => crv[c.id] ? (crv[c.id].ok ? '<span class="tag j-ok">✓ 진본</span>' : `<span class="tag j-danger">✕ ${esc(crv[c.id].why)}</span>`) : `<button class="btn sm" data-crver="${esc(c.id)}">${esc(T.co(c.audience).short)}사로서 검증</button>`]], CR, { wrap: false, empty: '발행한 증명서가 없습니다' })}
      <p class="note">PPAP·신규 협력사 등록 때 측정 원본 대신 이 증명서를 내면, 받는 쪽은 진위를 확인하고 보내는 쪽은 공정 노하우를 지킵니다.</p></div>`;
  };

  // ── 탄소 발자국
  V.pass_pcf = () => {
    const ls = H().lots.slice(-20).reverse(), rows = ls.map(l => pcf(l.id)).filter(Boolean), c = PC();
    const avg = rows.length ? rows.reduce((s, r) => s + r.total, 0) / rows.length : 0, keys = Object.keys((rows[0] || { part: {} }).part);
    const pAvg = k => rows.reduce((s, r) => s + r.part[k], 0) / Math.max(1, rows.length);
    return `<div class="kstrip">
      <div class="kpi"><div class="lab">부품 1개당 (평균)${M.asmTag ? M.asmTag('pcf') : ''}</div><div class="val">${f(avg * 1000, 0)}<small>gCO₂e</small></div><div class="foot">최근 ${rows.length}개 LOT · 소재 포함</div></div>
      <div class="kpi"><div class="lab">공정 배출만</div><div class="val">${f((avg - pAvg('소재')) * 1000, 1)}<small>g</small></div><div class="foot">우리 공장 에너지분</div></div>
      <div class="kpi"><div class="lab">가장 큰 공정</div><div class="val">${esc(keys.filter(k => k !== '소재').sort((a, b) => pAvg(b) - pAvg(a))[0] || '—')}</div><div class="foot">감축 우선 대상</div></div>
      <div class="kpi click" data-row="ctr:${esc((T.contracts.find(x => x.scopes.includes('pcf') && x.consumer === 'OEM-A') || {}).id || '')}"><div class="lab">완성차 보고</div><div class="val">${T.contracts.some(x => x.scopes.includes('pcf') && x.consumer === 'OEM-A' && T.contractState(x) === 'active') ? '연결' : '서명 대기'}</div><div class="foot">디지털 제품 여권 · 탄소 보고</div></div>
    </div>
    <div class="card"><h2>공정별 배출 (부품 1개당 평균)</h2>
      <div class="hbars">${keys.map(k => `<div class="hb"><span>${esc(k)}</span><i style="--w:${Math.max(2, pAvg(k) / Math.max(1e-9, avg) * 100)}%"></i><b>${f(pAvg(k) * 1000, 1)} g</b></div>`).join('')}</div></div>
    <div class="card" style="margin-top:16px"><h2>LOT별 <span class="h2sub">행을 누르면 그 LOT의 디지털 여권</span></h2>
      ${M.table([['LOT', r => `<b>${esc(r.lot)}</b>`], ['수량', r => f(r.qty, 0), 'n'], ['열처리 전력', r => f(r.heatKwh, 0) + ' kWh', 'n']].concat(keys.map(k => [k, r => f(r.part[k] * 1000, 1), 'n'])).concat([['합계 g/개', r => `<b>${f(r.total * 1000, 1)}</b>`, 'n']]), rows, { wrap: false, click: r => 'pp:' + r.lot })}
      <p class="note">추정 방식 — 열처리: 존별 정격 kW × 히터 출력 × 작업 시간 ÷ 수량, 프레스: 타발당 kWh ÷ 취수, 점용접: 타점당 kWh × 타점 수, MCT: 주축 kW × 가공 시간 ÷ 부품 수. 전력 배출계수 ${c.EF_KWH} kgCO₂e/kWh · 강판 ${c.STEEL_KG} kg × ${c.STEEL_EF} kgCO₂e/kg 는 예시값이며 config.js 의 PCF 에서 최신 고시값·공급사 제공값으로 바꿉니다.</p></div>`;
  };

  M.mod({
    id: 'pass', group: '연결 · 신뢰 (2.0)', order: 51,
    menu: [['pass_home', 'LOT 디지털 여권', 'passport'], ['pass_cred', '공정 능력 증명서', 'badge'], ['pass_pcf', '탄소 발자국', 'leaf']],
    views: V,
    drawers: { 'pp:': async id => { cur = id; vres = null; tamp = false; await issue(id); M.go('pass_home'); } },
    bind(root) {
      const on = (s, fn) => { const e = root.querySelector(s); if (e) e.onclick = fn; };
      const sl = root.querySelector('#pp-lot'); if (sl) sl.onchange = () => { cur = sl.value; vres = null; tamp = false; vmsg = ''; M.render(); };
      on('#pp-issue', async () => { await issue(cur); M.render(); });
      on('#pp-verify', async () => { vres = await verify(PP[cur]); M.render(); });
      on('#pp-tamp', async () => { const p = PP[cur], s = p.steps.find(x => x.data && x.data.bad != null) || p.steps[0]; s._orig = JSON.stringify(s.data); if (s.data.bad != null) s.data.bad = Math.max(0, s.data.bad - 1); else s.data.qty = (s.data.qty || 0) + 1; tamp = true; vres = await verify(p); M.render(); });
      on('#pp-untamp', async () => { const p = PP[cur]; p.steps.forEach(s => { if (s._orig) { s.data = JSON.parse(s._orig); delete s._orig; } }); tamp = false; vres = await verify(p); M.render(); });
      const as = root.querySelector('#pp-as'); if (as) as.onchange = () => viewAsCo(as.value);
      root.querySelectorAll('[data-crissue]').forEach(b => b.onclick = async () => { const k = b.dataset.crissue, aud = root.querySelector(`[data-craud="${k}"]`).value, r = await issueCred(k, aud); crmsg = r.ok ? `${r.c.id} 발행 — ${TPL[k].text} → ${T.co(aud).name}` : '기준 미달로 발행할 수 없습니다'; M.render(); });
      root.querySelectorAll('[data-crver]').forEach(b => b.onclick = async () => { const c = CR.find(x => x.id === b.dataset.crver), v = await T.verify(c.issuer, crBody(c), c.sig); crv[c.id] = c.expires < today() ? { ok: false, why: '기간 만료' } : v; M.render(); });
    },
  });
  // QR 검증 링크로 들어온 경우
  T.onInit(async () => { setTimeout(() => handleHash().catch(e => console.warn(e)), 50); });
  window.addEventListener('hashchange', () => { if (T.ready) handleHash(); });
})();
