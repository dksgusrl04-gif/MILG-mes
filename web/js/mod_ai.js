/* 5.7 — 공정 통합 AI (ai/ 의 Python 모델이 학습한 결과를 보여 준다)
 * ───────────────────────────────────────────────────────────────
 * 학습은 Python (ai/mlng_ai · 서버), 화면은 결과물 web/data/ai_model.json 만 읽는다 → GitHub Pages 에서도 동작
 *   칼럼 해석     칼럼 이름 · 설명 · 단위 · 값 모양 → 역할 · 물리량 (처음 보는 이름 시험 정확도 포함)
 *   누적 학습     시간순 «예측 → 채점 → 학습», 공정마다 게이트(모델을 믿는 정도) · 공통 효과
 *   처음 보는 공정 다른 공정으로 미리 배운 모델 vs 그 공정만으로 처음부터 — 적은 데이터에서
 *   원인 추적     LOT · 부품 하나의 불량 기여(앞 공정 포함) + 정답을 심은 가상 데이터 검증
 */
(function () {
  'use strict';
  const M = window.MLNG, { esc, f, pct } = M;
  let A = window.MLNG_AI || null, loading = null, err = '';
  const st = { tab: 'schema', proc: 'heat', pick: '' };
  const load = () => {
    if (A || loading) return;
    loading = fetch('data/ai_model.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : Promise.reject(new Error(r.status))).then(j => { A = j; M.render(); })
      .catch(e => { err = '모델 결과물을 읽지 못했습니다 (' + e.message + ') — ai/ 에서 python -m mlng_ai train'; M.render(); });
  };
  const ROLE = { param: ['조건', 'j-ok'], category: ['범주', 'j-none'], outcome: ['결과', 'j-danger'], outcome_detail: ['결과 세부 · 제외', 'j-warn'], exposure: ['수량', 'j-none'],
    id: ['식별자', 'j-none'], time: ['시각', 'j-none'], setpoint: ['설정값', 'j-none'], ignore: ['사용 안 함', 'j-none'] };
  const QKO = q => (A.slot_ko['q:' + q] || q).replace(' 이탈', '');
  const SK = s => A.slot_ko[s] || s;
  const pname = k => (A.procs[k] || {}).name || k;
  const few = k => { const s = A.sources.find(x => x.key === k); return s && s.events < 10; };
  const bar = (v, cls) => `<span class="xbar"><i class="${cls || ''}" style="width:${Math.max(2, Math.min(100, v * 100)).toFixed(0)}%"></i></span>`;
  const sgn = v => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%';

  function kpis() {
    const nCols = Object.values(A.schema).reduce((s, x) => s + x.n_cols, 0), inj = A.injection || [], i2 = inj.find(r => r.effect === 2), i0 = inj.find(r => r.effect === 1);
    return `<div class="kstrip">
      <div class="kpi"><div class="lab">학습한 공정</div><div class="val">${A.sources.length}<small>개</small></div><div class="foot">칼럼 ${f(nCols, 0)}개 · ${f(A.sources.reduce((s, x) => s + x.rows, 0), 0)}행</div></div>
      <div class="kpi"><div class="lab">처음 보는 칼럼 해석</div><div class="val">${pct(A.schema_eval.holdout, 0)}</div><div class="foot">시험 칼럼 ${A.schema_eval.holdout_n}개</div></div>
      <div class="kpi"><div class="lab">누적 갱신</div><div class="val">${A.model_version}<small>회</small></div><div class="foot">입력 변화 감지 ${A.drift_n}회</div></div>
      <div class="kpi"><div class="lab">공정 간 원인 찾기</div><div class="val">${i2 ? pct(i2.detected, 0) : '—'}</div><div class="foot">${i0 ? `효과 없을 때 오경보 ${pct(i0.detected, 0)}` : '가상 데이터 검증'}</div></div>
    </div>`;
  }

  // ── 1. 칼럼 해석
  function vSchema() {
    const keys = Object.keys(A.schema), k = keys.includes(st.proc) ? st.proc : keys[0], S = A.schema[k], ev = A.schema_eval;
    return `<div class="card" style="margin-top:16px"><h2>칼럼 의미 해석 <span class="h2sub">${esc(pname(k))} · ${S.n_cols}개</span></h2>
      <div class="spc-sub">${M.seg('xa', 'proc|' + k, keys.map(x => ['proc|' + x, pname(x).replace(/ \(.*\)/, '')]))}</div>
      <div class="tbx"><table class="tb xtab"><colgroup><col class="c-col"><col><col class="c-role"><col class="c-q"><col class="c-cf"><col></colgroup>
        <thead><tr><th>칼럼</th><th>설명 · 단위</th><th>역할</th><th>물리량</th><th>신뢰도</th><th>근거</th></tr></thead>
        <tbody>${S.rows.map(r => { const [t, c] = ROLE[r.role] || [r.role, 'j-none']; return `<tr class="${r.ok === false ? 'hl' : ''}">
          <td class="mono">${esc(r.col)}</td><td class="muted">${esc(r.label || '')}${r.unit ? ` <small>[${esc(r.unit)}]</small>` : ''}</td>
          <td><span class="tag ${c}">${esc(t)}</span>${r.leak ? ' <span class="tag j-warn">누설</span>' : ''}</td>
          <td>${r.role === 'param' ? esc(r.quantity === 'unknown' ? '알 수 없음' : r.quantity === 'event' ? '사건(0/1)' : QKO(r.quantity)) + (r.stat === 'spread' ? ' <small class="muted">변동</small>' : '') : '<span class="muted">—</span>'}</td>
          <td>${bar(r.conf, r.conf < 0.5 ? 'lo' : '')}</td><td class="muted wrap">${esc((r.why || []).join(' · '))}</td></tr>`; }).join('')}</tbody></table></div>
      ${k === 'secom' ? '<p class="note">칼럼 이름이 없는 공정 — 590개 센서 중 일부만 표시. 이름 대신 값 분포(평소 대비 이탈)로만 학습합니다.</p>' : ''}</div>
    <div class="card" style="margin-top:16px"><h2>처음 보는 칼럼 이름 시험 <span class="h2sub">사출 · 주조 · 도장 · 용접 · 가공 · 조립 · 반도체</span><span class="more"><span class="tag ${ev.holdout >= 0.85 ? 'j-ok' : 'j-warn'}">${pct(ev.holdout, 1)} · ${ev.holdout_n}개</span></span></h2>
      <div class="tbx"><table class="tb xtab"><colgroup><col class="c-col"><col><col class="c-role"><col class="c-role"></colgroup>
        <thead><tr><th>틀린 칼럼</th><th>설명</th><th>해석</th><th>정답</th></tr></thead>
        <tbody>${ev.holdout_wrong.map(r => `<tr><td class="mono">${esc(r.column)}</td><td class="muted">${esc(r.label || '')}</td><td>${esc(r.role)} ${esc(r.quantity || '')}</td><td>${esc(r.truth)}</td></tr>`).join('')}</tbody></table></div>
      <p class="note">학습 데이터 칼럼은 사전을 만들면서 본 것이라 ${pct(ev.in_sample, 0)} — 실제 성능은 처음 보는 이름 기준으로 봅니다.</p></div>`;
  }

  // ── 2. 누적 학습
  function vLearn() {
    const rows = Object.entries(A.prequential), sh = A.shared.filter(r => r.slot !== 'up:has').slice(0, 8);
    return `<div class="card" style="margin-top:16px"><h2>시간순 누적 학습 <span class="h2sub">먼저 예측 → 채점 → 학습</span></h2>
      <div class="tbx"><table class="tb xtab"><colgroup><col><col class="c-n"><col class="c-n"><col class="c-n"><col class="c-n"><col class="c-gate"><col class="c-rk"></colgroup>
        <thead><tr><th>공정</th><th class="n">평가 행</th><th class="n">불량</th><th class="n">모델</th><th class="n">게이트 적용</th><th>모델 신뢰 (게이트)</th><th class="n">순위 지표</th></tr></thead>
        <tbody>${rows.map(([k, r]) => `<tr><td><b>${esc(pname(k))}</b>${few(k) ? ' <span class="tag j-none">데이터 부족</span>' : ''}</td><td class="n">${f(r.n_eval, 0)}</td><td class="n">${f(r.events, 0)}</td>
          <td class="n ${r.dev_skill < 0 ? 'ng' : ''}">${sgn(r.dev_skill)}</td><td class="n"><b class="${r.gated_skill < 0 ? 'ng' : ''}">${sgn(r.gated_skill)}</b></td>
          <td>${bar(r.gate, r.gate < 0.5 ? 'lo' : '')} <small>${f(r.gate, 2)}</small></td><td class="n">${r.auc != null ? 'AUC ' + f(r.auc, 2) : '순위상관 ' + f(r.spearman, 2)}</td></tr>`).join('')}</tbody></table></div>
      <p class="note">값 = 공정 기준율(지금까지의 불량률)보다 예측 오차(포아송 이탈도)를 줄인 비율. 게이트가 0 쪽이면 조건으로 불량이 설명되지 않는 공정 → 기준율로 물러나 예측을 망치지 않습니다.</p></div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h2>공통 효과 <span class="h2sub">모든 공정이 같이 배운 규칙 · 평소보다 1σ</span></h2>
        <div class="tbx"><table class="tb xtab"><colgroup><col><col class="c-n"><col class="c-ci"></colgroup><thead><tr><th>슬롯</th><th class="n">배수</th><th class="n">90% 구간</th></tr></thead>
        <tbody>${sh.map(r => `<tr><td>${esc(SK(r.slot))}</td><td class="n"><b>×${f(r.rr, 2)}</b></td><td class="n"><span class="ci ${r.lo > 1 || r.hi < 1 ? 'sig' : ''}">${f(r.lo, 2)}~${f(r.hi, 2)}</span></td></tr>`).join('')}</tbody></table></div></div>
      <div class="card"><h2>공정별 확실한 효과 <span class="h2sub">90% 구간이 1 을 벗어난 것</span></h2>
        <div class="tbx"><table class="tb xtab"><colgroup><col class="c-p2"><col><col class="c-n"><col class="c-ci"></colgroup><thead><tr><th>공정</th><th>슬롯</th><th class="n">배수</th><th class="n">90% 구간</th></tr></thead>
        <tbody>${Object.entries(A.procs).flatMap(([k, p]) => p.effects.slice(0, 3).map(r => `<tr><td>${esc(pname(k).replace(/ \(.*\)/, ''))}</td><td>${esc(SK(r.slot))}</td><td class="n"><b>×${f(r.rr, 2)}</b></td><td class="n"><span class="ci sig">${f(r.lo, 2)}~${f(r.hi, 2)}</span></td></tr>`)).join('') || '<tr><td colspan="4" class="empty">없음</td></tr>'}</tbody></table></div></div></div>`;
  }

  // ── 3. 처음 보는 공정
  function vCold() {
    const ks = Object.keys(A.cold_start || {});
    if (!ks.length) return '<div class="card" style="margin-top:16px"><div class="empty">검증 결과 없음 — python -m mlng_ai train (빠른 모드 아님)</div></div>';
    return `<div class="grid g2" style="margin-top:16px">${ks.map(k => { const rs = A.cold_start[k], z = rs[0], tr = rs.slice(1);
      const win = tr.filter(r => r.transfer > r.fresh).length;
      return `<div class="card"><h2>${esc(pname(k))} <span class="h2sub">시험 = 마지막 40%</span><span class="more"><span class="tag ${win >= tr.length / 2 ? 'j-ok' : 'j-warn'}">미리 배운 쪽 ${win}/${tr.length}</span></span></h2>
        ${M.lineChart([{ v: tr.map(r => r.transfer * 100), cls: 's1' }, { v: tr.map(r => r.fresh * 100), cls: 's4' }], null, { h: 170, d: 0, refs: [{ y: 0 }], xlab: tr.map((r, i) => [i, f(r.n_train, 0)]) })}
        ${M.legend([['sw-s1', '다른 공정으로 미리 배운 모델'], ['sw-s4', '이 공정만으로 처음부터']])}
        <p class="note">세로 = 이탈도 개선(%) · 가로 = 이 공정에서 학습한 행 수. 학습 0개로 매긴 위험 순위 지표 ${z.zero_shot_auc != null ? f(z.zero_shot_auc, 2) : '—'} (0.5 = 무작위).</p></div>`; }).join('')}</div>`;
  }

  // ── 4. 원인 추적
  function items() {
    const it = [];
    (A.parts.insp || []).forEach(r => it.push(Object.assign({ k: 'insp', kind: '부품' }, r)));
    Object.entries(A.lots).forEach(([k, rs]) => rs.filter(r => r.y > 0).forEach(r => it.push(Object.assign({ k, kind: 'LOT' }, r))));
    return it.sort((a, b) => (b.k === 'insp') - (a.k === 'insp') || (b.p ?? 0) - (a.p ?? 0) || b.ratio - a.ratio);
  }
  function vTrace() {
    const it = items(), cur = it.find(x => x.id === st.pick) || it[0];
    if (!cur) return '<div class="card"><div class="empty">설명할 LOT 이 없습니다</div></div>';
    const g = (A.procs[cur.k] || {}).gate ?? 1, mx = Math.max(0.2, ...cur.top.map(r => Math.abs(r.hi)), ...cur.top.map(r => Math.abs(r.lo)));
    const X = v => 50 + v / mx * 48;
    return `<div class="grid xtr" style="margin-top:16px">
      <div class="card"><h2>불량 LOT · 부품 <span class="h2sub">${it.length}건</span></h2>
        <div class="tbx xlist"><table class="tb xtab"><colgroup><col><col class="c-p2"><col class="c-n"></colgroup><thead><tr><th>번호</th><th>공정</th><th class="n">${'예측'}</th></tr></thead>
        <tbody>${it.slice(0, 60).map(r => `<tr class="click ${r.id === cur.id ? 'sel' : ''}" data-xa="pick|${esc(r.id)}"><td class="mono">${esc(r.id)}</td><td>${esc(pname(r.k).replace(/ \(.*\)/, ''))}</td>
          <td class="n">${r.p != null ? (r.p >= 0.99 ? '≥99%' : pct(r.p, 0)) : '×' + f(r.ratio, 1)}</td></tr>`).join('')}</tbody></table></div></div>
      <div class="card"><h2>${esc(cur.id)} <span class="h2sub">${esc(pname(cur.k))}${cur.lot && cur.lot !== cur.id ? ' · ' + esc(cur.lot) : ''}</span>
        <span class="more"><span class="tag ${g >= 0.5 ? 'j-ok' : 'j-warn'}">모델 신뢰 ${f(g, 2)}</span></span></h2>
        <div class="xsum">${cur.p != null ? `<div><span>불량 확률 (예측)</span><b>${cur.p >= 0.99 ? '≥ 99%' : pct(cur.p, 0)}</b></div><div><span>공정 평균</span><b>${pct(1 - Math.exp(-(A.procs[cur.k].base_rate || 0)), 1)}</b></div>`
          : `<div><span>실제 불량</span><b>${f(cur.y, 0)}개</b></div><div><span>예상</span><b>${f(cur.exp, 1)}개</b></div><div><span>실제 / 예상</span><b>×${f(cur.ratio, 1)}</b></div>`}</div>
        <h3 class="dh">불량을 키운 쪽 <span class="h2sub">log 기여 · 90% 구간</span></h3>
        <div class="xctr">${cur.top.filter(r => r.slot !== 'up:has').map(r => `<div class="xc ${r.slot.startsWith('up:') ? 'up' : ''}">
          <div class="xn"><b>${esc(SK(r.slot))}</b><small>${esc(r.src || '')}${r.x ? ` · 평소보다 ${f(r.x, 1)}σ` : ''}</small></div>
          <div class="xg"><i class="ci" style="left:${X(Math.min(r.lo, r.hi)).toFixed(1)}%;width:${Math.max(0.6, X(Math.max(r.lo, r.hi)) - X(Math.min(r.lo, r.hi))).toFixed(1)}%"></i><i class="pt ${r.c > 0 ? 'pos' : 'neg'}" style="left:${X(r.c).toFixed(1)}%"></i><i class="z"></i></div>
          <div class="xv ${r.lo > 0 ? 'sig' : ''}">${r.c >= 0 ? '+' : ''}${f(r.c, 2)}</div></div>`).join('')}</div>
        ${g < 0.5 ? '<p class="note">이 공정은 조건 기록으로 불량이 잘 설명되지 않아(게이트 낮음) 기여는 참고용입니다.</p>' : ''}</div></div>
    <div class="card" style="margin-top:16px"><h2>원인 추적 검증 <span class="h2sub">정답을 심은 가상 데이터 · 앞 공정 로 온도 → 뒤 공정 불량</span></h2>
      <div class="tbx"><table class="tb xtab"><colgroup><col><col class="c-gate"><col class="c-n"></colgroup><thead><tr><th>심은 효과 (1σ 당)</th><th>찾아낸 비율</th><th class="n">순위 (중앙값)</th></tr></thead>
      <tbody>${(A.injection || []).map(r => `<tr><td>${r.effect === 1 ? '효과 없음 (오경보 시험)' : '×' + f(r.effect, 1)}</td><td>${bar(r.detected, r.effect === 1 ? 'lo' : '')} <small>${pct(r.detected, 0)}</small></td><td class="n">${f(r.median_rank, 0)}위</td></tr>`).join('')}</tbody></table></div>
      <p class="note">MLNG 데모 공장의 LOT 연결은 서로 다른 공개 데이터를 이은 것이라 실제 공정 간 인과가 없습니다. 그래서 원인 추적 능력은 정답을 아는 가상 데이터로 따로 검증했습니다.</p></div>`;
  }

  const V = {};
  V.xai = () => {
    load();
    if (err) return `<div class="card"><div class="empty">${esc(err)}</div></div>`;
    if (!A) return '<div class="card"><div class="empty">공정 통합 AI 결과를 불러오는 중…</div></div>';
    const body = { schema: vSchema, learn: vLearn, cold: vCold, trace: vTrace }[st.tab] || vSchema;
    return `${kpis()}<div class="spc-sub" style="margin-top:12px">${M.seg('xa', 'tab|' + st.tab, [['tab|schema', '칼럼 해석'], ['tab|learn', '누적 학습'], ['tab|cold', '처음 보는 공정'], ['tab|trace', '원인 추적']])}
      <span class="muted" style="font-size:12px;margin-left:auto">${esc(A.version)} · ${esc(A.built)}</span></div>${body()}`;
  };
  M.aiModel = () => A;
  M.mod({
    id: 'xai', group: '분석', order: 5.7,
    menu: [['xai', '공정 통합 AI', 'trend']],
    views: V,
    bind(root) { root.querySelectorAll('[data-xa]').forEach(b => b.onclick = e => { e.preventDefault(); const s = b.dataset.xa, i = s.indexOf('|'); st[s.slice(0, i)] = s.slice(i + 1); M.render(); }); },
  });
})();
