/* MILG AI 위젯 — 기존 MES 화면은 그대로, 우측 하단 «MILG | AI» 버튼을 누르면 분석 패널이 열린다
 * ───────────────────────────────────────────────────────────────
 * 패널: 분석 요약(감지 → 원인 후보 → 근거 → 영향 → 확인 제안) · 상세 근거 · 조치 이력 → 담당자 처리(조사 등록 · 보류 · 상세 분석)
 * 데이터: data/ai_model.json — MILG 엔진이 실제로 계산한 결과물 (mct = 그 공구를 빼고 학습한 엔진이 가공마다 매긴 위험도).
 *         화면은 이 값을 그대로 그린다 (지어낸 값 없음). 처리 기록은 이 브라우저에만 저장 (데모).
 * 다른 MES 에 붙일 때는 같은 패널을 띄우고, 보고 있는 LOT 을 MILG.aiContext(lotId) 로 넘긴다.
 */
(function () {
  'use strict';
  const M = window.MLNG, { esc, f, pct } = M, CFG = window.MLNG_CONFIG || {};
  const UNIT = Number(CFG.AI_UNIT_COST || 15000);
  const KEY = 'milg-ai-actions';
  const st = { open: false, i: 0, ctx: '', tab: 'sum' };
  let acts = {};
  try { acts = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { acts = {}; }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(acts)); } catch (e) { /* 저장 불가 환경 */ } };

  const HINT = [
    [/^up:/, '앞 공정 LOT 조건 확인'],
    [/acc:|tool|count|exposure/, '공구 사용 횟수 · 교체 주기 확인'],
    [/load|current|speed/, '공구 교체 이력 · 가공 조건 변경 확인'],
    [/temperature|time/, '온도 기록 · 유지 시간 확인'],
    [/pressure|force/, '압력 설정 · 금형 상태 확인'],
    [/vibration|wave:rough/, '설비 진동 · 고정 상태 확인'],
    [/text:/, '작업 메모 · 이상 보고 확인'],
    [/cat_rare/, '작업자 · 자재 LOT · 설비 변경 확인'],
  ];
  const hint = s => (HINT.find(([re]) => re.test(s)) || [null, '해당 조건의 설정 변경 이력 확인'])[1];
  const pc = p => p >= 0.99 ? '≥99%' : pct(p, 0);
  const short = s => String(s || '').split(' (')[0];
  const dev = x => x == null ? '조건 이탈' : `평소보다 ${x < 0 ? '낮음' : '높음'} · 표준편차 ${f(Math.abs(x), 1)}배`;
  const now = () => { const d = new Date(), z = n => String(n).padStart(2, '0'); return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}`; };

  function items() {
    const A = M.aiModel && M.aiModel();
    if (!A) return [];
    const it = [];
    // MCT 공구: 최근 공구 중 위험도 알림이 난 것 (그 공구를 빼고 학습한 엔진 기준)
    const mc = A.mct, mach = (M.S && M.S.mach) || {}, since = M.sinceDay ? M.sinceDay((M.CFG || {}).RECENT_DAYS || 7).getTime() : 0;
    if (mc) (mach.tools || []).filter(t => M.toDate(t.date).getTime() >= since && mc.tools[t.id] && mc.tools[t.id].alert_run != null).reverse().forEach(t => {
      const a = mc.tools[t.id], r = a.runs.find(x => x.run === a.alert_run) || {};
      it.push({ k: 'mct', kind: '공구', id: t.id, lot: t.lot, p: r.p, ratio: 0, tool: t, ai: a, run: r });
    });
    (A.parts.insp || []).filter(r => (r.p ?? 0) >= 0.5).forEach(r => it.push(Object.assign({ k: 'insp', kind: '부품' }, r)));
    Object.entries(A.lots || {}).forEach(([k, rs]) => rs.filter(r => r.y > 0 && r.ratio >= 1.5).forEach(r => it.push(Object.assign({ k, kind: 'LOT' }, r))));
    return it.sort((a, b) => (b.k === 'mct') - (a.k === 'mct') || (a.k === 'mct' ? 0 : (b.p ?? 0) - (a.p ?? 0) || b.ratio - a.ratio));
  }
  const open = () => items().filter(r => !acts[r.id]);

  // ── 근거 그림 (엔진이 계산한 값 그대로)
  // 공구: 가공 회차별 위험도 · 알림 기준 · 교체 기준 도달 회차
  function riskSvg(a) {
    const rs = a.runs, n = rs.length, W = 396, H = 132, L = 30, R = 10, T = 10, B = 22;
    if (n < 2) return '';
    const x = i => L + (W - L - R) * i / (n - 1), y = v => T + (H - T - B) * (1 - v);
    const ix = run => rs.findIndex(r => r.run === run);
    const line = rs.map((r, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(r.p).toFixed(1)}`).join('');
    const ia = ix(a.alert_run), il = a.limit_run != null ? ix(a.limit_run) : -1;
    const ticks = [0, Math.floor((n - 1) / 2), n - 1].filter((v, i, arr) => arr.indexOf(v) === i);
    return `<svg class="aiw-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="가공 회차별 위험도">
      ${[0, 0.5, 1].map(v => `<line class="g" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="t" x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${v * 100}</text>`).join('')}
      ${ia >= 0 ? `<rect class="band" x="${x(ia).toFixed(1)}" y="${T}" width="${(x(il > ia ? il : n - 1) - x(ia)).toFixed(1)}" height="${H - T - B}"/>` : ''}
      <line class="thr" x1="${L}" x2="${W - R}" y1="${y(a.thr).toFixed(1)}" y2="${y(a.thr).toFixed(1)}"/>
      ${il >= 0 ? `<line class="lim" x1="${x(il).toFixed(1)}" x2="${x(il).toFixed(1)}" y1="${T}" y2="${H - B}"/>` : ''}
      <path class="ln" d="${line}"/>
      ${rs.map((r, i) => `<circle class="${r.al ? 'al' : 'pt'}" cx="${x(i).toFixed(1)}" cy="${y(r.p).toFixed(1)}" r="${r.al ? 3.2 : 2}"><title>${r.run}회차 · 위험도 ${pc(r.p)}</title></circle>`).join('')}
      ${ticks.map(i => `<text class="t" x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="${i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}">${rs[i].run}회차</text>`).join('')}
    </svg>`;
  }
  // LOT · 부품: 원인 후보별 기여
  function barSvg(top, name) {
    const W = 396, RH = 24, L = 150, R = 44, mx = Math.max(...top.map(t => t.c), 0.01);
    return `<svg class="aiw-chart" viewBox="0 0 ${W} ${top.length * RH + 4}" role="img" aria-label="원인 후보별 기여">
      ${top.map((t, i) => `<text class="lb" x="0" y="${i * RH + 16}">${esc(name(t).slice(0, 14))}</text><rect class="bg" x="${L}" y="${i * RH + 7}" width="${W - L - R}" height="10" rx="3"/>
        <rect class="${i ? 'b2' : 'b1'}" x="${L}" y="${i * RH + 7}" width="${Math.max(2, (W - L - R) * t.c / mx).toFixed(1)}" height="10" rx="3"/><text class="v" x="${W}" y="${i * RH + 16}" text-anchor="end">${f(t.c, 2)}</text>`).join('')}
    </svg>`;
  }

  // ── 항목 → 패널 내용 (공구 · LOT · 부품 모두 같은 틀)
  function model(A, r) {
    if (r.k === 'mct') {
      const mc = A.mct, a = r.ai, t = r.tool, o = mc.ops, per = ((M.S.mach || {}).parts_per_run) || 0, top = (r.run.top || []).slice(0, 3);
      const left = a.limit_run != null ? a.lead : null;
      return {
        proc: 'MCT 가공 · 공구', status: ['j-warn', '검토 필요'], src: 'MILG 엔진 · ' + (mc.built || ''),
        head: '공구 교체 임박 신호 감지', det: `${a.alert_run}회차 위험도 ${pc(r.run.p)} · 알림 기준 ${pct(a.thr, 0)}`,
        cands: top.map(x => ({ name: x.label, sub: `${x.rel ? '교체 후 변화 · ' : ''}${dev(x.x)}`, hi: x.c >= 1, c: x.c })),
        chart: riskSvg(a), chartT: '가공 회차별 위험도 (%)', chartR: a.limit_run != null ? `교체 기준 ${a.limit_run}회차` : '교체 기준 미도달',
        legend: [['ln', '위험도'], ['thr', `알림 기준 ${pct(a.thr, 0)}`], ...(a.limit_run != null ? [['lim', '교체 기준 도달']] : [])],
        impact: left != null ? `교체 기준까지 ${left}회 · 부품 약 ${f(left * per, 0)}개` : a.limit_run != null ? `교체 기준(${a.limit_run}회차) 이후 알림` : '교체 기준 미도달',
        basis: `다른 공구 ${mc.n_tools - 1}개 학습 · 구분력 ${f(mc.auc, 2)} · 정상 가공 알림 ${pct(o.false_alarm, 0)}`,
        suggest: ['공구 날 마모 측정 · 교체 준비', `${t.material || ''} · 절삭깊이 ${t.DOC} · 이송 ${t.feed} 조건 확인`],
        detail: [['엔진', mc.engine], ['검증', mc.method], ['정답 기준', mc.label + ` · 교체 기준 VB ${f(mc.limit, 2)} mm`], ['알림', `학습 공구 정상 가공 95% 분위 · ${mc.alert_rule}`],
          ['입력', '센서 · 가공 조건 (공구 누적 사용량 제외)'], ['구분력 (AUC)', f(mc.auc, 2)], ['먼저 알림', `교체 기준 도달 ${o.over}개 중 ${o.caught}개`],
          ['알림 시점', o.lead_median != null ? `교체 기준 ${f(o.lead_min, 0)}~${f(o.lead_max, 0)}회 전` : '—'], ['정상 가공 알림', pct(o.false_alarm, 0)], ['학습 규모', `공구 ${mc.n_tools}개 · 가공 ${mc.n_runs}회`], ['계산', mc.built], ['출처', short(mc.source)]],
        runs: a.runs.filter(x => x.al).slice(0, 6).map(x => [`${x.run}회차`, `위험도 ${pc(x.p)}`]),
      };
    }
    const P = A.procs[r.k] || {}, src = (A.sources || []).find(s => s.key === r.k) || {}, g = P.gate ?? 1, sk = s => (A.slot_ko || {})[s] || s;
    const top = (r.top || []).filter(t => t.slot !== 'up:has' && t.c > 0).slice(0, 3);
    const base = r.p != null ? 1 - Math.exp(-(P.base_rate || 0)) : null, bad = r.p != null ? 1 : r.y;
    return {
      proc: `${short(P.name || r.k)} · ${r.kind}`, status: g < 0.5 ? ['j-none', '참고용'] : ['j-warn', '검토 필요'], src: `공정 통합 AI · 모델 신뢰 ${f(g, 2)}`,
      head: r.p != null ? '불량 가능성 높은 부품 감지' : '예상보다 많은 불량 감지',
      det: r.p != null ? `불량 확률 ${pc(r.p)} · 공정 평균 ${pct(base, 1)}` : `불량 ${f(r.y, 0)}개 · 예상 ${f(r.exp, 1)}개 (${f(r.ratio, 1)}배)`,
      cands: top.map(t => ({ name: sk(t.slot), sub: `${t.src ? t.src + ' · ' : ''}${dev(t.x || null)}`, hi: t.c >= 0.5, c: t.c })),
      chart: top.length ? barSvg(top, t => short(sk(t.slot))) : '', chartT: '원인 후보별 기여', chartR: '클수록 불량 쪽', legend: [],
      impact: `불량 ${f(bad, 0)}개 · ${f(bad * UNIT, 0)}원`,
      basis: `${esc(short(src.name || r.k))} ${f(src.rows || 0, 0)}건 비교 · 모델 신뢰 ${f(g, 2)}`,
      suggest: top.length ? [...new Set(top.map(t => hint(t.slot)))] : ['설정 변경 이력 확인'],
      detail: [['모델', `${A.version || ''} · ${A.built || ''}`], ['공정', short(P.name || r.k)], ['비교 데이터', `${f(src.rows || 0, 0)}건`], ['모델 신뢰', f(g, 2)],
        ['공정 평균', P.base_rate != null ? pct(1 - Math.exp(-P.base_rate), 1) : '—'], ['출처', short(src.name || r.k)]],
      runs: [], low: g < 0.5,
    };
  }

  function panel() {
    const A = M.aiModel && M.aiModel(), all = items();
    if (!A) return '<div class="aiw-empty">분석 결과 불러오는 중…</div>';
    if (!all.length) return '<div class="aiw-empty">감지된 항목 없음</div>';
    let i = st.ctx ? all.findIndex(r => r.id === st.ctx || r.lot === st.ctx) : st.i;
    if (i < 0) i = 0;
    st.i = Math.min(i, all.length - 1);
    const r = all[st.i], v = model(A, r), act = acts[r.id], nh = Object.keys(acts).length;
    const sum = `<div class="aiw-alert"><i>!</i><b>${esc(v.head)}</b></div>
        <dl class="aiw-kv"><dt>감지</dt><dd>${v.det}</dd></dl>
        <div class="aiw-sec"><span>원인 후보</span><span>기여</span></div>
        ${v.cands.length ? v.cands.map((c, j) => `<div class="aiw-cand${j ? ' sub' : ''}"><div class="r"><i>${j + 1}</i><b>${esc(c.name)}</b><em class="${c.hi ? 'hi' : ''}">${c.hi ? '높음' : '검토'}</em></div><p>${esc(c.sub)}</p></div>`).join('') : '<div class="aiw-cand sub"><p class="none">두드러진 조건 없음</p></div>'}
        ${v.chart ? `<div class="aiw-sec"><span>${esc(v.chartT)}</span><span>${esc(v.chartR)}</span></div>${v.chart}${v.legend.length ? `<div class="aiw-lg">${v.legend.map(([c, t]) => `<span><i class="${c}"></i>${esc(t)}</span>`).join('')}</div>` : ''}` : ''}
        <dl class="aiw-kv line"><dt>영향</dt><dd>${v.impact}</dd><dt>근거</dt><dd>${v.basis}</dd><dt>확인 제안</dt><dd>${v.suggest.map(esc).join('<br>')}</dd></dl>
        ${v.low ? '<div class="aiw-warn">모델 신뢰 낮음 · 참고용</div>' : ''}`;
    const det = `<dl class="aiw-rows">${v.detail.map(([k, x]) => `<dt>${esc(k)}</dt><dd>${esc(x)}</dd>`).join('')}</dl>
        ${v.runs.length ? `<div class="aiw-sec"><span>알림 기준 초과 회차</span><span>위험도</span></div><dl class="aiw-rows">${v.runs.map(([k, x]) => `<dt>${esc(k)}</dt><dd>${esc(x)}</dd>`).join('')}</dl>` : ''}`;
    const hist = nh ? Object.entries(acts).sort((a, b) => String(b[1].at).localeCompare(String(a[1].at))).map(([id, a]) =>
      `<button class="aiw-h" data-aiw="goto" data-id="${esc(id)}"><b class="mono">${esc(id)}</b><span class="tag ${a.s === 'reg' ? 'j-ok' : 'j-none'}">${a.s === 'reg' ? '조사 등록' : '보류'}</span><small>${esc(a.at)}</small></button>`).join('') : '<div class="aiw-empty">처리 기록 없음</div>';
    const tab = (k, t) => `<button class="${st.tab === k ? 'on' : ''}" data-aiw="tab" data-tab="${k}" role="tab" aria-selected="${st.tab === k}">${t}</button>`;
    return `<div class="aiw-meta"><span>공정 분석</span><span><i></i>${esc(v.src)}</span></div>
      <div class="aiw-ctx"><div><b class="mono">${esc(r.id)}</b><small>${esc(v.proc)}</small></div><span class="tag ${v.status[0]}">${v.status[1]}</span>
        <span class="aiw-nav"><button data-aiw="prev" aria-label="이전">‹</button><small>${st.i + 1} / ${all.length}</small><button data-aiw="next" aria-label="다음">›</button></span></div>
      <div class="aiw-tabs" role="tablist">${tab('sum', '분석 요약')}${tab('det', '상세 근거')}${tab('hist', `조치 이력 ${nh}`)}</div>
      <div class="aiw-body">${st.tab === 'det' ? det : st.tab === 'hist' ? hist : sum}</div>
      <footer>${act ? `<div class="aiw-done"><span class="tag ${act.s === 'reg' ? 'j-ok' : 'j-none'}">${act.s === 'reg' ? '조사 등록' : '보류'}</span><span class="muted">${esc(act.at)}</span><button class="btn sm" data-aiw="undo">취소</button></div>`
        : `<div class="aiw-act"><button class="btn pri" data-aiw="reg">조사 등록</button><button class="btn" data-aiw="hold">보류</button><button class="btn" data-aiw="more">상세 분석</button></div>`}
        <p>원인 후보는 담당자 확인이 필요합니다</p></footer>`;
  }

  const WM = '<span class="aiw-wm">MILG</span><span class="aiw-ai">AI</span>';
  function draw() {
    let root = document.getElementById('aiw');
    if (!root) {
      root = document.createElement('div');
      root.id = 'aiw';
      document.body.appendChild(root);
      root.addEventListener('click', onClick);
    }
    const n = open().length, keep = root.querySelector('.aiw-body'), top = keep ? keep.scrollTop : 0;
    root.innerHTML = `<button class="aiw-fab" data-aiw="toggle" aria-expanded="${st.open}" aria-label="MILG AI 분석 패널">${WM}<span class="aiw-lbl">분석 패널</span>${n ? `<span class="aiw-badge">${n}</span>` : ''}</button>
      ${st.open ? `<section class="aiw-panel" role="dialog" aria-label="MILG AI 분석"><header><div class="aiw-brand">${WM}</div><button data-aiw="toggle" aria-label="닫기">×</button></header>${panel()}</section>` : ''}`;
    const body = root.querySelector('.aiw-body');
    if (body && st.keep) body.scrollTop = top;
    st.keep = false;
  }

  function onClick(e) {
    const b = e.target.closest('[data-aiw]');
    if (!b) return;
    const a = b.dataset.aiw, all = items(), cur = all[st.i];
    if (a === 'toggle') { st.open = !st.open; if (st.open && M.aiLoad) M.aiLoad(); }
    if (a === 'tab') st.tab = b.dataset.tab;
    if (a === 'prev') { st.ctx = ''; st.i = (st.i - 1 + all.length) % all.length; }
    if (a === 'next') { st.ctx = ''; st.i = (st.i + 1) % all.length; }
    if (a === 'goto') { const j = all.findIndex(r => r.id === b.dataset.id); if (j >= 0) { st.ctx = ''; st.i = j; st.tab = 'sum'; } }
    if ((a === 'reg' || a === 'hold') && cur) { acts[cur.id] = { s: a, at: now() }; save(); st.keep = true; }
    if (a === 'undo' && cur) { delete acts[cur.id]; save(); st.keep = true; }
    if (a === 'more' && cur) { if (cur.k === 'mct') M.nav('mach:' + cur.id); else { if (M.aiPick) M.aiPick(cur.id); M.nav('tx_lots'); } }
    draw();
  }

  // 다른 화면 · 다른 MES 에서 보고 있는 LOT 을 넘긴다
  M.aiContext = id => { st.ctx = String(id || ''); st.open = true; st.tab = 'sum'; if (M.aiLoad) M.aiLoad(); draw(); };
  M.aiWidget = { draw, items, open: () => { st.open = true; draw(); } };
  const start = () => { if (M.aiLoad) M.aiLoad(); draw(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else setTimeout(start, 0);
})();
