/* MILG AI 위젯 — 기존 MES 화면은 그대로, 우측 하단 버튼을 누르면 분석 패널이 열린다
 * ───────────────────────────────────────────────────────────────
 * 패널 흐름: 감지 → 원인 후보 → 영향 → 근거 → 확인 제안 → 담당자 처리(조사 등록 · 보류 · 상세 분석)
 * 데이터: data/ai_model.json (공정 통합 AI 결과물). 처리 기록은 이 브라우저에만 저장 (데모).
 * 다른 MES 에 붙일 때는 같은 패널을 띄우고, 보고 있는 LOT 을 MILG.aiContext(lotId) 로 넘긴다.
 */
(function () {
  'use strict';
  const M = window.MLNG, { esc, f, pct } = M, CFG = window.MLNG_CONFIG || {};
  const UNIT = Number(CFG.AI_UNIT_COST || 15000);
  const KEY = 'milg-ai-actions';
  const st = { open: false, i: 0, ctx: '' };
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

  function items() {
    const A = M.aiModel && M.aiModel();
    if (!A) return [];
    const it = [];
    (A.parts.insp || []).filter(r => (r.p ?? 0) >= 0.5).forEach(r => it.push(Object.assign({ k: 'insp', kind: '부품' }, r)));
    Object.entries(A.lots || {}).forEach(([k, rs]) => rs.filter(r => r.y > 0 && r.ratio >= 1.5).forEach(r => it.push(Object.assign({ k, kind: 'LOT' }, r))));
    return it.sort((a, b) => (b.p ?? 0) - (a.p ?? 0) || b.ratio - a.ratio);
  }
  const open = () => items().filter(r => !acts[r.id]);

  function panel() {
    const A = M.aiModel && M.aiModel(), all = items();
    if (!A) return '<div class="aiw-empty">분석 결과 불러오는 중…</div>';
    if (!all.length) return '<div class="aiw-empty">감지된 항목 없음</div>';
    let i = st.ctx ? all.findIndex(r => r.id === st.ctx || r.lot === st.ctx) : st.i;
    if (i < 0) i = 0;
    st.i = Math.min(i, all.length - 1);
    const r = all[st.i], P = A.procs[r.k] || {}, src = (A.sources || []).find(s => s.key === r.k) || {}, g = P.gate ?? 1;
    const top = (r.top || []).filter(t => t.slot !== 'up:has' && t.c > 0).slice(0, 2), sk = s => (A.slot_ko || {})[s] || s;
    const base = r.p != null ? 1 - Math.exp(-(P.base_rate || 0)) : null;
    const bad = r.p != null ? 1 : r.y;
    const det = r.p != null ? `불량 확률 ${r.p >= 0.99 ? '≥99%' : pct(r.p, 0)} · 공정 평균 ${pct(base, 1)}` : `불량 ${f(r.y, 0)}개 · 예상 ${f(r.exp, 1)}개 (${f(r.ratio, 1)}배)`;
    const a = acts[r.id];
    return `<div class="aiw-ctx"><b class="mono">${esc(r.id)}</b><span>${esc((P.name || r.k).replace(/ \(.*\)/, ''))} · ${r.kind}</span>
        <span class="aiw-nav"><button data-aiw="prev" aria-label="이전">‹</button><small>${st.i + 1} / ${all.length}</small><button data-aiw="next" aria-label="다음">›</button></span></div>
      <table class="aiw-t"><tbody>
        <tr><th>감지</th><td>${det}</td></tr>
        <tr><th>원인 후보</th><td>${top.length ? top.map(t => `${esc(sk(t.slot))}${t.src ? ` <span class="muted">${esc(t.src)}</span>` : ''}${t.x ? ` · 표준편차 ${f(t.x, 1)}배` : ''}`).join('<br>') : '<span class="muted">두드러진 조건 없음</span>'}</td></tr>
        <tr><th>영향</th><td>불량 ${f(bad, 0)}개 · ${f(bad * UNIT, 0)}원</td></tr>
        <tr><th>근거</th><td>${esc(src.name ? src.name.replace(/ \(.*\)/, '') : r.k)} ${f(src.rows || 0, 0)}건 비교 · 모델 신뢰 ${f(g, 2)}</td></tr>
        <tr><th>확인 제안</th><td>${top.length ? [...new Set(top.map(t => hint(t.slot)))].map(esc).join('<br>') : '설정 변경 이력 확인'}</td></tr>
      </tbody></table>
      ${g < 0.5 ? '<div class="aiw-warn">모델 신뢰 낮음 · 참고용</div>' : ''}
      ${a ? `<div class="aiw-done"><span class="tag ${a.s === 'reg' ? 'j-ok' : 'j-none'}">${a.s === 'reg' ? '조사 등록' : '보류'}</span><span class="muted">${esc(a.at)}</span><button class="btn sm" data-aiw="undo">취소</button></div>`
        : `<div class="aiw-act"><button class="btn sm pri" data-aiw="reg">조사 등록</button><button class="btn sm" data-aiw="hold">보류</button><button class="btn sm" data-aiw="more">상세 분석</button></div>`}`;
  }

  function draw() {
    let root = document.getElementById('aiw');
    if (!root) {
      root = document.createElement('div');
      root.id = 'aiw';
      document.body.appendChild(root);
      root.addEventListener('click', onClick);
    }
    const n = open().length;
    root.innerHTML = `<button class="aiw-fab" data-aiw="toggle" aria-expanded="${st.open}" aria-label="MILG AI 분석 패널">MILG AI${n ? `<span class="aiw-badge">${n}</span>` : ''}</button>
      ${st.open ? `<section class="aiw-panel" role="dialog" aria-label="MILG AI 분석"><header><b>MILG AI</b><span class="muted">원인 후보 분석</span><button data-aiw="toggle" aria-label="닫기">×</button></header>${panel()}</section>` : ''}`;
  }

  function onClick(e) {
    const b = e.target.closest('[data-aiw]');
    if (!b) return;
    const a = b.dataset.aiw, all = items(), cur = all[st.i];
    if (a === 'toggle') { st.open = !st.open; if (st.open && M.aiLoad) M.aiLoad(); }
    if (a === 'prev') { st.ctx = ''; st.i = (st.i - 1 + all.length) % all.length; }
    if (a === 'next') { st.ctx = ''; st.i = (st.i + 1) % all.length; }
    if ((a === 'reg' || a === 'hold') && cur) { acts[cur.id] = { s: a, at: new Date().toLocaleString('ko-KR', { hour12: false }).slice(0, -3) }; save(); }
    if (a === 'undo' && cur) { delete acts[cur.id]; save(); }
    if (a === 'more' && cur) { if (M.aiPick) M.aiPick(cur.id); M.nav('tx_lots'); }
    draw();
  }

  // 다른 화면 · 다른 MES 에서 보고 있는 LOT 을 넘긴다
  M.aiContext = id => { st.ctx = String(id || ''); st.open = true; if (M.aiLoad) M.aiLoad(); draw(); };
  M.aiWidget = { draw, items, open: () => { st.open = true; draw(); } };
  const start = () => { if (M.aiLoad) M.aiLoad(); draw(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else setTimeout(start, 0);
})();
