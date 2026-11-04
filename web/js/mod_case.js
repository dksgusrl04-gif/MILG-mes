/* 3.0 ① — 품질 이슈 (알람 대신 «이슈»)
 * ───────────────────────────────────────────────────────────────
 * 알람은 «무엇이 이상하다» 만 말한다. 사람은 그 뒤에 LOT 이력 · 다른 공정 데이터 · 고객 계약 · 발주 화면을 오가며 조사한다.
 * 이슈는 관련 알람을 한데 묶고, 그 조사를 MES 가 먼저 해 둔다.
 *   영향 범위  — 어느 LOT 이 몇 개, 공장 안인지 이미 납품됐는지, 어느 고객 계약에 걸리는지 (전체 생산 중 조치 대상만 좁힘)
 *   추정 원인  — 같은 공정 + 앞뒤 공정 데이터를 비교해 근거 강도(강함·있음·약함)를 숫자와 함께
 *   돈         — 발생 손실 · 잠재 손실 · 보류 재고 · 에너지 손실 (decide.js)
 *   조치       — 격리 → 원인 확정 → 시정 → 재발 방지 단계별 버튼. 실행 · 단계 이동은 모두 우리 서명으로 신뢰 원장에
 *   보고서     — 8D 형식으로 바로 출력
 */
(function () {
  'use strict';
  const M = window.MLNG, T = window.MLNGTrust, DX = M.dx, { esc, f, pct } = M;
  const STAGES = ['접수', '봉쇄 조치', '원인 분석', '시정 조치', '재발 방지', '종결'];   // 8D: D1 접수 · D3 봉쇄 · D4 원인 · D5-6 시정 · D7 재발 방지 · D8 종결
  const OLD_STAGES = ['감지', '격리', '원인 확정', '시정 조치', '재발 방지', '종결'];   // 5.1 이전 원장 기록
  const ACT_STAGE = { contain: 1, cause: 2, fix: 3, prevent: 4 };
  const CS = {};          // 이슈 진행 상태 (id → { stage, log, done, cause })
  const RD = () => M.CFG.RECENT_DAYS || 7;
  const inDays = (s, n) => M.toDate(s).getTime() >= M.sinceDay(n).getTime();
  const lotOf = id => (M.S.lots || []).find(l => l.id === id);
  const H = () => M.S.heat || { lots: [] }, P = () => M.S.press || { lots: [], vib: { events: [] } }, MC = () => M.S.mach || { tools: [], runs: [] };
  const VBL = () => (M.CFG.MACH || {}).VB_LIMIT || 0.5;
  const yymmdd = s => String(s || '').slice(2, 10).replace(/-/g, '');
  const median = M.median;

  // 납품 · 계약 상태
  const deliveries = () => [].concat(...(T.orders || []).map(o => o.deliveries.map(d => Object.assign({ buyer: o.buyer, so: o.id }, d))));
  const shipState = id => { const d = deliveries().find(x => x.lot === id); if (d) return { k: 'shipped', t: `납품됨 (${T.co(d.buyer).short}${d.claim ? ' · 클레임' : ''})`, buyer: d.buyer };
    if (M.gate && M.gate.isHeld(id)) return { k: 'held', t: '출하 보류' }; return { k: 'inside', t: '공장 안' }; };
  // 어떤 LOT 이든 그 LOT 이 속한 출하 단위(열처리 LOT)
  const heatOf = id => { const l = lotOf(id); if (!l) return []; if (l.proc === 'heat') return [id]; const g = M.lineage(id); return g.up.concat(g.down).filter(x => x.proc === 'heat').map(x => x.id); };

  // 조치 공통
  const hold = (lots, why) => async () => { const ls = [...new Set(lots)].filter(id => shipState(id).k === 'inside'); for (const id of ls) await M.gate.hold(id, why); return ls.length ? `${ls.length} LOT 출하 보류 — ${ls.slice(0, 4).join(', ')}${ls.length > 4 ? ' 외' : ''}` : '보류할 공장 안 LOT 이 없습니다 (이미 보류·납품)'; };
  const po = (codes, why) => async () => { if (!M.buyApi || !codes.length) return '발주할 품목이 없습니다'; const p = await M.buyApi.createPO(codes.map(code => ({ code })), why, '품질 이슈'); return `${p.id} 발주 등록 (승인 대기)`; };
  const order = text => async () => text;
  const whatif = preset => async () => { if (M.whatifApi) { M.whatifApi.preset(preset); M.go('whatif'); } return 'What-if 에 조건을 넣었습니다'; };

  // ── 이슈 유형별 조사
  const TPL = {
    tool: { name: 'MCT 공구 마모 초과', proc: 'mach', match: a => a.proc === 'mach',
      build(c) {
        const lim = VBL(), tools = MC().tools.filter(t => t.vb_last > lim && inDays(t.date, 14));
        tools.forEach(t => { const l = lotOf(t.lot), over = DX.overRuns(t.id).length;
          c.lots.push({ id: t.lot, proc: 'mach', qty: l ? l.qty : t.runs * (MC().parts_per_run || 25), why: `${t.id} VB ${f(t.vb_last, 2)} · 기준 넘긴 가공 ${over}회` });
          M.lineage(t.lot).down.forEach(d => c.lots.push({ id: d.id, proc: d.proc, qty: d.qty, why: `${t.lot} 가공 부품 검사` })); });
        const all = MC().tools.filter(t => inDays(t.date, 30)).reduce((s, t) => s + t.runs * (MC().parts_per_run || 25), 0);
        c.scope = { all, unit: 'MCT 가공 부품 (30일)' };
        const R = m => MC().tools.filter(t => t.rate && t.material === m).map(t => t.rate * 1000), cmp = DX.compare(R('스테인리스강'), R('주철'));
        c.causes.push({ cmp: cmp, t: '재질 — 스테인리스강 가공', strength: cmp.strength, d: `마모 속도 스테인리스강 ${f(cmp.ma, 1)} µm/분 (${cmp.na}개) vs 주철 ${f(cmp.mb, 1)} (${cmp.nb}개) → ${f(cmp.ratio, 1)}배` });
        const cd = (MC().conditions || []).filter(x => x.material === '스테인리스강'), fc = DX.compare(cd.filter(x => x.feed >= 0.5).map(x => x.rate * 1000), cd.filter(x => x.feed < 0.5).map(x => x.rate * 1000));
        c.causes.push({ cmp: fc, t: '이송 속도 — 0.5 mm/rev', strength: fc.strength === 'none' && fc.ratio ? (fc.ratio >= 1.3 ? 'some' : 'weak') : fc.strength, d: `스테인리스강에서 이송 0.5 → ${f(fc.ma, 1)} µm/분, 0.25 → ${f(fc.mb, 1)} (${f(fc.ratio, 1)}배, 조건별 평균 ${fc.na}+${fc.nb}개)` });
        const late = tools.filter(t => t.vb_last > lim * ((M.CFG.MACH || {}).LATE_RATIO || 1.2));
        c.causes.push({ t: '교체 시점 — 늦은 교체', strength: tools.length && late.length / tools.length >= 0.5 ? 'strong' : late.length ? 'some' : 'weak', d: `기준 넘긴 공구 ${tools.length}개 중 ${late.length}개가 기준의 ${(M.CFG.MACH || {}).LATE_RATIO || 1.2}배 이상 (${late.map(t => `${t.id} ${f(t.vb_last, 2)}`).join(', ') || '없음'})` });
        const ins = c.lots.filter(l => l.proc === 'insp').map(l => l.id), mcs = c.lots.filter(l => l.proc === 'mach').map(l => l.id);
        c.actions.push({ id: 'insp', stage: 'contain', label: `해당 가공 부품 검사 강화 지시 (${mcs.length} LOT)`, run: order(`검사 셀에 ${mcs.join(', ')} 가공 부품 전수 측정 지시`) });
        c.actions.push({ id: 'hold', stage: 'contain', label: `가공 · 검사 LOT 출하 보류 (${mcs.length + ins.length})`, run: hold(mcs.concat(ins), '공구 마모 초과 가공') });
        c.actions.push({ id: 'po', stage: 'fix', label: '페이스커터 인서트 발주 (MC-INS)', run: po(['MC-INS'], '이슈 — 공구 마모 초과 인서트 교체') });
        c.actions.push({ id: 'chg', stage: 'fix', label: '마모 공구 인서트 즉시 교체 기록', run: order(`인서트 교체 — ${tools.map(t => t.id).join(', ')}`) });
        c.actions.push({ id: 'wi', stage: 'prevent', label: '교체 기준 VB 0.40 으로 낮추면? (What-if)', run: whatif({ vb: 0.4 }) });
        c.actions.push({ id: 'std', stage: 'prevent', label: '스테인리스강 가공 표준 개정 요청 — 이송 0.25', run: order('작업 표준 개정 요청 — 스테인리스강 이송 0.25 mm/rev') });
      } },
    press: { name: '프레스 설비 이상 · 불량 증가', proc: 'press', match: a => a.proc === 'press',
      build(c) {
        const tgt = (M.CFG.PRESS || {}).TARGET_PPM || 150, ls = P().lots, ev = (P().vib || { events: [] }).events.filter(e => inDays(e.end, 14));
        const evDays = new Set(ev.map(e => String(e.end).slice(0, 10)));
        const bad = ls.filter(l => inDays(l.date, RD()) && (l.ppm > tgt || evDays.has(l.date)));
        bad.forEach(l => { c.lots.push({ id: l.id, proc: 'press', qty: l.qty, why: `${f(l.ppm, 0)} PPM${evDays.has(l.date) ? ' · 진동 이상 당일' : ''}` });
          M.lineage(l.id).down.filter(d => d.proc === 'heat' || d.proc === 'weld').forEach(d => c.lots.push({ id: d.id, proc: d.proc, qty: d.qty, why: `${l.id} 성형품 사용` })); });
        c.scope = { all: ls.filter(l => inDays(l.date, 30)).reduce((s, l) => s + l.qty, 0), unit: '프레스 성형 (30일)' };
        const vb = DX.compare(ls.filter(l => evDays.has(l.date)).map(l => l.ppm), ls.filter(l => !evDays.has(l.date)).map(l => l.ppm));
        c.causes.push({ cmp: vb, t: '설비 진동 이상', strength: vb.na ? vb.strength : 'none', d: ev.length ? `진동 이상 ${ev.length}회 (최대 정상의 ${f(Math.max(...ev.map(e => e.ratio)), 1)}배) · 당일 불량률 ${f(vb.ma, 0)} PPM vs 다른 날 ${f(vb.mb, 0)} PPM (${f(vb.ratio, 1)}배, 하루 표본이라 단정 어려움)` : '최근 진동 이상 없음' });
        const rec = ls.filter(l => inDays(l.date, RD())), cpk = {}; rec.forEach(l => Object.entries(l.stats || {}).forEach(([k, s]) => { if (s.cpk != null) (cpk[k] = cpk[k] || []).push(s.cpk); }));
        const low = Object.entries(cpk).map(([k, v]) => [k, Math.min(...v), median(v)]).sort((a, b) => a[1] - b[1])[0];
        const lab = k => ((P().recipe || []).find(r => r.key === k) || {}).label || k;
        if (low) c.causes.push({ t: `공정능력 — ${lab(low[0])}`, strength: low[1] < 1 ? 'strong' : low[1] < 1.33 ? 'some' : 'weak', d: `최근 ${RD()}일 Cpk 최저 ${f(low[1], 2)} · 중앙값 ${f(low[2], 2)} (기준 1.33)` });
        const dc = {}; rec.forEach(l => Object.entries(l.defects || {}).forEach(([k, v]) => dc[k] = (dc[k] || 0) + v));
        const nm = k => (M.S.masters.defect_codes.find(x => x.code === k) || {}).name || k, tot = Object.values(dc).reduce((s, v) => s + v, 0), top = Object.entries(dc).sort((a, b) => b[1] - a[1]);
        if (top.length) c.causes.push({ t: `불량 유형 — ${top.slice(0, 2).map(([k]) => nm(k)).join('·')}`, strength: top[0][1] / tot >= 0.5 ? 'some' : 'weak', d: `${top.map(([k, v]) => `${nm(k)} ${v}`).join(' · ')} — 주름·터짐은 금형 마모 · 윤활 부족 후보` });
        const mS = median(ls.map(l => l.strokes)), sp = DX.compare(ls.filter(l => l.strokes > mS).map(l => l.ppm), ls.filter(l => l.strokes <= mS).map(l => l.ppm));
        c.causes.push({ cmp: sp, t: '생산 속도 (하루 타발 수)', strength: sp.strength, d: `타발 많은 날 ${f(sp.ma, 0)} PPM vs 적은 날 ${f(sp.mb, 0)} PPM (${f(sp.ratio, 2)}배, ${sp.na}+${sp.nb}일)` });
        const hts = c.lots.filter(l => l.proc === 'heat').map(l => l.id);
        c.actions.push({ id: 'hold', stage: 'contain', label: `하류 열처리 LOT 출하 보류 (${hts.length})`, run: hold(hts, '프레스 불량 증가 LOT 사용') });
        c.actions.push({ id: 'mt', stage: 'fix', label: 'PR-01 설비 점검 지시 — 금형 · 크랭크 · 클러치', run: order('보전 담당에게 PR-01 점검 지시') });
        c.actions.push({ id: 'po', stage: 'fix', label: '예비 펀치 발주 (PR-PUNB · PR-PUNP)', run: po(['PR-PUNB', 'PR-PUNP'], '이슈 — 프레스 설비 이상 예비품') });
        c.actions.push({ id: 'wi', stage: 'prevent', label: '프레스 속도 90% 로 낮추면? (What-if)', run: whatif({ press: 90 }) });
        c.actions.push({ id: 'rule', stage: 'prevent', label: '진동 정상의 5배 초과 시 자동 정지 규칙 등록', run: order('설비 규칙 등록 — 진동 정상 대비 5배 초과 30초 지속 시 자동 정지') });
      } },
    heat: { name: '열처리 품질 · 설비', proc: 'heat', match: a => a.proc === 'heat' || (a.proc === 'deal' && /클레임/.test(a.title)),
      build(c) {
        const tgt = M.heatApi ? M.heatApi.target() : 450, rate = (M.CFG.HEAT || {}).DEVIATION_RATE || 0.05, all = H().lots;
        const rec = all.filter(l => inDays(l.end, 14)), bad = rec.filter(l => l.ppm > tgt), dev = rec.filter(l => M.heatDev(l) >= rate);
        const claims = deliveries().filter(d => d.claim && d.claim.status === 'open');
        bad.forEach(l => c.lots.push({ id: l.id, proc: 'heat', qty: l.qty, why: `${f(l.ppm, 0)} PPM` }));
        dev.forEach(l => c.lots.push({ id: l.id, proc: 'heat', qty: l.qty, why: `조건 이탈 ${pct(M.heatDev(l), 0)}` }));
        claims.forEach(d => c.lots.push({ id: d.lot, proc: 'heat', qty: d.qty, why: `클레임 ${d.claim.id}` }));
        c.scope = { all: all.filter(l => inDays(l.end, 30)).reduce((s, l) => s + l.qty, 0), unit: '열처리 (30일)' };
        const w = DX.heaterWaste()[0], key = w ? w.key : 'op2', hv = all.filter(l => l.heater && l.heater[key] != null).sort((a, b) => a.heater[key] - b.heater[key]), n3 = Math.floor(hv.length / 3);
        const hc = DX.compare(hv.slice(-n3).map(l => l.ppm), hv.slice(0, n3).map(l => l.ppm));
        c.causes.push({ cmp: hc, t: `히터 출력 — ${w ? w.label : '소입로 2존'}`, strength: hc.strength, d: `출력 상위 1/3 작업 ${f(hc.ma, 0)} PPM vs 하위 1/3 ${f(hc.mb, 0)} PPM (${f(hc.ratio, 2)}배, ${hc.na}+${hc.nb}작업)${w ? ` · 출력 +${f(w.diff, 1)}%p 증가 중` : ''}` });
        const dc = DX.compare(all.filter(l => M.heatDev(l) >= rate).map(l => l.ppm), all.filter(l => M.heatDev(l) < rate).map(l => l.ppm));
        c.causes.push({ cmp: dc, t: '열처리 조건 이탈', strength: dc.strength, d: `이탈 작업 ${f(dc.ma, 0)} PPM (${dc.na}건) vs 정상 ${f(dc.mb, 0)} PPM (${dc.nb}건) → ${f(dc.ratio, 2)}배` });
        const up = all.map(l => { const pr = M.lineage(l.id).up.find(x => x.proc === 'press'), pl = pr && P().lots.find(x => x.id === pr.id); return pl ? { h: l.ppm, p: pl.ppm } : null; }).filter(Boolean);
        const mp = median(up.map(x => x.p)), uc = DX.compare(up.filter(x => x.p > mp).map(x => x.h), up.filter(x => x.p <= mp).map(x => x.h));
        c.causes.push({ cmp: uc, t: '앞 공정 — 프레스 불량률 (공정 간)', strength: up.length >= 6 ? uc.strength : 'none', d: up.length ? `프레스 불량 높은 날 성형품 → 열처리 ${f(uc.ma, 0)} PPM vs 낮은 날 ${f(uc.mb, 0)} PPM (${f(uc.ratio, 2)}배, ${up.length}작업 연결)` : '프레스와 연결된 열처리 작업이 없어 비교 불가' });
        const mq = median(all.map(l => l.qty)), qc = DX.compare(all.filter(l => l.qty > mq).map(l => l.ppm), all.filter(l => l.qty <= mq).map(l => l.ppm));
        c.causes.push({ cmp: qc, t: '장입량 (한 작업 수량)', strength: qc.strength, d: `많이 넣은 작업 ${f(qc.ma, 0)} PPM vs 적게 넣은 작업 ${f(qc.mb, 0)} PPM (${f(qc.ratio, 2)}배)` });
        const inside = c.lots.map(l => l.id);
        c.actions.push({ id: 'hold', stage: 'contain', label: '고불량 · 조건 이탈 LOT 출하 보류', run: hold(inside, '열처리 불량·조건 이탈') });
        if (claims.length) c.actions.push({ id: 'rc', stage: 'contain', label: `클레임 LOT 재검사 요청 (${claims.map(d => d.lot).join(', ')})`, run: async () => { for (const d of claims) { const o = T.orders.find(x => x.id === d.so), dd = o.deliveries.find(x => x.lot === d.lot); dd.claim.status = 'recheck'; await T.append('MLNG', 'claim', dd.claim.id, '클레임 재검사 요청 — 품질 이슈', { id: dd.claim.id, status: 'recheck' }); } return `재검사 요청 ${claims.length}건 — 고객 MES 에 전달`; } });
        c.actions.push({ id: 'mt', stage: 'fix', label: '소입로 히터 · 단열재 · 열전대 점검 지시', run: order('보전 담당에게 소입로 히터·단열재·열전대 점검 지시') });
        const hc2 = M.buyApi ? M.buyApi.itemsFor({ proc: 'heat', go: 'heat_equip' }) : [];
        if (hc2.length) c.actions.push({ id: 'po', stage: 'fix', label: `히터 예비품 발주 (${hc2.join(', ')})`, run: po(hc2, '이슈 — 열처리 히터 출력 증가') });
        c.actions.push({ id: 'rule', stage: 'prevent', label: '조건 이탈 5% 이상 LOT 자동 보류 규칙 켜기', run: async () => { M.gate.rules.dev = true; return '출하 승인 규칙 — 조건 이탈 자동 보류 켬'; } });
        c.actions.push({ id: 'heater', stage: 'prevent', label: '히터 정비하면 전기 · 탄소 얼마나 줄까? (What-if)', run: whatif({ heater: true }) });
      } },
    weld: { name: '점용접 불량 위험', proc: 'weld', match: a => a.proc === 'weld',
      build(c) {
        const W = M.S.weld || { lots: [], windows: [] }, l = W.lots[W.lots.length - 1]; if (!l) return;
        c.lots.push({ id: l.id, proc: 'weld', qty: l.welds, why: `예상 불량 ${f(l.est, 1)} · 경보 타점 ${f((W.summary || {}).tiers ? W.summary.tiers['경보'] : 0, 0)}` });
        M.lineage(l.id).down.filter(d => d.proc === 'heat').forEach(d => c.lots.push({ id: d.id, proc: 'heat', qty: d.qty, why: `${l.id} 용접품 사용` }));
        c.scope = { all: W.lots.reduce((s, x) => s + x.welds, 0), unit: '점용접 타점 (전체)' };
        (W.windows || []).slice().sort((a, b) => b.ratio - a.ratio).slice(0, 3).forEach(w => c.causes.push({ t: `${w.variable} 안정 구간 벗어남`, strength: w.ratio >= 2 ? 'strong' : w.ratio >= 1.3 ? 'some' : 'weak', d: `안정 구간 ${f(w.stable_lo, 2)}~${f(w.stable_hi, 2)} 밖이면 불량 위험 ${f(w.ratio, 1)}배 · 안정 구간 비율 ${pct(w.stable_share, 0)}` }));
        const hts = c.lots.filter(x => x.proc === 'heat').map(x => x.id);
        c.actions.push({ id: 'q', stage: 'contain', label: '위험 상위 타점 1순위 검사', run: async () => { M.go('weld_queue'); return '검사 우선순위 화면으로 이동'; } });
        c.actions.push({ id: 'hold', stage: 'contain', label: `하류 열처리 LOT 출하 보류 (${hts.length})`, run: hold(hts, '점용접 불량 위험') });
        c.actions.push({ id: 'po', stage: 'fix', label: '전극 팁 교체 · 발주 (SP-TIP)', run: po(['SP-TIP'], '이슈 — 점용접 가압 이상') });
        c.actions.push({ id: 'win', stage: 'prevent', label: '안정 공정 조건 창을 작업 표준에 반영', run: async () => { M.go('weld_window'); return '안정 공정조건 화면으로 이동 — 표준 반영 검토'; } });
      } },
    insp: { name: '검사 판정 불일치', proc: 'insp', match: a => a.proc === 'insp',
      build(c) {
        const I = M.S.insp || { lots: [], checks: {} }, ck = I.checks || {};
        I.lots.forEach(l => { if (l.conflict) c.lots.push({ id: l.id, proc: 'insp', qty: l.parts, why: `확정 필요 ${l.conflict}개` }); });
        c.scope = { all: I.lots.reduce((s, l) => s + l.parts, 0), unit: '검사 부품 (전체)' };
        c.causes.push({ t: '같은 시각 이중 기록 — 수집 중복 또는 재검 미구분', strength: ck.conflict ? 'strong' : 'none', d: `같은 부품 번호 · 같은 시각에 합격·불합격이 함께 있는 부품 ${f(ck.conflict || 0, 0)}개 — 불합격 기록 전부가 이 경우` });
        if ((ck.stuck || []).length) c.causes.push({ t: `${ck.stuck.join(',')}축 센서 고착`, strength: 'strong', d: `${ck.stuck_cols}개 항목이 모든 부품에서 ${ck.const_value} 로 고정 — 이 축으로는 판정 불가` });
        const ins = c.lots.map(l => l.id);
        c.actions.push({ id: 'hold', stage: 'contain', label: `판정 확정 전 출하 보류 (${ins.length} LOT)`, run: hold(ins, '검사 판정 확정 필요') });
        c.actions.push({ id: 'cf', stage: 'cause', label: '이중 기록 부품 판정 확정', run: async () => { M.go('insp_parts'); return '부품 이력 화면으로 이동'; } });
        c.actions.push({ id: 'mt', stage: 'fix', label: '검사 셀 센서 배선 · 수집 설정 점검 지시', run: order('검사 셀 센서 점검 지시 — 고착 축 판정 보류') });
        c.actions.push({ id: 'rule', stage: 'prevent', label: '같은 부품 · 같은 시각 중복 기록 차단 규칙', run: order('수집 규칙 등록 — 같은 부품 번호·시각 중복 기록은 재검 표시 없이는 거부') });
      } },
    plan: { name: '결품 위험', proc: 'plan', match: a => a.proc === 'plan',
      build(c, al) {
        c.scope = null;
        if (M.planApi) M.planApi.items().forEach(it => { const s = M.planApi.stat(it); if (s.short) {
          c.causes.push({ t: `${it} 수주가 생산보다 많음`, strength: s.avg > s.std * 1.1 ? 'some' : 'weak', d: `최근 평균 수주 ${f(s.avg, 0)} vs 생산 ${f(s.std, 0)} · 마감 재고 ${f(s.last.inv_close, 0)} (${f(s.cover, 1)}일분)` });
          if (s.need) c.actions.push({ id: 'wo' + it, stage: 'fix', label: `${it} 작업지시 초안 ${f(s.need, 0)}개`, run: async () => { await M.planApi.makeWo(it, s.need); return `${it} 작업지시 초안 ${f(s.need, 0)}개`; } }); } });
        c.actions.push({ id: 'ss', stage: 'prevent', label: '안전재고 0.5일 → 1일 상향 검토', run: order('계획 기준 변경 요청 — 안전재고 1일') });
      } },
  };

  // ── 이슈 만들기 (지금 알람에서)
  let cache = null;
  function build() {
    if (cache) return cache;
    const A = M.alerts().filter(a => a.level !== 'info'), out = [];
    Object.entries(TPL).forEach(([k, tp]) => {
      const al = A.filter(tp.match); if (!al.length) return;
      const opened = al.map(a => String(a.at || '')).filter(Boolean).sort()[0] || M.S.asof;
      const id = `CS-${k.toUpperCase()}-${yymmdd(opened)}`;
      const c = { id, tpl: k, name: tp.name, proc: tp.proc, alerts: al, opened, level: al.some(a => a.level === 'danger') ? 'danger' : 'warn', lots: [], causes: [], actions: [], scope: null };
      try { tp.build(c, al); } catch (e) { console.warn('[case]', k, e); }
      // 다중 비교 보정: 후보가 여러 개면 «하나쯤은 우연히 유의» 해 보이므로 BH 로 q 값을 낸다
      const pc = c.causes.filter(x => x.cmp && x.cmp.p != null); if (pc.length && M.st) M.st.bh(pc.map(x => x.cmp.p)).forEach((q, i) => { pc[i].q = q; if (pc[i].strength === 'strong' && q >= 0.05) pc[i].strength = 'some'; });
      c.causes.sort((a, b) => DX.RANK[a.strength] - DX.RANK[b.strength]);
      // 4.0 원인을 실험한다: 불량률(PPM) 비교 후보는 «두 조건을 번갈아 운전하면 몇 작업이면 가릴 수 있나» 를 계산해 실험 계획으로 제안
      const top = c.causes.find(x => x.cmp && x.cmp.ma != null && x.cmp.mb != null && /PPM/.test(x.d) && x.strength !== 'inverse');
      if (top && M.st && (k === 'heat' || k === 'press')) { const qs = (k === 'heat' ? (M.S.heat || { lots: [] }).lots : (M.S.press || { lots: [] }).lots).map(l => l.qty), qa = M.st.mean(qs) || 1;
        const m1 = Math.max(top.cmp.ma, top.cmp.mb) * qa / 1e6, m2 = Math.min(top.cmp.ma, top.cmp.mb) * qa / 1e6, n = M.st.poissonN(m1, m2);
        c.experiment = { cause: top.t, n: isFinite(n) ? n : null, m1, m2, qa, p: top.cmp.p };
        if (isFinite(n)) c.actions.push({ id: 'exp', stage: 'cause', label: `확인 실험 — «${top.t}» 조건 A/B 를 ${n}작업씩 번갈아 운전 (유의수준 5% · 검출력 80%)`, run: async () => {
          c.st.exp = Object.assign({ at: T.stamp(), by: M.D.user().name, status: '계획' }, c.experiment);
          return `실험 계획 서명 — ${top.t}: A/B 각 ${n}작업 (작업당 기대 불량 ${f(m1, 1)} vs ${f(m2, 1)}개) · 결과가 나오면 원인 분석 완료`; } }); }
      // LOT 정리 (같은 LOT 은 하나로, 상태 · 고객)
      const m = new Map(); c.lots.forEach(l => { const x = m.get(l.id); if (x) { if (!x.why.includes(l.why)) x.why += ' · ' + l.why; } else m.set(l.id, Object.assign({}, l)); });
      c.lots = [...m.values()].map(l => Object.assign(l, { ship: shipState(l.id) }));
      c.qty = c.lots.filter(l => l.proc === c.proc || c.proc === 'plan').reduce((s, l) => s + (l.qty || 0), 0) || c.lots.reduce((s, l) => s + (l.qty || 0), 0);
      const ms = al.map(DX.money).filter(Boolean), sum = k2 => ms.reduce((s, x) => s + (x[k2] || 0), 0);
      c.money = { loss: sum('loss'), risk: sum('risk'), tied: sum('tied'), waste: sum('waste') }; c.money.won = c.money.loss + c.money.risk + c.money.tied + c.money.waste; c.money.kind = ['loss', 'risk', 'tied', 'waste'].reduce((a, k2) => c.money[k2] > c.money[a] ? k2 : a, 'loss');
      c.money.basis = [].concat(...ms.map(x => x.basis));
      c.customers = [...new Set(c.lots.map(l => l.ship.buyer).filter(Boolean))];
      const heats = [...new Set([].concat(...c.lots.map(l => heatOf(l.id))))];
      c.contracts = (T.contracts || []).filter(x => x.provider === 'MLNG' && x.scopes.includes('lot_passport') && T.contractState(x) === 'active');
      c.heats = heats;
      // 위험해지는 납품 조항 — 영향 LOT(출하 단위) 중 아직 안 보낸 것을 고객 품질 협정 기준으로 미리 판정
      c.clauseRisk = []; if (M.gate && DX.activeSO()) heats.filter(h => shipState(h).k !== 'shipped').forEach(h => { const r = M.gate.predict(h, { noCase: true }); r.checks.filter(x => !x.ok || x.near).forEach(x => c.clauseRisk.push({ lot: h, label: x.label, actual: x.actual, th: x.th, fail: !x.ok })); });
      c.save = c.money.loss ? c.money.risk + c.money.tied + c.money.waste * 30 : c.money.won - c.money.loss + c.money.waste * 29;   // 조치하면 막을 수 있는 돈 (이미 버린 실손 제외 · 낭비는 30일)
      const st = CS[id] = CS[id] || { stage: 0, log: [], done: {}, cause: null };
      if (st.restored) { c.actions.forEach(a => { const r = st.restored.labels[a.label]; if (r && !st.done[a.id]) st.done[a.id] = r; }); if (st.restored.cause && st.cause == null) { const i = c.causes.findIndex(x => x.t === st.restored.cause); if (i >= 0) st.cause = i; } }
      c.st = st; c.stage = st.stage;
      out.push(c);
    });
    out.sort((a, b) => (a.st.stage >= 5) - (b.st.stage >= 5) || b.money.won - a.money.won || (b.level === 'danger') - (a.level === 'danger'));   // 돈 순서
    cache = out; queueMicrotask(() => { cache = null; });
    return out;
  }
  const open = () => build().filter(c => c.st.stage < 5);
  const next = c => { const pend = c.actions.filter(a => !c.st.done[a.id]).sort((a, b) => ACT_STAGE[a.stage] - ACT_STAGE[b.stage]); return pend.find(a => ACT_STAGE[a.stage] >= Math.max(1, c.st.stage)) || pend[0]; };
  // 실시간 모드: 서명된 원장 기록(제목)에서 이슈 단계 · 실행한 조치 · 고른 원인을 복원한다
  const restore = () => { let n = 0; (T.ledger || []).filter(e => e.kind === 'case').forEach(e => {
    const st = CS[e.ref] = CS[e.ref] || { stage: 0, log: [], done: {}, cause: null }; st.restored = st.restored || { labels: {}, cause: null }; n++;
    const t = String(e.title), mS = t.match(/^(?:사건|이슈) (\S+(?: \S+)?) — /), mA = t.match(/^(?:사건|이슈) 조치 — (.+)$/), mC = t.match(/^(?:사건|이슈) 원인 선택 — (.+)$/);
    if (mS) { const k = STAGES.indexOf(mS[1]) >= 0 ? STAGES.indexOf(mS[1]) : OLD_STAGES.indexOf(mS[1]); if (k >= 0) st.stage = Math.max(st.stage, k); }
    if (mA) { st.restored.labels[mA[1]] = { at: e.at, by: '원장 복원', result: '이전 기록' }; }
    if (mC) st.restored.cause = mC[1];
    st.log.unshift({ at: e.at, by: '원장', text: t }); }); cache = null; return n; };
  T.onInit(() => { if (M.D.LIVE) restore(); });
  M.caseApi = {
    restore,
    list: build, open, next,
    get: id => build().find(c => c.id === id),
    casesOfLot: lot => open().filter(c => c.lots.some(l => l.id === lot)).map(c => ({ id: c.id, name: c.name })),
    runAll: async cid => { const c = M.caseApi.get(cid); if (!c) return ''; const out = []; for (const a of c.actions.filter(x => !c.st.done[x.id] && !/What-if|이동/.test(x.label)).sort((x, y) => ACT_STAGE[x.stage] - ACT_STAGE[y.stage])) { try { out.push(await M.caseApi.run(cid, a.id)); } catch (e) { out.push('실패 ' + e.message); } }
      if (c.st.cause == null && c.causes.length) { c.st.cause = 0; await T.append('MLNG', 'case', cid, `이슈 원인 선택 — ${c.causes[0].t}`, { case: cid, cause: c.causes[0].t, auto: true }); }
      await M.caseApi.advance(cid, '조치 일괄 승인 · 8D 보고서 출력 가능'); return `조치 ${out.length}건 실행 — ${out.join(' · ')}`.slice(0, 300); },
    run: async (cid, aid) => { const c = M.caseApi.get(cid), a = c && c.actions.find(x => x.id === aid); if (!a) throw new Error('조치를 찾지 못했습니다');
      const r = await a.run(); c.st.done[a.id] = { at: T.stamp(), by: M.D.user().name, result: r }; c.st.log.unshift({ at: T.stamp(), by: M.D.user().name, text: `${a.label} → ${r}` });
      if (c.st.stage < ACT_STAGE[a.stage]) c.st.stage = Math.min(ACT_STAGE[a.stage], 4);
      await T.append('MLNG', 'case', c.id, `이슈 조치 — ${a.label}`.slice(0, 160), { case: c.id, action: a.id, result: r });
      if (M.prove) { const inp = { case: c.id, action: a.id, alerts: c.alerts.map(x => ({ key: x.key || null, data: x.data || null })) }, red = i => ({ action: i.action, won: Math.round(i.alerts.reduce((s, x) => s + ((DX.money(x) || {}).won || 0), 0)) });
        await M.prove.record({ kind: 'case', ref: c.id, title: `이슈 조치 판단 — ${a.label}`.slice(0, 80), model: 'decide@3.0', inputs: inp, output: red(inp), redo: red, now: () => ({ case: c.id, action: a.id, alerts: (M.caseApi.get(c.id) || c).alerts.map(x => ({ key: x.key || null, data: x.data || null })) }) }); }
      return r; },
    advance: async (cid, note) => { const c = M.caseApi.get(cid); if (!c || c.st.stage >= 5) return; c.st.stage += 1;
      c.st.log.unshift({ at: T.stamp(), by: M.D.user().name, text: `단계 → ${STAGES[c.st.stage]}${note ? ' · ' + note : ''}` });
      await T.append('MLNG', 'case', c.id, `이슈 ${STAGES[c.st.stage]} — ${c.name}${note ? ' · ' + note : ''}`.slice(0, 160), { case: c.id, stage: c.st.stage, note: note || null, money: Math.round(c.money.won) }); },
    STAGES,
  };

  // ── 화면
  let cur = null, msg = '';
  const stTag = c => c.st.stage >= 5 ? '<span class="tag t-off">종결</span>' : `<span class="tag ${c.level === 'danger' ? 'j-danger' : 'j-warn'}">${esc(STAGES[c.st.stage])}</span>`;
  const stepper = c => `<div class="cstep">${STAGES.map((s, i) => `<span class="${i < c.st.stage ? 'done' : i === c.st.stage ? 'now' : ''}"><i>${i < c.st.stage ? '✓' : i + 1}</i>${esc(s)}</span>`).join('')}</div>`;
  const moneyRow = m => `<div class="cmoney">${Object.entries(DX.KIND).filter(([k]) => m[k]).map(([k, [t, d]]) => `<div class="k-${k}" title="${esc(d)}"><small>${esc(t)}</small><b>${DX.wonShort(m[k])}</b></div>`).join('') || '<div><small>금액</small><b>—</b></div>'}</div>`;
  const V = {};
  V.cases = () => {
    if (!T.ready) return '<div class="empty">신뢰 계층을 준비하는 중…</div>';
    const cs = build(), op = cs.filter(c => c.st.stage < 5), c = cs.find(x => x.id === cur) || op[0] || cs[0];
    if (c) cur = c.id;
    const risk = op.reduce((s, x) => s + x.money.won, 0), inside = op.reduce((s, x) => s + x.lots.filter(l => l.ship.k === 'inside').length, 0);
    return `<div class="kstrip">
      <div class="kpi"><div class="lab">미결 이슈</div><div class="val" style="color:${op.some(x => x.level === 'danger') ? 'var(--danger-ink)' : 'inherit'}">${op.length}<small>건</small></div><div class="foot">알람 ${M.alerts().filter(a => a.level !== 'info').length}건을 ${cs.length}건으로 묶음</div></div>
      <div class="kpi click" data-go="cost"><div class="lab">예상 손실${M.asmTag ? M.asmTag('loss') : ''}</div><div class="val">${DX.wonShort(risk)}<small>원</small></div><div class="foot">발생 손실 · 잠재 손실 · 보류 재고 · 에너지 손실 합</div></div>
      <div class="kpi click" data-go="gate"><div class="lab">미봉쇄 LOT</div><div class="val" style="color:${inside ? 'var(--warn-ink)' : 'var(--ok)'}">${inside}<small>LOT</small></div><div class="foot">이슈에 걸렸지만 아직 공장 안 · 보류 전</div></div>
      <div class="kpi"><div class="lab">종결</div><div class="val">${cs.length - op.length}<small>건</small></div><div class="foot">단계마다 서명 · 원장 기록</div></div>
    </div>
    <div class="card"><h2>품질 이슈 목록 <span class="h2sub">관련 알람을 한데 묶고, 영향 범위 · 추정 원인 · 돈 · 조치를 미리 조사해 둡니다</span></h2>
      ${cs.length ? `<div class="tbx"><table class="tb issue-table"><thead><tr><th>이슈 번호</th><th>공정</th><th>이슈 내용</th><th>처리 단계</th><th class="n">영향 LOT</th><th class="n">예상 손실</th></tr></thead><tbody>${cs.map(x => `<tr class="click ${x.id === cur ? 'selected' : ''}" data-csel="${esc(x.id)}">
        <td class="mono">${esc(x.id)}</td><td>${M.procTag(x.proc)}</td><td><b>${esc(x.name)}</b></td><td>${stTag(x)}</td><td class="n">${x.lots.length}</td><td class="n">${x.money.won ? DX.wonShort(x.money.won) + '원' : '—'}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">미결 이슈 없음</div>'}</div>
    ${c ? detail(c) : ''}`;
  };
  function detail(c) {
    const nx = next(c), byStage = k => c.actions.filter(a => a.stage === k);
    const sc = c.scope && c.scope.all ? c.lots.filter(l => l.proc === c.proc).reduce((s, l) => s + (l.qty || 0), 0) : 0;
    const actBlock = (k, t) => byStage(k).length ? `<div class="cact"><h4>${esc(t)}</h4>${byStage(k).map(a => { const d = c.st.done[a.id];
      return `<div class="ca ${d ? 'done' : ''}"><span>${d ? '✓' : '○'}</span><div>${esc(a.label)}${d ? `<div class="muted" style="font-size:12px">${esc(d.result)} · ${esc(d.by)} ${esc(d.at.slice(5, 16))}</div>` : ''}</div>${d ? '' : `<button class="btn sm ${nx && nx.id === a.id ? 'pri' : ''}" data-crun="${esc(c.id)}|${esc(a.id)}">실행</button>`}</div>`; }).join('')}</div>` : '';
    return `<div class="card cdet" style="margin-top:16px">
      <h2>${esc(c.name)} <span class="h2sub">${esc(c.id)}</span><span class="more" style="display:flex;gap:6px">${c.st.stage < 4 ? `<button class="btn sm" data-call="${esc(c.id)}" title="남은 조치(화면 이동 제외)를 순서대로 모두 실행하고 단계를 올립니다">조치 일괄 승인</button>` : ''}<button class="btn sm" data-crep="${esc(c.id)}">8D 보고서</button>${c.st.stage < 5 ? `<button class="btn pri sm" data-cadv="${esc(c.id)}">${esc(STAGES[c.st.stage + 1])} 단계로 (서명)</button>` : ''}</span></h2>
      ${stepper(c)}
      ${msg ? `<p class="note msg" style="margin-top:6px">${esc(msg)}</p>` : ''}
      ${nx && c.st.stage < 5 ? `<div class="cnext"><b>다음 조치</b> ${esc(nx.label)} <button class="btn pri sm" data-crun="${esc(c.id)}|${esc(nx.id)}">실행</button><button class="btn sm" data-askq="${esc(`이슈 ${c.id} (${c.name}) — 추정 원인과 「${nx.label}」 말고 다른 선택지를 알려줘`)}">AI 문의</button></div>` : ''}
      <div class="grid g2" style="align-items:start;margin-top:12px">
        <div><h3 class="dh" style="margin-top:0">손실 금액 ${M.asmTag ? M.asmTag('loss') : ''}<span class="h2sub">항목에 마우스를 올리면 산출 근거</span></h3>${moneyRow(c.money)}
          ${c.money.basis.length ? `<details class="ui-details"><summary>비용 산정 근거</summary><ul class="plain cbasis">${c.money.basis.map(b => `<li>${esc(b)}</li>`).join('')}</ul></details>` : ''}
          <h3 class="dh">영향 범위</h3>
          ${sc ? `<div class="cscope"><div><i style="--w:${Math.max(1.5, Math.min(100, sc / c.scope.all * 100))}%"></i></div><span>조사 대상 <b>${f(sc, 0)}개 (${pct(sc / c.scope.all, sc / c.scope.all < 0.01 ? 2 : 1)})</b> · ${esc(c.scope.unit)} ${f(c.scope.all, 0)}개 기준</span></div>` : ''}
          ${M.table([['LOT', l => `<b>${esc(l.id)}</b>`], ['공정', l => M.procTag(l.proc)], ['수량', l => f(l.qty, 0), 'n'], ['상태', l => `<span class="tag ${l.ship.k === 'shipped' ? 'j-danger' : l.ship.k === 'held' ? 'j-ok' : 'j-warn'}">${esc(l.ship.t)}</span>`], ['이유', l => `<span class="muted" style="font-size:12px">${esc(l.why)}</span>`]], c.lots.slice(0, 14), { wrap: false, click: l => 'trace:' + l.id, empty: '연결된 LOT 이 없습니다' })}
          ${c.lots.length > 14 ? `<p class="note">외 ${c.lots.length - 14} LOT</p>` : ''}
          ${c.clauseRisk.length ? `<h3 class="dh">기준 초과 우려 품질 항목 <span class="h2sub">아직 안 보낸 LOT 을 고객 계약으로 미리 판정</span></h3>${M.table([['LOT', r => `<b>${esc(r.lot)}</b>`], ['조항', 'label'], ['예상', r => `<span class="${r.fail ? 'ng' : ''}" style="${r.fail ? '' : 'color:var(--warn-ink)'}">${esc(r.actual)}</span>`, 'n'], ['기준', 'th']], c.clauseRisk, { wrap: false, click: r => 'gate:' + r.lot })}` : ''}
          ${c.customers.length || c.contracts.length ? `<p class="note">고객 영향 — ${c.customers.length ? `이미 납품: ${c.customers.map(k => esc(T.co(k).name)).join(', ')} · ` : ''}LOT 여권 공유 계약 ${c.contracts.map(x => esc(T.co(x.consumer).short)).join(', ') || '없음'} (계약 상대는 여권으로 영향 LOT 을 직접 확인)</p>` : ''}
        </div>
        <div><h3 class="dh" style="margin-top:0">추정 원인 <span class="h2sub">같은 공정 + 앞뒤 공정 데이터 비교</span></h3>
          <div class="ccause">${c.causes.map((x, i) => { const [k, t] = DX.STRENGTH[x.strength] || DX.STRENGTH.none;
            return `<label class="cc ${c.st.cause === i ? 'pick' : ''}"><input type="radio" name="cc-${esc(c.id)}" data-ccause="${esc(c.id)}|${i}" ${c.st.cause === i ? 'checked' : ''}><div><b>${esc(x.t)}</b> <span class="tag ${k}">연관성 ${esc(t)}</span><div class="muted" style="font-size:12.5px;margin-top:2px">${esc(x.d)}</div>${x.cmp && x.cmp.p != null ? `<div style="font-size:12px;margin-top:3px;color:var(--ink-2)">p 값 = ${f(x.cmp.p, 3)}${x.q != null ? ` · 보정 q = ${f(x.q, 3)}` : ''} · 배수 90% 구간 ${f(x.cmp.lo, 2)}~${f(x.cmp.hi, 2)}</div>` : ''}</div></label>`; }).join('') || '<div class="empty">추정 원인을 만들 데이터가 없습니다</div>'}</div>
          <p class="note">연관성 = 순열 검정 p 값과 부트스트랩 90% 신뢰구간 기준 (p &lt; 0.01 · 하한 1.2배 이상 = 높음, p &lt; 0.1 = 보통, 반대로 뚜렷하면 역상관). 후보가 여럿이면 BH 보정 q 값을 함께 봅니다. 인과 확정이 아니라 우선 확인 대상이며, 선택하면 「원인 분석」 단계에 기록됩니다.</p>
          <h3 class="dh">조치</h3>${actBlock('contain', '봉쇄 조치')}${actBlock('cause', '원인 분석')}${actBlock('fix', '시정 조치')}${actBlock('prevent', '재발 방지')}
          ${c.experiment ? `<details class="ui-details"><summary>확인 시험 산정 근거 ${c.st.exp ? '· 계획 서명됨' : ''}</summary><p>«${esc(c.experiment.cause)}» p = ${f(c.experiment.p, 3)} · 조건별 ${c.experiment.n || '미정'}작업. 유의수준 5% · 검출력 80% 가정, 기대 불량 ${f(c.experiment.m1, 1)} / ${f(c.experiment.m2, 1)}개. 실제 시험은 공정 담당자 검토·승인 후 진행합니다.</p></details>` : ''}
          <div class="pacts"><button class="btn sm" data-cfirst="${esc(c.id)}">최초 발생 조회</button><span id="cfirst" class="muted" style="font-size:12.5px">${c.st.first ? esc(c.st.first) : ''}</span></div>
        </div></div>
      ${M.spc ? `<details class="ui-fold"><summary>상세 분석 보고서 · SPC / 층별 / 원인 검정</summary>${M.spc.caseReport(c)}</details>` : ''}
      <div class="grid g2" style="margin-top:12px;align-items:start">
        <div><h3 class="dh" style="margin-top:0">묶인 알람</h3><div class="alist">${c.alerts.map(a => { const m = DX.money(a); return `<div class="al ${esc(a.level)} ${a.go ? 'click' : ''}" ${a.go ? `data-row="${esc(a.go)}"` : ''}><div class="lv"></div><div style="min-width:0;flex:1"><div class="tt">${esc(a.title)} ${DX.chip(m)}</div><div class="bd">${esc(a.body || '')}</div></div></div>`; }).join('')}</div></div>
        <div><h3 class="dh" style="margin-top:0">이슈 기록 <a class="more" href="#" data-go="net_ledger">신뢰 원장 →</a></h3>
          ${M.table([['시각', l => `<span class="muted">${esc(l.at.slice(5, 16))}</span>`], ['내용', 'text'], ['담당', 'by']], c.st.log, { wrap: false, empty: '아직 기록이 없습니다 — 조치를 실행하거나 단계를 올리면 서명과 함께 남습니다' })}</div></div></div>`;
  }
  // 8D 보고서
  function report(c) {
    const w = window.open('', '_blank'); if (!w) return;
    const li = a => `<li>${esc(a)}</li>`, done = c.actions.filter(a => c.st.done[a.id]), cause = c.st.cause != null ? c.causes[c.st.cause] : null;
    w.document.write(`<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>${esc(c.id)} 8D</title><style>body{font-family:"Malgun Gothic","Apple SD Gothic Neo",sans-serif;margin:28px;color:#111;font-size:13px}h1{font-size:20px;margin:0 0 4px}h2{font-size:14px;border-bottom:2px solid #222;padding-bottom:3px;margin:18px 0 6px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #999;padding:4px 6px;text-align:left}small{color:#555}@media print{button{display:none}}</style></head><body>
      <button onclick="print()">인쇄</button><h1>8D 문제 해결 보고서 — ${esc(c.name)}</h1><small>${esc(c.id)} · ${esc(M.S.factory.name)} · 작성 ${esc(T.stamp())} · 단계 ${esc(STAGES[c.st.stage])}</small>
      <h2>D1 팀</h2><p>품질 담당 · 생산기술 · 보전 (${esc(M.D.user().name)} 작성)</p>
      <h2>D2 문제 기술</h2><ul>${c.alerts.map(a => li(`${a.title} — ${a.body || ''} (${a.at || ''})`)).join('')}</ul>
      <h2>D3 봉쇄 조치</h2><ul>${done.filter(a => a.stage === 'contain').map(a => li(`${a.label} → ${c.st.done[a.id].result}`)).join('') || '<li>미실시</li>'}</ul>
      <table><tr><th>LOT</th><th>공정</th><th>수량</th><th>상태</th><th>이유</th></tr>${c.lots.map(l => `<tr><td>${esc(l.id)}</td><td>${esc(M.PROC[l.proc] || l.proc)}</td><td>${f(l.qty, 0)}</td><td>${esc(l.ship.t)}</td><td>${esc(l.why)}</td></tr>`).join('')}</table>
      <h2>D4 근본 원인 조사</h2>${M.spc && M.spc.caseLines(c).length ? `<p><b>데이터 분석</b></p><ul>${M.spc.caseLines(c).map(li).join('')}</ul>` : ''}${cause ? `<p><b>검토 후보: ${esc(cause.t)}</b> — ${esc(cause.d)} (원인 확정에는 현장 검증 필요)</p>` : '<p>미확정</p>'}<ul>${c.causes.map(x => li(`${x.t} (연관성 ${DX.STRENGTH[x.strength][1]}) — ${x.d}`)).join('')}</ul>
      <h2>D5·D6 시정 조치</h2><ul>${done.filter(a => a.stage === 'fix').map(a => li(`${a.label} → ${c.st.done[a.id].result}`)).join('') || '<li>미실시</li>'}</ul>
      <h2>D7 재발 방지</h2><ul>${done.filter(a => a.stage === 'prevent').map(a => li(`${a.label} → ${c.st.done[a.id].result}`)).join('') || '<li>미실시</li>'}</ul>
      <h2>D8 비용 · 종결</h2><p>실손 ${DX.won(c.money.loss)} · 위험 ${DX.won(c.money.risk)} · 묶임 ${DX.won(c.money.tied)} · 낭비 ${DX.won(c.money.waste)}/일 (계수는 예시값)</p><ul>${c.money.basis.map(li).join('')}</ul>
      <h2>기록 (신뢰 원장 서명)</h2><ul>${c.st.log.slice().reverse().map(l => li(`${l.at} ${l.text} — ${l.by}`)).join('')}</ul></body></html>`);
    w.document.close();
    T.append('MLNG', 'case', c.id, `8D 보고서 출력 — ${c.name}`, { case: c.id, stage: c.st.stage });
  }

  M.mod({
    id: 'case', group: '판단 (3.0)', order: 3,
    menu: [['cases', '품질 이슈', 'case']],
    views: V,
    drawers: { 'case:': id => { cur = id; msg = ''; M.closeDrawer(); M.go('cases'); } },
    bind(root) {
      root.querySelectorAll('[data-csel]').forEach(b => b.onclick = () => { cur = b.dataset.csel; msg = ''; M.render(); });
      root.querySelectorAll('[data-crun]').forEach(b => b.onclick = async e => { e.stopPropagation(); const [cid, aid] = b.dataset.crun.split('|'); b.disabled = true;
        try { msg = await M.caseApi.run(cid, aid); } catch (er) { msg = '실패 — ' + er.message; } if (M.cur === 'cases') M.render(); });
      root.querySelectorAll('[data-cadv]').forEach(b => b.onclick = async () => { const c = M.caseApi.get(b.dataset.cadv); const note = c.st.stage === 1 && c.st.cause != null ? '원인: ' + c.causes[c.st.cause].t : ''; await M.caseApi.advance(c.id, note); msg = `${STAGES[c.st.stage]} 단계 — 서명 기록`; M.render(); });
      root.querySelectorAll('[data-ccause]').forEach(b => b.onchange = async () => { const [cid, i] = b.dataset.ccause.split('|'), c = M.caseApi.get(cid); c.st.cause = +i;
        c.st.log.unshift({ at: T.stamp(), by: M.D.user().name, text: `원인 선택 — ${c.causes[+i].t}` }); await T.append('MLNG', 'case', cid, `이슈 원인 선택 — ${c.causes[+i].t}`, { case: cid, cause: c.causes[+i].t }); if (c.st.stage < 2) c.st.stage = 2; M.render(); });
      root.querySelectorAll('[data-call]').forEach(b => b.onclick = async () => { b.disabled = true; msg = await M.caseApi.runAll(b.dataset.call); M.render(); });
      root.querySelectorAll('[data-crep]').forEach(b => b.onclick = () => report(M.caseApi.get(b.dataset.crep)));
      root.querySelectorAll('[data-cfirst]').forEach(b => b.onclick = () => { const c = M.caseApi.get(b.dataset.cfirst); if (!M.replayApi) return; b.disabled = true; M.$('#cfirst').textContent = '되감는 중…';
        setTimeout(() => { const r = M.replayApi.firstSeen(a => TPL[c.tpl].match(a) && a.level !== 'info'), cg = M.ml ? M.ml.changeFor(c.tpl) : null;
          const lead = cg && cg.day && r ? Math.round((M.toDate(r.day).getTime() - M.toDate(cg.day).getTime()) / 86400000) : null;
          c.st.first = (r ? `규칙 알람 처음 ${r.day} (${r.lead}일 전${r.capped ? ' · 시점 조회 범위 처음부터' : ''}) — ${r.title}` : '시점 조회 범위 안에서 규칙 알람 없음')
            + (cg ? ` · 통계적 변화 시작 ${cg.day} — ${cg.what} (p = ${f(cg.p, 3)})${lead != null && lead > 0 ? ` → 규칙 알람보다 ${lead}일 먼저 알 수 있었음` : ''}` : ''); M.render(); }, 20); });
      root.querySelectorAll('[data-askq]').forEach(b => b.onclick = e => { e.stopPropagation(); if (M.askApi) M.askApi.ask(b.dataset.askq); });
    },
  });
  M.PROC.case = '품질 이슈';
})();
