/* 5.5 — 생산 일보 · 현장 모니터(안돈)
 * ───────────────────────────────────────────────────────────────
 * 생산 일보: 국내 제조 현장의 일보 양식 그대로 — 결재란(작성 · 검토 · 승인), 공정별 계획 대비 실적, 불량 내역, 비가동 내역, 수주 진행, 특이사항
 *   엑셀(.xlsx) 다운로드 · 인쇄(A4 가로)
 * 현장 모니터: 공장 TV 용 전체 화면 — 설비별 상태(가동 · 정지 · 대기 · 이상), 현재 시각까지 목표 대비 실적, OEE, A/B 알람 흐름. 30초마다 새로 그림
 */
(function () {
  'use strict';
  const M = window.MLNG, { esc, f, pct } = M, XL = () => window.MLNG_XLSX;
  const PROCS = [['press', '프레스 성형', 'PR-01'], ['weld', '점용접', 'SP-01'], ['heat', '열처리', 'SB-01'], ['mach', 'MCT 가공', 'MC-01'], ['insp', '정밀 검사', 'IN-01']];
  const pad = n => String(n).padStart(2, '0');
  const ymd = t => { const d = new Date(t); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  const dayOf = l => String(l.end_at || l.start_at || '').slice(0, 10);
  const prodDays = () => [...new Set((M.S.lots || []).filter(l => l.kind === 'process').map(dayOf))].filter(Boolean).sort();
  const cname = c => ((M.S.masters.defect_codes || []).find(x => x.code === c) || {}).name || c;

  function report(d) {
    const lm = M.lineApi ? M.lineApi.model() : null, L = (M.S.lots || []).filter(l => l.kind === 'process' && dayOf(l) === d);
    const oe = M.oeeApi ? M.oeeApi.days().filter(r => r.d === d) : [];
    const prod = PROCS.map(([p, nm, eq]) => { const ls = L.filter(l => l.proc === p), qty = ls.reduce((s, l) => s + (l.qty || 0), 0), badK = ls.some(l => l.bad != null), bad = ls.reduce((s, l) => s + (l.bad || 0), 0), o = oe.find(r => r.eq === eq);
      const plan = lm ? Math.round(lm.plan[p] || 0) : 0;
      return { p, nm, eq, plan, qty, good: qty - bad, bad: badK ? bad : null, rate: badK && qty ? bad / qty : null, ach: plan ? qty / plan : null, run: o ? o.run : 0, stop: o ? o.planned - o.run : 0, oee: o ? o.oee : null, n: ls.length }; });
    const defects = [];
    (M.S.heat || { lots: [] }).lots.filter(l => String(l.end).slice(0, 10) === d).forEach(l => Object.entries(l.defects || {}).forEach(([c, n]) => defects.push({ p: '열처리', lot: l.id, c, n: cname(c), q: n })));
    (M.S.press || { lots: [] }).lots.filter(l => l.date === d).forEach(l => Object.entries(l.defects || {}).forEach(([c, n]) => defects.push({ p: '프레스', lot: l.id, c, n: cname(c), q: n })));
    M.inspections().filter(i => String(i.at).slice(0, 10) === d && i.result === '불량').forEach(i => defects.push({ p: M.PROC[i.proc] || i.proc, lot: i.lot_id, c: i.defect_code || '', n: cname(i.defect_code || '') || i.note || '', q: i.qty || 1 }));
    const by = {}; defects.forEach(x => { const k = x.p + '|' + x.n; (by[k] = by[k] || { p: x.p, n: x.n, q: 0, lots: new Set() }).q += x.q; by[k].lots.add(x.lot); });
    const dsum = Object.values(by).sort((a, b) => b.q - a.q).map(x => Object.assign(x, { lots: [...x.lots].join(', ') }));
    const dts = [].concat(...oe.map(r => r.ev.map(e => Object.assign({ nm: r.nm }, e)))).sort((a, b) => a.at - b.at);
    const orders = M.flow ? M.flow.book().orders.filter(o => o.stage !== 'done') : [];
    const notes = M.alerts().filter(a => a.grade !== 'C' && String(a.at || '').slice(0, 10) === d);
    return { d, prod, dsum, dts, orders, notes };
  }
  const hm = m => `${pad(Math.floor(m / 60) % 24)}:${pad(Math.round(m % 60))}`;
  let cur = null;
  const V = {};
  V.daily = () => {
    const ds = prodDays(); if (!cur || !ds.includes(cur)) cur = ds[ds.length - 1];
    const i = ds.indexOf(cur), R = report(cur), F = M.S.factory, tot = R.prod.find(x => x.p === 'heat') || {};
    const st = x => !x.n || x.ach == null ? '' : x.ach >= ((M.CFG.SIGNAL || {}).PLAN_GREEN || 0.95) ? 'ok' : x.ach >= ((M.CFG.SIGNAL || {}).PLAN_YELLOW || 0.85) ? 'warn' : 'danger';
    return `<div class="tools rtools"><div class="rnav"><button class="btn" data-rday="${i - 1}" ${i <= 0 ? 'disabled' : ''}>◀ 이전</button><b>${esc(cur)}</b><button class="btn" data-rday="${i + 1}" ${i >= ds.length - 1 ? 'disabled' : ''}>다음 ▶</button></div>
      <span class="sp"></span><button class="btn" id="rp-print">인쇄</button><button class="btn pri" id="rp-xlsx">엑셀 다운로드</button></div>
    <div class="rpt" id="rpt">
      <div class="rhead"><div><h1>생산 일보</h1><table class="rmeta"><tr><th>일자</th><td>${esc(cur)} (${'일월화수목금토'[new Date(cur).getDay()]})</td><th>공장 · 라인</th><td>${esc(F.name)} · ${esc(F.line || '')}</td></tr>
        <tr><th>품목</th><td>${esc((M.S.masters.items[0] || {}).code || '')} ${esc((M.S.masters.items[0] || {}).name || '')}</td><th>작성</th><td>${esc(M.D.user().name)} · ${esc(new Date().toLocaleString('sv-SE').slice(0, 16))}</td></tr></table></div>
        <table class="rsign"><tr><th rowspan="2">결<br>재</th><th>작성</th><th>검토</th><th>승인</th></tr><tr><td></td><td></td><td></td></tr></table></div>
      <h2>1. 공정별 생산 실적</h2>
      <table class="rt"><thead><tr><th>공정</th><th>설비</th><th>계획</th><th>실적</th><th>달성률</th><th>양품</th><th>불량</th><th>불량률</th><th>가동</th><th>비가동</th><th>OEE</th></tr></thead>
        <tbody>${R.prod.map(x => `<tr><td class="l">${esc(x.nm)}</td><td>${x.eq}</td><td class="n">${f(x.plan, 0)}</td><td class="n"><b>${x.n ? f(x.qty, 0) : '—'}</b></td><td class="n ${st(x)}">${x.n && x.ach != null ? pct(x.ach, 1) : '—'}</td>
          <td class="n">${x.n ? f(x.good, 0) : '—'}</td><td class="n">${x.bad == null ? '—' : f(x.bad, 0)}</td><td class="n">${x.rate == null ? '—' : x.rate >= 0.001 ? pct(x.rate, 2) : f(x.rate * 1e6, 0) + ' ppm'}</td>
          <td class="n">${x.run ? f(x.run / 60, 1) + 'h' : '—'}</td><td class="n">${x.stop ? f(x.stop / 60, 1) + 'h' : '—'}</td><td class="n ${x.oee == null ? '' : M.oeeApi.sig(x.oee)}">${x.oee == null ? '—' : pct(x.oee, 1)}</td></tr>`).join('')}</tbody></table>
      <div class="rcols">
        <div><h2>2. 불량 내역</h2><table class="rt"><thead><tr><th>공정</th><th>불량 유형</th><th>수량</th><th>LOT</th></tr></thead>
          <tbody>${R.dsum.map(x => `<tr><td class="l">${esc(x.p)}</td><td class="l">${esc(x.n)}</td><td class="n">${f(x.q, 0)}</td><td class="l sm">${esc(x.lots)}</td></tr>`).join('') || '<tr><td colspan="4">불량 없음</td></tr>'}</tbody></table></div>
        <div><h2>3. 비가동 내역</h2><table class="rt"><thead><tr><th>설비</th><th>시작</th><th>시간</th><th>사유</th><th>구분</th></tr></thead>
          <tbody>${R.dts.map(e => `<tr class="${e.code === 'UNK' ? 'unk' : ''}"><td class="l">${esc(e.nm)}</td><td>${hm(e.at)}</td><td class="n">${e.min}분</td><td class="l">${esc((M.oeeApi.REASONS.find(r => r[0] === e.code) || [])[1] || '')}</td><td>${esc(e.src)}</td></tr>`).join('') || '<tr><td colspan="5">비가동 없음</td></tr>'}</tbody></table></div>
      </div>
      <h2>4. 수주 진행</h2>
      <table class="rt"><thead><tr><th>수주 번호</th><th>고객</th><th>수량</th><th>납기</th><th>생산 실적</th><th>출하</th><th>상태</th><th>비고</th></tr></thead>
        <tbody>${R.orders.map(o => `<tr><td>${esc(o.id)}</td><td>${esc(({ 'T1-B': 'B사', 'T1-E': 'E사', 'T1-F': 'F사' })[o.buyer] || o.buyer)}</td><td class="n">${f(o.qty, 0)}</td><td>${esc(o.due)}</td><td class="n">${f(Math.min(o.made, o.qty), 0)}</td><td class="n">${f(o.shippedQty, 0)}</td><td class="${o.status}">${esc(o.statusT)}</td><td class="l sm">${esc(o.issue || '')}</td></tr>`).join('')}</tbody></table>
      <h2>5. 특이사항</h2>
      <table class="rt"><thead><tr><th>등급</th><th>공정</th><th>내용</th></tr></thead>
        <tbody>${R.notes.map(a => `<tr><td>${a.grade}</td><td>${esc(M.PROC[a.proc] || a.proc)}</td><td class="l">${esc(a.title)}</td></tr>`).join('') || '<tr><td colspan="3">특이사항 없음</td></tr>'}</tbody></table>
      <p class="rfoot">${esc(F.name)} · MILG MES 자동 집계 — 계획 = 라인 밸런스 일 계획, 가동 · 비가동 · OEE = 설비 OEE 기준</p>
    </div>`;
  };
  function xlsx() {
    const R = report(cur), F = M.S.factory, H = rs => rs.map(v => ({ v, s: 'h' }));
    const s1 = { name: '생산실적', widths: [14, 9, 11, 11, 9, 11, 9, 10, 9, 9, 9], head: 0, headRows: [4], merges: ['A1:K1'], rows: [[{ v: `생산 일보 — ${cur}`, s: 'title' }], [{ v: `${F.name} · ${F.line || ''} · 작성 ${M.D.user().name}`, s: 'sub' }], [], ['', '', '', '', '', '', '', '', '작성', '검토', '승인'],
      H(['공정', '설비', '계획', '실적', '달성률', '양품', '불량', '불량률', '가동(h)', '비가동(h)', 'OEE'])].concat(R.prod.map(x => [x.nm, x.eq, x.plan, x.n ? x.qty : '', x.n && x.ach != null ? { v: x.ach, s: 'p' } : '', x.n ? x.good : '', x.bad ?? '', x.rate == null ? '' : { v: x.rate, s: 'p' }, x.run ? +(x.run / 60).toFixed(1) : '', x.stop ? +(x.stop / 60).toFixed(1) : '', x.oee == null ? '' : { v: x.oee, s: 'p' }])) };
    s1.rows[3] = s1.rows[3].map((v, i) => i >= 8 ? { v, s: 'h' } : v);
    const tbl = (name, head, rows, widths) => ({ name, widths, head: 0, headRows: [2], merges: [], rows: [[{ v: `${name} — ${cur}`, s: 'title' }], [], H(head)].concat(rows) });
    return XL().download(`생산일보_${cur}.xlsx`, [s1,
      tbl('불량내역', ['공정', '불량 유형', '수량', 'LOT'], R.dsum.map(x => [x.p, x.n, x.q, x.lots]), [12, 18, 10, 40]),
      tbl('비가동내역', ['설비', '시작', '시간(분)', '사유', '구분', '근거'], R.dts.map(e => [e.nm, hm(e.at), e.min, (M.oeeApi.REASONS.find(r => r[0] === e.code) || [])[1] || '', e.src, e.why]), [12, 8, 10, 18, 12, 40]),
      tbl('수주진행', ['수주 번호', '고객', '수량', '납기', '생산 실적', '출하', '상태', '비고'], R.orders.map(o => [o.id, o.buyer, o.qty, o.due, Math.min(o.made, o.qty), o.shippedQty, o.statusT, o.issue || '']), [16, 8, 11, 12, 11, 11, 10, 44]),
      tbl('특이사항', ['등급', '공정', '내용'], R.notes.map(a => [a.grade, M.PROC[a.proc] || a.proc, a.title]), [8, 12, 70])]);
  }

  // ── 현장 모니터 (안돈)
  let andonT = null;
  function andonHtml() {
    const now = M.now(), lm = M.lineApi ? M.lineApi.model() : null, d = ymd(now), al = M.alerts().filter(a => a.grade !== 'C');
    const L = (M.S.lots || []).filter(l => l.kind === 'process' && dayOf(l) === d), nowMin = now.getHours() * 60 + now.getMinutes();
    const rows = PROCS.map(([p, nm, eq]) => {
      const ls = L.filter(l => l.proc === p), qty = ls.reduce((s, l) => s + (l.qty || 0), 0), plan = lm ? lm.plan[p] || 0 : 0, o = M.oeeApi ? M.oeeApi.days().find(r => r.eq === eq && r.d === d) : null;
      const span = o ? o.planned : 460, start = o ? o.start : 480, frac = Math.max(0, Math.min(1, (nowMin - start) / span)), tgtNow = Math.round(plan * frac);
      const A = al.filter(a => a.proc === p), st = !ls.length ? 'idle' : A.some(a => a.grade === 'A') ? 'alarm' : 'run';
      const ach = ls.length && tgtNow ? qty / tgtNow : null, ac = ach == null ? '' : ach >= 0.95 ? 'ok' : ach >= 0.85 ? 'warn' : 'danger';
      return `<div class="an-r ${st}"><div class="an-nm"><b>${esc(nm)}</b><span>${eq}</span></div>
        <div class="an-st"><em>${st === 'alarm' ? '이상' : st === 'idle' ? '대기' : '가동'}</em></div>
        <div class="an-v"><b>${ls.length ? f(tgtNow, 0) : '—'}</b></div><div class="an-v"><b>${ls.length ? f(qty, 0) : '—'}</b></div>
        <div class="an-ach ${ac}"><div class="an-bar"><i style="width:${ach == null ? 0 : Math.min(100, ach * 100)}%"></i></div><b>${ach == null ? '—' : Math.round(ach * 100) + '%'}</b></div>
        <div class="an-v"><b class="${o ? M.oeeApi.sig(o.oee) : ''}">${o ? pct(o.oee, 0) : '—'}</b></div>
        <div class="an-msg">${st === 'idle' ? '금일 작업 계획 없음' : A.length ? `${A[0].grade} · ${esc(A[0].title)}` : '이상 없음'}</div></div>`; }).join('');
    const tiles = `<div class="an-hd"><span>설비</span><span>상태</span><span>목표(현재)</span><span>실적</span><span>달성률</span><span>OEE</span><span>알람</span></div>${rows}`;
    const heat = L.filter(l => l.proc === 'heat').reduce((s, l) => s + (l.qty || 0), 0), hp = lm ? lm.plan.heat : 0;
    return `<div class="an-top"><b>${esc(M.S.factory.name)} · ${esc(M.S.factory.line || '')}</b><span>${d} (${'일월화수목금토'[now.getDay()]}) · ${nowMin >= 480 && nowMin < 1200 ? '주간' : '야간'} 교대</span><time>${pad(now.getHours())}:${pad(now.getMinutes())}</time><button id="an-x" aria-label="닫기">✕</button></div>
      <div class="an-grid">${tiles}</div>
      <div class="an-bot"><div><span>라인 금일 생산 (열처리 완료)</span><b>${f(heat, 0)}</b><small>/ ${f(hp, 0)}</small></div><div><span>A 알람</span><b class="${al.some(a => a.grade === 'A') ? 'r' : ''}">${al.filter(a => a.grade === 'A').length}</b></div><div><span>B 알람</span><b>${al.filter(a => a.grade === 'B').length}</b></div>
        <div class="an-tick">${al.slice(0, 6).map(a => `<span class="g${a.grade}">${a.grade} · ${esc(M.PROC[a.proc] || a.proc)} — ${esc(a.title)}</span>`).join('')}</div></div>`;
  }
  M.andon = {
    open() { let el = document.getElementById('andon'); if (!el) { el = document.createElement('div'); el.id = 'andon'; document.body.appendChild(el); }
      const draw = () => { el.innerHTML = andonHtml(); el.querySelector('#an-x').onclick = M.andon.close; }; draw(); el.classList.add('on');
      clearInterval(andonT); andonT = setInterval(draw, 30000); try { if (el.requestFullscreen && !document.fullscreenElement) el.requestFullscreen().catch(() => {}); } catch (e) { /* 전체 화면 거부 시 창 안에서 */ } },
    close() { const el = document.getElementById('andon'); if (el) el.classList.remove('on'); clearInterval(andonT); try { if (document.fullscreenElement) document.exitFullscreen(); } catch (e) { /* 무시 */ } },
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape') M.andon.close(); });
  const addBtn = () => { const th = document.getElementById('themebtn'); if (!th || document.getElementById('andonbtn')) return; const b = document.createElement('button'); b.id = 'andonbtn'; b.className = 'palbtn'; b.title = '공장 TV 용 전체 화면 현황판';
    b.innerHTML = '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/></svg>현장 모니터'; b.onclick = M.andon.open; th.before(b); };

  M.mod({
    id: 'report', group: '공정 실적', order: 4.6,
    menu: [['daily', '생산 일보', 'book']],
    views: V,
    bind(root) {
      addBtn();
      root.querySelectorAll('[data-rday]').forEach(b => b.onclick = () => { const ds = prodDays(); cur = ds[+b.dataset.rday] || cur; M.render(); });
      const x = root.querySelector('#rp-xlsx'); if (x) x.onclick = xlsx;
      const p = root.querySelector('#rp-print'); if (p) p.onclick = () => { document.body.classList.add('printing'); window.print(); setTimeout(() => document.body.classList.remove('printing'), 500); };
    },
  });
  M.report = { report, xlsx: () => xlsx(), days: prodDays };
})();
