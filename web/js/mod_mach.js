/* MCT 가공 모듈 — 수직형 머시닝센터 MC-01 (정면 밀링)
 * 데이터: NASA Ames / UC Berkeley Milling Data Set (공구 16개 마모 시험, 가공 167회, 센서 6종)
 * 화면: MCT 현황 · 공구 수명 · 가공 조건   /  상세: 공구 서랍
 * MILG 위험도: data/ai_model.json 의 mct (python -m mlng_ai export-mct) — 그 공구를 빼고 학습한 엔진이 가공마다 매긴 값
 */
(function () {
  'use strict';
  const M = window.MLNG, { esc, f, pct } = M;
  const MC = () => Object.assign({ VB_LIMIT: 0.5, VB_WARN: 0.35, LATE_RATIO: 1.2 }, M.CFG.MACH || {});
  const D = () => M.S.mach || { tools: [], runs: [], conditions: [], signals: [], corr: {} };
  const RD = () => M.CFG.RECENT_DAYS || 7;
  const runsOf = id => D().runs.filter(r => r.tool === id);
  const cur = () => D().tools[D().tools.length - 1] || {};
  const since = () => M.sinceDay(RD()).getTime();
  const recentTools = () => D().tools.filter(t => M.toDate(t.date).getTime() >= since());
  const vbState = v => v == null ? ['', '—'] : v >= MC().VB_LIMIT ? ['danger', '교체 필요'] : v >= MC().VB_WARN ? ['warn', '주의'] : ['ok', '정상'];
  const vbTag = v => { const [c, t] = vbState(v); return c ? `<span class="tag j-${c}">${t}</span>` : '—'; };
  const late = t => t.vb_last != null && t.vb_last > MC().VB_LIMIT * MC().LATE_RATIO;
  const remain = t => { if (!t.rate || t.vb_last == null) return null; return (MC().VB_LIMIT - t.vb_last) / t.rate; };
  const overRuns = ts => ts.reduce((s, t) => s + runsOf(t.id).filter(r => r.vb_now != null && r.vb_now > MC().VB_LIMIT).length, 0);
  const sigLabel = k => (D().signals.find(s => s.key === k) || {}).label || k;

  // ── MILG 엔진 위험도 (가공별 · 그 공구는 학습에서 뺌)
  const AI = () => (M.aiModel && M.aiModel() || {}).mct || null;
  const aiT = id => { const a = AI(); return a && a.tools[id] || null; };
  const aiRun = (id, run) => { const a = aiT(id); return a && a.runs.find(r => r.run === run) || null; };
  const aiTop = r => r && r.top && r.top.length ? r.top.map(t => esc(t.label) + (t.rel ? ' <span class="muted">교체 후 변화</span>' : '')).join(' · ') : '<span class="muted">—</span>';
  // 알림 시점과 교체 기준 도달 시점의 관계 — [표시 종류, 문구]
  function aiLead(a) {
    if (!a) return ['', '—'];
    if (a.alert_run == null) return a.limit_run != null ? ['danger', '알림 없음'] : ['', '알림 없음'];
    if (a.limit_run == null) return ['ok', `${a.alert_run}회차 알림`];
    return a.lead != null ? ['ok', `${a.alert_run}회차 · 교체 기준 ${a.lead}회 전`] : ['warn', `${a.alert_run}회차 · 교체 기준 이후`];
  }
  const aiTag = a => { const [c, t] = aiLead(a); return c ? `<span class="tag j-${c}">${t}</span>` : `<span class="muted">${t}</span>`; };
  function riskChart(t, opt = {}) {
    const a = aiT(t.id), rs = runsOf(t.id); if (!a || rs.length < 2) return '<div class="empty">위험도 계산에 필요한 가공 기록이 부족합니다</div>';
    const v = rs.map(r => { const x = aiRun(t.id, r.run); return x ? x.p * 100 : null; });
    const xl = [0, Math.floor(rs.length / 2), rs.length - 1].filter((x, i, arr) => arr.indexOf(x) === i).map(i => [i, rs[i].run + '회차']);
    return M.lineChart([{ v, cls: 's1' }], null, Object.assign({ h: 200, d: 0, xlab: xl, yfix: [0, 100 / 1.08], refs: [{ y: a.thr * 100, label: `알림 기준 ${f(a.thr * 100, 0)}%` }],
      dots: rs.map((r, i) => { const x = aiRun(t.id, r.run); return x && x.al ? { i, y: x.p * 100, r: 3.4, cls: 'bad', t: `${r.run}회차 · 위험도 ${f(x.p * 100, 0)}% · VB ${f(r.vb_now, 2)}` } : null; }).filter(Boolean) }, opt));
  }
  function aiSummary() {
    const a = AI(); if (!a) return '';
    const o = a.ops;
    return `<dl class="stat"><dt>먼저 알림</dt><dd>교체 기준 도달 ${o.over}개 중 ${o.caught}개</dd><dt>알림 시점</dt><dd>교체 기준 ${f(o.lead_min, 0)}~${f(o.lead_max, 0)}회 전 · 중앙값 ${f(o.lead_median, 0)}회</dd>
      <dt>오경보</dt><dd>정상 가공의 ${pct(o.false_alarm, 0)}</dd><dt>구분력</dt><dd>${f(a.auc, 2)}</dd><dt>검증</dt><dd>공구 단위로 빼고 평가</dd><dt>입력</dt><dd>센서 ${D().signals.length}종 · 가공 조건</dd></dl>`;
  }

  function alerts() {
    const out = [], t = cur(), at = (runsOf(t.id).slice(-1)[0] || {}).t || M.S.asof;
    if (t.id && t.vb_last != null && t.vb_last >= MC().VB_LIMIT) out.push({ key: 'mach.tool_over', data: { tool: t.id, vb: t.vb_last }, proc: 'mach', level: 'danger', title: `${t.id} 공구 교체 — 마모 VB ${f(t.vb_last, 2)} mm (기준 ${f(MC().VB_LIMIT, 2)})`,
      body: `${t.material} · 절삭깊이 ${t.DOC} mm · 이송 ${t.feed} mm/rev · 마모 속도 ${f((t.rate || 0) * 1000, 1)} µm/분 — 다음 가공 전 인서트 교체`, by: 'MCT', at, go: 'mach:' + t.id });
    const lt = recentTools().filter(late);
    if (lt.length) { const n = overRuns(lt); out.push({ key: 'mach.late', data: { tools: lt.map(x => x.id), runs: n }, proc: 'mach', level: 'warn', title: `최근 ${RD()}일 교체 기준을 넘겨 가공한 공구 ${lt.length}개 — ${n}회 가공`,
      body: `${lt.map(x => `${x.id} VB ${f(x.vb_last, 2)}`).join(' · ')} → 해당 가공 부품 약 ${f(n * D().parts_per_run, 0)}개 검사 강화 권장`, by: 'MCT', at: lt[lt.length - 1].date, go: 'mach_tools' }); }
    const bad = recentTools().filter(x => x.sensor_bad);
    if (bad.length) out.push({ key: 'mach.sensor', data: { tools: bad.map(x => x.id) }, proc: 'mach', level: 'warn', title: `MCT 센서 값 비정상 ${bad.reduce((s, x) => s + x.sensor_bad, 0)}회`,
      body: `${bad.map(x => x.id).join(' · ')} 가공 중 센서 값이 범위를 벗어남 — 센서 배선·수집 설정 확인 (해당 가공은 마모 추정에서 제외)`, by: '데이터 점검', at: bad[bad.length - 1].date, go: 'mach_cond' });
    return out;
  }
  function summary() {
    const t = cur(), [c] = vbState(t.vb_last), rt = recentTools();
    return { kpi: t.id ? `공구 VB ${f(t.vb_last, 2)}` : '—', sub: `${t.id || ''} · 최근 ${RD()}일 ${rt.reduce((s, x) => s + x.runs, 0)}회 가공 · ${f(rt.reduce((s, x) => s + x.runs, 0) * D().parts_per_run, 0)}개`,
      st: c === 'danger' ? 'danger' : c === 'warn' ? 'warn' : 'ok', go: 'mach_home' };
  }

  // 공구 한 개의 마모 곡선 (가공 시간 → VB): 측정값 점 + 현재 마모 선 + 기준선
  function wearChart(t, opt = {}) {
    const rs = runsOf(t.id); if (rs.length < 2) return '<div class="empty">가공 기록이 1회뿐입니다</div>';
    const xl = [0, Math.floor(rs.length / 2), rs.length - 1].filter((v, i, a) => a.indexOf(v) === i).map(i => [i, f(rs[i].minute, 0) + '분']);
    return M.lineChart([{ v: rs.map(r => r.vb_now), cls: 's1' }, { v: rs.map(r => r.vb_est), cls: 's4' }], null, Object.assign({ h: 220, d: 2, xlab: xl,
      refs: [{ y: MC().VB_LIMIT, label: `교체 ${MC().VB_LIMIT}` }, { y: MC().VB_WARN, label: '주의', cls: 'soft' }],
      dots: rs.map((r, i) => r.vb != null ? { i, y: r.vb, r: 3.4, cls: r.vb >= MC().VB_LIMIT ? 'bad' : '', t: `${r.run}회차 · ${r.minute}분 · VB ${r.vb}` } : null).filter(Boolean) }, opt));
  }

  const V = {};
  V.mach_home = () => {
    const t = cur(), rt = recentTools(), runsN = rt.reduce((s, x) => s + x.runs, 0), rm = remain(t), lt = rt.filter(late);
    const rates = D().tools.filter(x => x.rate).slice(-10);
    return `
    <div class="kstrip">
      <div class="kpi click" data-row="mach:${esc(t.id)}"><div class="lab">현재 공구 ${esc(t.id || '')} 마모</div><div class="val" style="color:${vbState(t.vb_last)[0] === 'danger' ? 'var(--danger-ink)' : vbState(t.vb_last)[0] === 'warn' ? 'var(--warn-ink)' : 'inherit'}">${f(t.vb_last, 2)}<small>mm</small></div><div class="foot">플랭크 마모 VB · 교체 기준 ${f(MC().VB_LIMIT, 2)} mm</div></div>
      <div class="kpi click" data-row="mach:${esc(t.id)}"><div class="lab">남은 공구 수명</div><div class="val">${rm == null ? '—' : rm <= 0 ? '<span style="color:var(--danger-ink)">교체</span>' : f(rm, 0) + '<small>분</small>'}</div><div class="foot">${t.rate ? `마모 속도 ${f(t.rate * 1000, 1)} µm/분 기준` : '측정값 부족'}</div></div>
      <div class="kpi click" data-row="k:mc:runs"><div class="lab">가공 · 최근 ${RD()}일</div><div class="val">${runsN}<small>회</small></div><div class="foot">공구 ${rt.length}개 · 부품 약 ${f(runsN * D().parts_per_run, 0)}개</div></div>
      <div class="kpi click" data-go="mach_tools"><div class="lab">교체 기준 넘겨 가공</div><div class="val" style="color:${lt.length ? 'var(--warn-ink)' : 'inherit'}">${overRuns(rt)}<small>회</small></div><div class="foot">공구 ${lt.length}개 · 부품 약 ${f(overRuns(rt) * D().parts_per_run, 0)}개 검사 강화</div></div>
    </div>
    <div class="grid g21" style="margin-top:16px">
      <div class="card"><h2>현재 공구 마모 <span class="h2sub">${esc(t.id || '')} · ${esc(t.material || '')} · 절삭깊이 ${t.DOC} mm · 이송 ${t.feed} mm/rev · ${t.runs}회 ${f(t.minutes, 0)}분</span> <a class="more" href="#" data-row="mach:${esc(t.id)}">공구 상세 →</a></h2>
        ${t.id ? wearChart(t) : ''}${M.legend([['l-s1', '현재 마모 (측정·보간)'], ['l-s4', '신호로 추정한 마모'], ['l-bad', '측정값'], ['l-lim', '교체·주의 기준']])}</div>
      <div class="card"><h2>공구별 마모 속도 <span class="h2sub">µm/분 · 최근 10개</span></h2>
        <div class="hbars">${rates.map(x => `<div class="hb"><span>${esc(x.id)} ${esc(x.material)}</span><div><i style="width:${x.rate / Math.max(...rates.map(y => y.rate)) * 100}%;${x.material === '스테인리스강' ? 'background:var(--warn)' : ''}"></i></div><b>${f(x.rate * 1000, 1)}</b><em></em></div>`).join('')}</div>
        <p class="note">주황 = 스테인리스강. 같은 조건이면 주철보다 마모가 몇 배 빨라 교체 주기를 짧게 잡아야 합니다 → 가공 조건 화면.</p></div>
    </div>
    ${AI() && t.id ? `<div class="grid g21" style="margin-top:16px">
      <div class="card"><h2>MILG 위험도 <span class="h2sub">${esc(t.id)} · 주의 기준(VB ${f(AI().vb, 2)} mm) 도달 확률 · ${aiLead(aiT(t.id))[1]}</span> <a class="more" href="#" data-row="mach:${esc(t.id)}">공구 상세 →</a></h2>
        ${riskChart(t)}${M.legend([['l-s1', '위험도 (%)'], ['l-bad', '알림 기준 초과'], ['l-lim', '알림 기준']])}</div>
      <div class="card"><h2>MILG 엔진 검증 <span class="h2sub">공구 ${AI().n_tools}개 · 가공 ${AI().n_runs}회</span> <a class="more" href="#" data-go="xai">기준선 비교 →</a></h2>${aiSummary()}</div>
    </div>` : ''}
    <div class="card" style="margin-top:16px"><h2>최근 공구 <a class="more" href="#" data-go="mach_tools">공구 수명 →</a></h2>${toolTable(D().tools.slice(-5).reverse(), true)}</div>`;
  };

  function toolTable(rows, compact) {
    const head = [['공구', t => `<b>${esc(t.id)}</b>`], ['작업일', 'date'], ['가공 LOT', t => `<a href="#" data-trace="${esc(t.lot)}">${esc(t.lot)}</a>`], ['재질', 'material'],
      ['절삭깊이', t => f(t.DOC, 2), 'n'], ['이송', t => f(t.feed, 2), 'n'], ['가공', t => t.runs + '회', 'n'], ['절삭 시간', t => f(t.minutes, 0) + '분', 'n'],
      ['최종 VB', t => f(t.vb_last, 2), 'n'], ['마모 속도', t => t.rate ? f(t.rate * 1000, 1) + ' µm/분' : '—', 'n'],
      ['교체', t => t.vb_last == null || !t.rate ? '<span class="muted">—</span>' : late(t) ? '<span class="tag j-warn">늦은 교체</span>' : '<span class="tag j-ok">적정</span>'],
      ['MILG 알림', t => aiTag(aiT(t.id))],
      ['센서', t => t.sensor_bad ? `<span class="tag j-warn">이상 ${t.sensor_bad}</span>` : '<span class="muted">정상</span>']];
    return M.table(compact ? head.filter(h => !['가공 LOT', '센서'].includes(h[0])) : head, rows, { click: t => 'mach:' + t.id, hl: late });
  }
  V.mach_tools = () => {
    const ts = D().tools.slice().reverse(), lt = D().tools.filter(late);
    return `<div class="card"><div class="tools"><span class="tsum" style="margin-left:0">공구 ${ts.length}개 · 가공 ${D().runs.length}회 · 늦은 교체 ${lt.length}개 (VB ${f(MC().VB_LIMIT * MC().LATE_RATIO, 2)} mm 초과) · 1회 가공 = 부품 ${D().parts_per_run}개</span></div>
      ${toolTable(ts)}<p class="note">행을 누르면 공구 상세(마모 곡선·센서 추이)가 열립니다. «가공 LOT» 은 앞 공정(열처리)·뒤 공정(검사)과 이어진 LOT 입니다.${AI() ? ` «MILG 알림» = 위험도가 2회 연속 알림 기준을 넘은 회차 (그 공구를 빼고 학습한 엔진 기준).` : ''}</p></div>`;
  };

  V.mach_cond = () => {
    const C = D().conditions, mx = Math.max(...C.map(c => c.rate)), lim = MC().VB_LIMIT;
    const per = M.median(D().tools.flatMap(t => { const rs = runsOf(t.id); return rs.slice(1).map((r, i) => r.minute - rs[i].minute); }).filter(x => x > 0)) || 5;
    const ci = C.filter(c => c.material === '주철'), ss = C.filter(c => c.material === '스테인리스강');
    const ratio = ci.length && ss.length ? (ss.reduce((s, c) => s + c.rate, 0) / ss.length) / (ci.reduce((s, c) => s + c.rate, 0) / ci.length) : null;
    const corr = Object.entries(D().corr).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
    return `<div class="card"><h2>가공 조건별 공구 마모 <span class="h2sub">${esc(D().machine || '')}</span></h2>
      ${M.table([['재질', 'material'], ['절삭깊이 (mm)', c => f(c.DOC, 2), 'n'], ['이송 (mm/rev)', c => f(c.feed, 2), 'n'],
        ['마모 속도', c => `<span class="eff"><i class="pos" style="width:${c.rate / mx * 100}%"></i></span> <b>${f(c.rate * 1000, 1)}</b> µm/분`],
        ['교체까지 예상', c => `${f(lim / c.rate, 0)}분`, 'n'], ['교체까지 가공', c => `약 ${f(lim / c.rate / per, 0)}회`, 'n'], ['공구 수', 'n', 'n']], C, { wrap: false })}
      <p class="note">교체까지 예상 = 교체 기준 VB ${lim} mm ÷ 마모 속도. 가공 1회 ≈ ${f(per, 1)}분(데이터 중앙값) 기준.${ratio ? ` 스테인리스강은 주철보다 평균 <b>${f(ratio, 1)}배</b> 빨리 마모 → 공구 교체 주기·재고를 재질별로 따로 잡아야 합니다.` : ''}</p></div>
    <div class="grid g21" style="margin-top:16px">
      <div class="card"><h2>마모와 함께 움직인 센서 <span class="h2sub">가공 1회 RMS · 순위상관</span></h2>
        <div class="hbars">${corr.map(([k, v]) => `<div class="hb"><span>${esc(sigLabel(k))}</span><div><i style="width:${Math.abs(v) * 100}%;${v < 0 ? 'background:var(--c2)' : ''}"></i></div><b>${v > 0 ? '+' : ''}${f(v, 2)}</b><em></em></div>`).join('')}</div>
        <p class="note">주축 전류가 마모와 가장 강하게 같이 움직입니다. 측정하지 않은 가공의 마모는 이 신호와 가공 조건으로 추정합니다 (공구 단위 검증 오차 ±${f(D().est_mae, 2)} mm).</p></div>
      <div class="card"><h2>데이터 점검</h2><dl class="stat"><dt>센서 값 비정상 가공</dt><dd>${D().n_bad}회</dd><dt>VB 측정 가공</dt><dd>${D().runs.filter(r => r.vb != null).length} / ${D().runs.length}회</dd>
        <dt>추정으로 채운 가공</dt><dd>${D().runs.filter(r => r.vb == null && r.vb_now != null).length}회</dd></dl>
        <p class="note">비정상 값(무한대·과대·0)이 나온 가공은 마모 추정에서 빼고 알람으로 알립니다.</p></div>
    </div>`;
  };

  function toolDrawer(id) {
    const t = D().tools.find(x => x.id === id); if (!t) return;
    const rs = runsOf(id), ai = aiT(id), lg = M.lineage(t.lot), heats = lg.up.filter(x => x.proc === 'heat'), ins = lg.down.filter(x => x.proc === 'insp');
    const xl = rs.length > 1 ? [[0, f(rs[0].minute, 0) + '분'], [rs.length - 1, f(rs[rs.length - 1].minute, 0) + '분']] : [];
    const sch = k => rs.length > 1 ? `<div class="pch"><div class="pt2">${esc(sigLabel(k))} <span class="muted">가공 1회 RMS</span></div>${M.lineChart([{ v: rs.map(r => r.s[k]), cls: 's2' }], null, { h: 110, w: 460, d: 2, xlab: xl })}</div>` : '';
    M.drawer(`MCT 가공 · MC-01 · ${t.date}`, `공구 ${t.id}`, `
      <div class="blk"><h4>판정</h4><dl class="stat"><dt>최종 마모 VB</dt><dd>${f(t.vb_last, 3)} mm ${vbTag(t.vb_last)}</dd><dt>마모 속도</dt><dd>${t.rate ? f(t.rate * 1000, 1) + ' µm/분' : '—'}</dd>
        <dt>교체</dt><dd>${late(t) ? '<span class="tag j-warn">늦은 교체</span>' : t.rate ? '<span class="tag j-ok">적정</span>' : '—'}</dd><dt>가공 조건</dt><dd>${esc(t.material)} · 깊이 ${t.DOC} · 이송 ${t.feed}</dd>
        <dt>가공</dt><dd>${t.runs}회 · ${f(t.minutes, 0)}분 · 부품 약 ${f(t.runs * D().parts_per_run, 0)}개</dd></dl></div>
      <div class="blk"><h4>이력</h4><dl class="stat"><dt>가공 LOT</dt><dd><a href="#" data-trace="${esc(t.lot)}">${esc(t.lot)}</a></dd>
        <dt>앞 공정 (열처리)</dt><dd>${heats.map(h => `<a href="#" data-row="heat:${esc(h.id)}">${esc(h.id)}</a>`).join(' · ') || '—'}</dd>
        <dt>뒤 공정 (검사)</dt><dd>${ins.map(h => `<a href="#" data-trace="${esc(h.id)}">${esc(h.id)}</a>`).join(' · ') || '—'}</dd></dl></div>
      <div class="blk"><h4>마모 곡선</h4>${wearChart(t, { w: 460, h: 180 })}</div>
      ${ai ? `<div class="blk"><h4>MILG 위험도</h4><dl class="stat"><dt>알림</dt><dd>${aiTag(ai)}</dd><dt>기준 도달</dt><dd>주의 ${ai.warn_run != null ? ai.warn_run + '회차' : '—'} · 교체 ${ai.limit_run != null ? ai.limit_run + '회차' : '—'}</dd>
        <dt>원인 후보</dt><dd>${aiTop(aiRun(id, ai.alert_run))}</dd></dl>${riskChart(t, { w: 460, h: 160 })}</div>` : ''}
      <div class="blk"><h4>센서 추이</h4>${sch('smcDC')}${sch('AE_table')}${sch('vib_spindle')}</div>
      <div class="blk"><h4>가공 기록</h4>${M.table([['회차', 'run', 'n'], ['시각', r => esc(r.t.slice(11))], ['누적', r => r.minute + '분', 'n'], ['VB 측정', r => r.vb == null ? '<span class="muted">—</span>' : f(r.vb, 2), 'n'],
        ['VB 추정', r => r.vb_est == null ? '—' : f(r.vb_est, 2), 'n'], ...(ai ? [['위험도', r => { const x = aiRun(id, r.run); return x ? (x.al ? `<b style="color:var(--danger-ink)">${f(x.p * 100, 0)}%</b>` : f(x.p * 100, 0) + '%') : '<span class="muted">—</span>'; }, 'n']] : []), ['상태', r => r.bad ? '<span class="tag j-warn">센서 이상</span>' : vbTag(r.vb_now)]], rs, { wrap: false })}
        <button class="btn pri" data-insp="mach|${esc(t.lot)}|${esc(t.id)}" style="margin-top:8px">검사 결과 기록</button></div>`);
  }

  // ── 요약 띠 상세
  function kd(k) {
    if (k !== 'runs') return;
    const rt = recentTools().slice().reverse(), n = rt.reduce((s, x) => s + x.runs, 0);
    return M.kdrawer(`MCT 가공 · 최근 ${RD()}일`, `공구 ${rt.length}개 · ${n}회 가공 · 부품 약 ${f(n * D().parts_per_run, 0)}개`, [
      { html: M.table([['공구', x => `<b>${esc(x.id)}</b>`], ['작업일', 'date'], ['재질', 'material'], ['가공', x => x.runs + '회', 'n'], ['부품', x => f(x.runs * D().parts_per_run, 0), 'n'], ['최종 VB', x => f(x.vb_last, 2), 'n'], ['상태', x => vbTag(x.vb_last)]], rt, { click: x => 'mach:' + x.id, wrap: false }) },
      { html: `<p class="note">가공 1회 = 부품 ${D().parts_per_run}개. 행을 누르면 공구 마모 곡선과 센서 추이가 열립니다.</p>` }]);
  }

  M.mod({
    id: 'mach', proc: 'mach', group: 'MCT 가공', order: 40,
    menu: [['mach_home', 'MCT 현황', 'gauge'], ['mach_tools', '공구 수명', 'list'], ['mach_cond', '가공 조건', 'range']],
    views: V, alerts, summary,
    drawers: { 'k:mc:': kd, 'mach:': id => { if (id.startsWith('MC-')) { const t = D().tools.find(x => x.lot === id); return t ? toolDrawer(t.id) : M.go('mach_tools'); } return toolDrawer(id); } },
  });
})();
