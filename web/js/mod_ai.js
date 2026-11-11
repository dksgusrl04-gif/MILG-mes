/* 5.7 — 공정 통합 AI (ai/ 의 Python 모델이 학습한 결과를 보여 준다)
 * ───────────────────────────────────────────────────────────────
 * 학습은 Python (ai/mlng_ai · 서버), 화면은 결과물 web/data/ai_model.json 만 읽는다 → GitHub Pages 에서도 동작
 *   칼럼 해석     칼럼 이름 · 설명 · 단위 · 값 모양 → 역할 · 물리량 (처음 보는 이름 시험 정확도 포함)
 *   누적 학습     시간순 «예측 → 채점 → 학습», 공정마다 게이트(모델을 믿는 정도) · 공통 효과
 *   처음 보는 공정 다른 공정으로 미리 배운 모델 vs 그 공정만으로 처음부터 — 적은 데이터에서
 *   원인 추적     LOT · 부품 하나의 불량 기여(앞 공정 포함) + 정답을 심은 가상 데이터 검증
 *   데이터 진단   고객 CSV → 칼럼 해석 · 품질 · 불량 원인 후보 · 손실(원). 기본은 데모 결과, 자체 서버가 있으면 업로드해 바로 진단
 */
(function () {
  'use strict';
  const M = window.MLNG, { esc, f, pct } = M;
  let A = window.MLNG_AI || null, loading = null, err = '';
  const st = { tab: 'schema', proc: 'heat', pick: '' };
  const dx = { r: null, busy: false, err: '', cost: '15000' };
  const load = () => {
    if (A || loading) return;
    loading = fetch('data/ai_model.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : Promise.reject(new Error(r.status))).then(j => { A = j; M.render(); if (M.aiWidget) M.aiWidget.draw(); })
      .catch(e => { err = '모델 결과물을 읽지 못했습니다 (' + e.message + ') — ai/ 에서 python -m mlng_ai train'; M.render(); });
  };
  const ROLE = { text: ['문장 (비정형)', 'j-ok'], param: ['조건', 'j-ok'], category: ['범주', 'j-none'], outcome: ['결과', 'j-danger'], outcome_detail: ['결과 세부 · 제외', 'j-warn'], exposure: ['수량', 'j-none'],
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
      <div class="kpi"><div class="lab">원인 추적 검증 (가상 데이터)</div><div class="val">${i2 ? pct(i2.detected, 0) : '—'}</div><div class="foot">${i0 ? `효과 없을 때 오경보 ${pct(i0.detected, 0)}` : '가상 데이터 검증'}</div></div>
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
          <td>${r.role === 'param' ? esc(r.quantity === 'unknown' ? '알 수 없음' : r.quantity === 'event' ? '사건(0/1)' : QKO(r.quantity)) + (r.stat === 'spread' ? ' <small class="muted">변동</small>' : r.stat === 'accum' ? ' <small class="muted">적산</small>' : '') : '<span class="muted">—</span>'}</td>
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
        <thead><tr><th>공정</th><th class="n">평가 행</th><th class="n">불량</th><th class="n">모델</th><th class="n">신뢰 반영</th><th>모델 신뢰 (게이트)</th><th class="n">순위 지표</th></tr></thead>
        <tbody>${rows.map(([k, r]) => `<tr><td><b>${esc(pname(k))}</b>${few(k) ? ' <span class="tag j-none">데이터 부족</span>' : ''}</td><td class="n">${f(r.n_eval, 0)}</td><td class="n">${f(r.events, 0)}</td>
          <td class="n ${r.dev_skill < 0 ? 'ng' : ''}">${sgn(r.dev_skill)}</td><td class="n"><b class="${r.gated_skill < 0 ? 'ng' : ''}">${sgn(r.gated_skill)}</b></td>
          <td>${bar(r.gate, r.gate < 0.5 ? 'lo' : '')} <small>${f(r.gate, 2)}</small></td><td class="n">${r.auc != null ? '구분력 ' + f(100 * r.auc, 0) + '점' : '순위상관 ' + f(r.spearman, 2)}</td></tr>`).join('')}</tbody></table></div>
      <p class="note">값 = 공정 기준율(지금까지의 불량률)보다 예측 오차(포아송 이탈도)를 줄인 비율. 게이트가 낮으면 기준율 예측의 비중을 높입니다. 평가 기간 전체에서 기준율보다 좋은 성능을 보장하지는 않습니다.</p></div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h2>공통 효과 <span class="h2sub">모든 공정이 같이 배운 규칙 · 평소보다 1σ</span></h2>
        <div class="tbx"><table class="tb xtab"><colgroup><col><col class="c-n"><col class="c-ci"></colgroup><thead><tr><th>신호</th><th class="n">배수</th><th class="n">90% 구간</th></tr></thead>
        <tbody>${sh.map(r => `<tr><td>${esc(SK(r.slot))}</td><td class="n"><b>${f(r.rr, 2)}배</b></td><td class="n"><span class="ci ${r.lo > 1 || r.hi < 1 ? 'sig' : ''}">${f(r.lo, 2)}~${f(r.hi, 2)}</span></td></tr>`).join('')}</tbody></table></div></div>
      <div class="card"><h2>공정별 확실한 효과 <span class="h2sub">90% 구간이 1 을 벗어난 것</span></h2>
        <div class="tbx"><table class="tb xtab"><colgroup><col class="c-p2"><col><col class="c-n"><col class="c-ci"></colgroup><thead><tr><th>공정</th><th>신호</th><th class="n">배수</th><th class="n">90% 구간</th></tr></thead>
        <tbody>${Object.entries(A.procs).flatMap(([k, p]) => p.effects.slice(0, 3).map(r => `<tr><td>${esc(pname(k).replace(/ \(.*\)/, ''))}</td><td>${esc(SK(r.slot))}</td><td class="n"><b>${f(r.rr, 2)}배</b></td><td class="n"><span class="ci sig">${f(r.lo, 2)}~${f(r.hi, 2)}</span></td></tr>`)).join('') || '<tr><td colspan="4" class="empty">없음</td></tr>'}</tbody></table></div></div></div>`;
  }

  // ── 3. 처음 보는 공정
  // MCT 공구 모델 — 기준선 비교(같은 검증 방식) + MES 데모 가공 기록에 적용한 결과
  function vMct() {
    const m = A.mct;
    if (!m) return '<div class="card"><div class="empty">MCT 공구 모델 결과 없음 — ai/ 에서 python -m mlng_ai export-mct</div></div>';
    const o = m.ops, ts = Object.entries(m.tools), win = r => r.boost == null || r.engine >= r.boost;
    const cell = (v, on) => v == null ? '<span class="muted">—</span>' : on ? `<b>${f(v, 2)}</b>` : f(v, 2);
    return `<div class="card"><h2>기준선 비교 <span class="h2sub">구분력(AUC) · 공구 누적 사용량 제외</span></h2>
        <div class="tbx"><table class="tb xtab"><colgroup><col><col class="c-rk"><col class="c-gate"><col class="c-n"><col class="c-n"><col class="c-n"><col class="c-rk"></colgroup>
        <thead><tr><th>데이터</th><th>규모</th><th>검증</th><th class="n">단순 규칙</th><th class="n">MILG 엔진</th><th class="n">범용 부스팅</th><th>엔진 입력</th></tr></thead><tbody>
        ${m.bench.map(r => `<tr><td>${esc(r.name)}</td><td>${esc(r.size)}</td><td>${esc(r.unit)}</td><td class="n">${cell(r.rule)}</td><td class="n">${cell(r.engine, win(r))}</td><td class="n">${cell(r.boost, !win(r))}</td><td>${esc(r.input)}${r.verification_status ? ' <span class="tag j-none">이전 평가 · 재검증 필요</span>' : ''}</td></tr>`).join('')}</tbody></table></div>
        <p class="note">굵게 = 더 높은 쪽. 범용 부스팅은 비교 기준 — 엔진 계산에는 들어가지 않습니다. 다른 설비로 옮기면 엔진 0.88 · 부스팅 0.97.</p></div>
      ${A.packs ? `<div class="card" style="margin-top:16px"><h2>사출 · 프레스 · 용접 학습 팩 검증 <span class="h2sub">${esc(A.packs.policy || "구분력(AUC) · 별도 운영 기준 검증 필요")} · ${esc(A.packs.built)}</span></h2>
        <div class="tbx"><table class="tb xtab"><colgroup><col class="c-n"><col><col class="c-rk"><col class="c-gate"><col class="c-n"><col class="c-n"><col class="c-n"><col class="c-n"><col></colgroup>
        <thead><tr><th>공정</th><th>데이터</th><th>규모</th><th>검증</th><th class="n">단순 규칙</th><th class="n">MILG 엔진</th><th class="n">범용 부스팅</th><th>불량 검출 / 정상 알림</th><th>사용 조건</th><th>판정</th></tr></thead><tbody>
        ${A.packs.rows.map(r => `<tr><td>${esc(r.proc)}</td><td>${esc(r.short || r.name)}</td><td>${esc(r.size)}</td><td>${esc(r.unit)}</td><td class="n">${cell(r.rule)}</td><td class="n">${cell(r.engine, r.engine >= r.boost)}</td><td class="n">${cell(r.boost, r.boost > r.engine)}</td>
          <td class="n">${r.recall == null ? "—" : pct(r.recall,0)} / ${r.false_alarm == null ? "—" : pct(r.false_alarm,0)}</td><td>${esc(r.license)}</td><td>${r.apply ? '<span class="tag j-ok">검증 통과 · 등록 상태 확인</span>' : `<span class="tag j-none">보류</span> <span class="muted">${esc(r.reason || '')}</span>`}</td></tr>`).join('')}</tbody></table></div>
        <p class="note">묶음(실험 · 조건 · 펀치 · 시간 구간)을 통째로 빼고 평가. 표의 과거 수치는 참고용입니다. 실시간 추론 등록과 현장 검증 여부는 엔진 상태에서 확인합니다. 학습에 쓰지 않은 데이터 ${A.packs.skipped.length}묶음 (불량 라벨 없음 · 조건 분류 · 사용 조건 미확인) — ai/reports/packs.md.</p></div>` : ''}
      <div class="grid g21" style="margin-top:16px">
        <div class="card"><h2>MES 가공 기록 적용 <span class="h2sub">${esc(m.engine)} · 정답 ${esc(m.label)}</span></h2>
          <div class="tbx"><table class="tb xtab"><colgroup><col class="c-n"><col class="c-n"><col class="c-n"><col class="c-n"><col class="c-n"><col></colgroup>
          <thead><tr><th>공구</th><th class="n">가공</th><th class="n">알림</th><th class="n">주의 기준</th><th class="n">교체 기준</th><th>결과</th></tr></thead><tbody>
          ${ts.map(([id, t]) => { const late = t.limit_run != null && t.lead == null; return `<tr class="click" data-row="mach:${esc(id)}"><td><b>${esc(id)}</b></td><td class="n">${t.runs.length}회</td><td class="n">${t.alert_run != null ? t.alert_run + '회차' : '—'}</td>
            <td class="n">${t.warn_run != null ? t.warn_run + '회차' : '—'}</td><td class="n">${t.limit_run != null ? t.limit_run + '회차' : '—'}</td>
            <td>${t.lead != null ? `<span class="tag j-ok">교체 기준 ${t.lead}회 전</span>` : late ? `<span class="tag j-danger">${t.alert_run != null ? '교체 기준 이후' : '알림 없음'}</span>` : '<span class="muted">교체 기준 미도달</span>'}</td></tr>`; }).join('')}</tbody></table></div>
          <p class="note">공구 하나를 통째로 빼고 나머지 공구로 학습한 엔진이 그 공구의 가공마다 위험도를 매깁니다. 알림 = ${esc(m.alert_rule)}. 행을 누르면 공구 상세가 열립니다.</p></div>
        <div class="card"><h2>요약</h2><dl class="stat"><dt>공구 · 가공</dt><dd>${m.n_tools}개 · ${m.n_runs}회</dd><dt>구분력 (AUC)</dt><dd>${f(m.auc, 2)}</dd>
          <dt>먼저 알림</dt><dd>교체 기준 도달 ${o.over}개 중 ${o.caught}개</dd><dt>놓침</dt><dd>${o.over - o.caught}개</dd><dt>알림 시점</dt><dd>교체 기준 ${f(o.lead_min, 0)}~${f(o.lead_max, 0)}회 전 · 중앙값 ${f(o.lead_median, 0)}회</dd>
          <dt>정상 가공 알림</dt><dd>${pct(o.false_alarm, 0)}</dd><dt>알림 기준</dt><dd>별도 검증 공구 정상 가공 95% 분위</dd><dt>계산</dt><dd>${esc(m.built)}</dd></dl>
          <p class="note">${esc(m.source)}</p></div>
      </div>`;
  }

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
  // ── 5. 적산 · 비정형 (파형 · 문장)
  let memo = '가공 중 주축 소음 있고 떨림 느낌 — 칩 색 이상';
  function vRaw() {
    const ch = A.channels || [], ab = A.ablation || {}, W = A.alarm_words || [];
    const hits = W.filter(w => memo.toLowerCase().includes(w));
    const mark = esc(memo).replace(new RegExp('(' + hits.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi'), hits.length ? '<mark>$1</mark>' : '$1');
    return `<div class="card" style="margin-top:16px"><h2>공정마다 쓰는 적산 · 비정형 정보</h2>
      <div class="tbx"><table class="tb xtab"><colgroup><col class="c-p3"><col><col><col><col class="c-q"></colgroup>
        <thead><tr><th>공정</th><th>적산 (리셋 단위 · 횟수)</th><th>적산 칼럼</th><th>파형</th><th>문장</th></tr></thead>
        <tbody>${A.sources.map(x => `<tr><td><b>${esc(x.name.replace(/ \(.*\)/, ''))}</b></td><td>${x.entity || x.resets > 1 ? `${esc(x.entity || (x.accum_cols.length ? '적산 값이 0 으로' : '교체 사건'))} · ${f(x.resets, 0)}회` : '<span class="muted">—</span>'}</td>
          <td>${x.accum_cols.length ? esc(x.accum_cols.join(', ')) : '<span class="muted">—</span>'}</td><td>${x.waves.length ? `${x.waves.length}종 <small class="muted">${esc(x.waves.slice(0, 2).join(', '))}${x.waves.length > 2 ? ' …' : ''}</small>` : '<span class="muted">—</span>'}</td>
          <td>${x.texts.length ? esc(x.texts.join(', ')) : '<span class="muted">—</span>'}</td></tr>`).join('')}</tbody></table></div></div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h2>정답을 심은 가상 데이터 <span class="h2sub">찾는가 · 오경보 없는가</span></h2>
        <div class="tbx"><table class="tb xtab"><colgroup><col><col class="c-n"><col class="c-gate"><col class="c-n"></colgroup><thead><tr><th>신호</th><th class="n">심음</th><th>찾아낸 비율</th><th class="n">AUC 포함 / 뺌</th></tr></thead>
        <tbody>${ch.map(r => { const none = r.group === '텍스트' ? !r.signal : r.signal === 1; return `<tr><td>${esc(SK(r.slot))}</td><td class="n">${none ? '<span class="muted">없음</span>' : '있음'}</td>
          <td>${bar(r.detected, none ? 'lo' : '')} <small>${pct(r.detected, 0)}</small></td><td class="n">${f(r.auc_with, 2)} / ${f(r.auc_without, 2)}</td></tr>`; }).join('')}</tbody></table></div>
        <p class="note">«없음» 줄 = 신호가 없는 데이터에서 잘못 찾은 비율(오경보). AUC 0.5 = 무작위.</p></div>
      <div class="card"><h2>문장 해석 미리보기 <span class="h2sub">이상 징후 단어</span></h2>
        <textarea id="xa-memo" class="xmemo" rows="3">${esc(memo)}</textarea>
        <div class="xmemo-out">${mark}</div>
        <div class="muted" style="font-size:12.5px;margin-top:6px">징후 단어 ${hits.length}개${hits.length ? ' — ' + esc(hits.join(', ')) : ''} · 학습된 위험도는 과거 메모와 불량을 함께 쌓아 계산 (서버 /ai/v1/update)</div></div></div>
    ${Object.entries(ab).map(([g, rs]) => `<div class="card" style="margin-top:16px"><h2>실제 데이터 제거 실험 — ${esc(g)} <span class="h2sub">이 묶음을 빼면</span></h2>
      <div class="tbx"><table class="tb xtab"><colgroup><col><col class="c-n"><col class="c-n"><col class="c-rk"></colgroup><thead><tr><th>공정</th><th class="n">포함</th><th class="n">뺌</th><th class="n">순위 지표 포함 → 뺌</th></tr></thead>
      <tbody>${Object.entries(rs).map(([k, r]) => `<tr><td>${esc(pname(k).replace(/ \(.*\)/, ''))}${few(k) ? ' <span class="tag j-none">데이터 부족</span>' : ''}</td><td class="n"><b class="${r.with - r.without > 0.02 ? 'okc' : ''}">${sgn(r.with)}</b></td><td class="n">${sgn(r.without)}</td><td class="n">${f(r.rank_with, 2)} → ${f(r.rank_without, 2)}</td></tr>`).join('')}</tbody></table></div></div>`).join('')}`;
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
          <td class="n">${r.p != null ? (r.p >= 0.99 ? '≥99%' : pct(r.p, 0)) : f(r.ratio, 1) + '배'}</td></tr>`).join('')}</tbody></table></div></div>
      <div class="card"><h2>${esc(cur.id)} <span class="h2sub">${esc(pname(cur.k))}${cur.lot && cur.lot !== cur.id ? ' · ' + esc(cur.lot) : ''}</span>
        <span class="more"><span class="tag ${g >= 0.5 ? 'j-ok' : 'j-warn'}">모델 신뢰 ${f(g, 2)}</span></span></h2>
        <div class="xsum">${cur.p != null ? `<div><span>불량 확률 (예측)</span><b>${cur.p >= 0.99 ? '≥ 99%' : pct(cur.p, 0)}</b></div><div><span>공정 평균</span><b>${pct(1 - Math.exp(-(A.procs[cur.k].base_rate || 0)), 1)}</b></div>`
          : `<div><span>실제 불량</span><b>${f(cur.y, 0)}개</b></div><div><span>예상</span><b>${f(cur.exp, 1)}개</b></div><div><span>실제 / 예상</span><b>${f(cur.ratio, 1)}배</b></div>`}</div>
        <h3 class="dh">불량을 키운 쪽 <span class="h2sub">log 기여 · 90% 구간</span></h3>
        <div class="xctr">${cur.top.filter(r => r.slot !== 'up:has').map(r => `<div class="xc ${r.slot.startsWith('up:') ? 'up' : ''}">
          <div class="xn"><b>${esc(SK(r.slot))}</b><small>${esc(r.src || '')}${r.x ? ` · 표준편차 ${f(r.x, 1)}배` : ''}</small></div>
          <div class="xg"><i class="ci" style="left:${X(Math.min(r.lo, r.hi)).toFixed(1)}%;width:${Math.max(0.6, X(Math.max(r.lo, r.hi)) - X(Math.min(r.lo, r.hi))).toFixed(1)}%"></i><i class="pt ${r.c > 0 ? 'pos' : 'neg'}" style="left:${X(r.c).toFixed(1)}%"></i><i class="z"></i></div>
          <div class="xv ${r.lo > 0 ? 'sig' : ''}">${r.c >= 0 ? '+' : ''}${f(r.c, 2)}</div></div>`).join('')}</div>
        ${g < 0.5 ? '<p class="note">이 공정은 조건 기록으로 불량이 잘 설명되지 않아(게이트 낮음) 기여는 참고용입니다.</p>' : ''}</div></div>
    <div class="card" style="margin-top:16px"><h2>원인 추적 검증 <span class="h2sub">정답을 심은 가상 데이터 · 앞 공정 로 온도 → 뒤 공정 불량</span></h2>
      <div class="tbx"><table class="tb xtab"><colgroup><col><col class="c-gate"><col class="c-n"></colgroup><thead><tr><th>심은 효과 크기</th><th>찾아낸 비율</th><th class="n">순위 (중앙값)</th></tr></thead>
      <tbody>${(A.injection || []).map(r => `<tr><td>${r.effect === 1 ? '효과 없음 (오경보 시험)' : f(r.effect, 1) + '배'}</td><td>${bar(r.detected, r.effect === 1 ? 'lo' : '')} <small>${pct(r.detected, 0)}</small></td><td class="n">${f(r.median_rank, 0)}위</td></tr>`).join('')}</tbody></table></div>
      <p class="note">MILG 데모 공장의 LOT 연결은 서로 다른 공개 데이터를 이은 것이라 실제 공정 간 인과가 없습니다. 그래서 원인 추적 능력은 정답을 아는 가상 데이터로 따로 검증했습니다.</p></div>`;
  }

  // ── 6. 데이터 진단 (영업 PoC)
  const ROLEKO = { id: '식별자', time: '시간', outcome: '결과(불량)', outcome_detail: '결과 상세', exposure: '수량', category: '범주', param: '공정 조건', setpoint: '설정값', ignore: '제외', text: '메모 · 문장' };
  const aiUrl = () => ((M.cfg && M.cfg.AI_URL) || ((window.MLNG_CONFIG || {}).AI_URL) || ((window.MLNG_RUNTIME || {}).SUPABASE_URL || '')).replace(/\/$/, '');
  async function runDx(file) {
    if (!aiUrl()) { dx.err = 'AI 엔진 서버가 연결되지 않았습니다 (자체 서버 · 설치형에서 사용) — 지금은 데모 결과'; M.render(); return; }
    dx.busy = true; dx.err = ''; M.render();
    try {
      if (file.size > 5e6) throw new Error('파일이 너무 큽니다 (5MB 이하)');
      if (dx.cost && (!Number.isFinite(Number(dx.cost)) || Number(dx.cost) < 0)) throw new Error('불량 1건당 비용을 0 이상의 숫자로 입력하세요');
      const u8 = new Uint8Array(await file.arrayBuffer()); let bin = '';
      for (let i = 0; i < u8.length; i += 0x8000) bin += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
      const res = await fetch(aiUrl() + '/ai/v1/diagnose', { method: 'POST', headers: { 'content-type': 'application/json' },   // CSV(UTF-8 · CP949) · 엑셀 그대로 → 서버가 판별
        body: JSON.stringify({ file_b64: btoa(bin), unit_cost: dx.cost ? Number(dx.cost) : null, name: file.name }) });
      if (!res.ok) throw new Error(res.status === 401 ? '인증된 분석 환경에 연결해야 합니다. 공장 담당자에게 접속 환경을 확인하세요.' : res.status === 404 ? 'AI 엔진 서버가 연결되지 않았습니다 (자체 서버 · 설치형에서 사용)' : ((await res.json().catch(() => ({}))).message || res.status));
      dx.r = await res.json();
    } catch (e) { dx.err = String(e.message || e); }
    dx.busy = false; M.render();
  }
  function vDiag() {
    const source = dx.r || A.diagnose_sample;
    if (!source) return '<div class="card" style="margin-top:16px"><div class="empty">진단 결과가 없습니다 — ai/ 에서 python -m mlng_ai train</div></div>';
    const unitCost = dx.cost.trim() !== '' && Number.isFinite(Number(dx.cost)) && Number(dx.cost) >= 0 ? Number(dx.cost) : null;
    const r = Object.assign({}, source, { unit_cost: unitCost, copq: unitCost != null && source.events != null ? { total: source.events * unitCost } : null });
    const q = r.quality, fs = r.factors || [], ai = r.ai || {}, mx = Math.max(1, ...fs.map(x => x.lift)), won = v => f(v, 0) + '원';
    const head = `<div class="card" style="margin-top:16px"><h2>${dx.r ? esc(r.name) : '정밀가공 검사 · 공개 데이터 예시'}
        <span class="more">${aiUrl() ? `<label class="btn sm">${dx.busy ? '진단 중…' : '파일 진단 (CSV · 엑셀)'}<input id="xa-dx" type="file" accept=".csv,.xlsx,.xls,text/csv" hidden ${dx.busy ? 'disabled' : ''}></label>` : '<span class="tag j-none">샘플 보기 · 파일 분석 미연결</span>'}</span></h2>
      <div class="xdx-in"><span class="tag ${dx.r ? 'j-ok' : 'j-none'}">${dx.r ? '업로드한 파일' : '공개 데이터 데모'}</span><span class="muted">${esc(r.created)}</span><label>불량 1건당 비용 <input id="xa-cost" class="inp" inputmode="decimal" value="${esc(dx.cost)}"> 원 (가정)</label>${dx.err ? `<span class="tag j-warn">${esc(dx.err)}</span>` : ''}${dx.r ? ' <a href="#" data-xa="dxr|1">데모 결과로</a>' : ''}</div>
      <div class="trace-status"><span class="tag j-warn">원인 미확정</span><span>검토 전 초안</span><details class="ui-details"><summary>분석 기준</summary><p>구간별 연관성을 비교한 조사 자료입니다. 현장 검증 뒤 원인을 확정합니다. 비용은 불량 수 × 입력한 건당 비용이며 실제 절감액이 아닙니다.</p></details></div>
      <div class="tbx"><table class="tb xtab xsumt"><thead><tr><th class="n">행</th><th class="n">칼럼</th><th class="n">불량률</th><th class="n">불량 비용 추정</th><th class="n">파일 점검</th><th>결과 칼럼</th><th>기간</th></tr></thead>
        <tbody><tr><td class="n">${f(q.rows, 0)}</td><td class="n">${q.cols}</td><td class="n">${r.defect_rate != null ? pct(r.defect_rate, 1) : '—'}</td><td class="n">${r.copq ? won(r.copq.total) : '—'}</td>
          <td class="n">${r.readiness.score} / 100</td><td>${esc(r.outcome || '없음')}</td><td>${q.time_range ? esc(q.time_range.join(' ~ ')) : '—'}</td></tr></tbody></table></div>
      </div>`;
    const fac = `<div class="card" style="margin-top:16px"><h2>불량 원인 후보 <span class="h2sub">칼럼별 구간 불량률 · z ≥ 2</span></h2>
      ${fs.length ? `<div class="tbx"><table class="tb xtab"><colgroup><col class="c-n"><col><col><col class="c-n"><col class="c-n"><col class="c-gate"><col style="width:120px"></colgroup>
        <thead><tr><th class="n">순위</th><th>칼럼</th><th>관측 구간</th><th class="n">구간 불량률</th><th class="n">나머지</th><th>배수</th><th class="n">${r.unit_cost != null ? '차이 수량 × 비용' : '구간 차이 수량'}</th></tr></thead>
        <tbody>${fs.map((x, i) => `<tr><td class="n">${i + 1}</td><td>${esc(x.label)}</td><td class="mono">${esc(x.worst)}</td><td class="n">${pct(x.worst_rate, 1)}</td><td class="n">${pct(x.rest_rate, 1)}</td>
          <td>${bar(x.lift / mx)} <small>${f(x.lift, 1)}배</small></td><td class="n">${r.unit_cost != null ? won(x.excess * r.unit_cost) : f(x.excess, 1)}</td></tr>`).join('')}</tbody></table></div>
      <details class="ui-details"><summary>산정 근거 · 해석 범위</summary><p>탐색 비교(z ≥ 2, 다중 비교 미보정). 구간 차이 수량 = (구간 불량률 − 나머지 불량률) × 구간 수량. 후보 간 중복되므로 합산할 수 없으며 조치 효과·절감액을 뜻하지 않습니다.</p></details>` : '<div class="empty">유의한 후보 없음 · 표본과 누락 조건 확인 필요</div>'}</div>`;
    const sig = (ai.signals || []).length ? `<div class="card"><h2>AI 신호 <span class="h2sub">학습 0건 · 공통 효과${ai.zero_shot_auc != null ? ' · 구분력 ' + f(100 * ai.zero_shot_auc, 0) + '점' : ''}</span></h2>
      <div class="tbx"><table class="tb xtab"><colgroup><col><col><col class="c-n"></colgroup><thead><tr><th>신호</th><th>주로 나온 칼럼</th><th class="n">평소 대비 (표준편차)</th></tr></thead>
      <tbody>${ai.signals.map(x => `<tr><td>${esc(x.name)}</td><td>${esc(x.source || '—')}</td><td class="n">+${f(x.gap_sd, 2)}</td></tr>`).join('')}</tbody></table></div></div>` : '';
    const rd = `<div class="card"><h2>데이터 준비도 <span class="h2sub">${r.readiness.score} / 100</span></h2>
      <div class="tbx"><table class="tb xtab"><colgroup><col><col class="c-role"><col class="c-n"></colgroup><thead><tr><th>항목</th><th>상태</th><th class="n">배점</th></tr></thead>
      <tbody>${r.readiness.checks.map(c => `<tr><td>${esc(c.item)}</td><td><span class="tag ${c.ok ? 'j-ok' : 'j-danger'}">${c.ok ? '충족' : '부족'}</span></td><td class="n">${c.weight}</td></tr>`).join('')}</tbody></table></div><details class="ui-details"><summary>점검 기준</summary><p>파일의 기본 점검 점수입니다. 모델 성능이나 현장 적용 가능성을 보증하지 않습니다. 결과·수량·단위·측정 시점은 담당자 확인이 필요합니다.</p></details></div>`;
    const cols = `<div class="card" style="margin-top:16px"><h2>칼럼 해석 <span class="h2sub">${r.columns.length}개</span></h2>
      <div class="tbx"><table class="tb xtab"><colgroup><col class="c-col"><col class="c-role"><col class="c-n"><col></colgroup><thead><tr><th>칼럼</th><th>역할</th><th class="n">확신도</th><th>근거</th></tr></thead>
      <tbody>${r.columns.map(c => `<tr><td class="mono">${esc(c.column)}</td><td><span class="tag ${(ROLE[c.role] || ['', 'j-none'])[1]}">${esc(ROLEKO[c.role] || c.role)}</span></td><td class="n">${f(c.confidence, 2)}</td><td class="muted">${esc(c.why)}</td></tr>`).join('')}</tbody></table></div></div>`;
    const d8 = r.report_8d, e8 = d8 && d8.d4.length ? `<div class="card" style="margin-top:16px"><h2>원인 분석 보고서 초안 (8D)</h2>
      <div class="tbx"><table class="tb xtab"><colgroup><col class="c-p3"><col></colgroup><thead><tr><th>항목</th><th>내용</th></tr></thead><tbody>
        <tr><td>D2 문제</td><td>${f(d8.d2.units, 0)}개 중 불량 ${f(d8.d2.events, 0)}건 (${pct(d8.d2.rate, 1)})${r.copq ? ' · 비용 추정 ' + won(r.copq.total) : ''}</td></tr>
        ${d8.d4.map((x, i) => `<tr><td>D4 원인 후보 ${i + 1}</td><td>${esc(x.column)} <span class="mono">${esc(x.worst)}</span> — 불량률 ${pct(x.worst_rate, 1)}, 나머지의 ${f(x.lift, 1)}배</td></tr>`).join('')}
        ${d8.d4.map((x, i) => `<tr><td>D5 확인 계획 ${i + 1}</td><td>«${esc(x.column)}» ${esc(x.worst)} 구간의 품목·설비·작업 이력을 비교. 운전 조건 변경은 공정 담당자 승인 후 검증</td></tr>`).join('')}
        <tr><td>D6 효과 검증</td><td>같은 품목·설비의 별도 기간 또는 승인된 비교 시험으로 확인. 불량 감소 목표·절감액은 검증 후 확정</td></tr>
        <tr><td>D7 재발 방지</td><td>검증된 원인과 승인된 조치를 작업 표준에 반영하고 재발 여부를 확인</td></tr></tbody></table></div>
      <p class="trace-caption">담당자 검토 전 초안 · D1 팀 구성, D3 임시 조치, 원인 확정·대책 승인·효과 검증, D8 종결은 현장에서 작성합니다.</p></div>` : '';
    return head + fac + (e8 ? `<details class="ui-fold" id="trace-report"><summary>8D 보고서 초안 <span class="tag j-warn">검토 대기</span></summary>${e8}</details>` : '')
      + `<details class="ui-fold"><summary>데이터 점검 · AI 분석 근거</summary><div class="grid g2" style="margin-top:16px">${sig || ''}${rd}</div>${cols}</details>`;
  }

  const V = {};
  V.xai = () => {
    load();
    if (err) return `<div class="card"><div class="empty">${esc(err)}</div></div>`;
    if (!A) return '<div class="card"><div class="empty">공정 통합 AI 결과를 불러오는 중…</div></div>';
    const body = { schema: vSchema, learn: vLearn, mct: vMct, cold: vCold, raw: vRaw, trace: vTrace, dx: vDiag }[st.tab] || vSchema;
    return `${kpis()}<div class="spc-sub" style="margin-top:12px">${M.seg('xa', 'tab|' + st.tab, [['tab|schema', '칼럼 해석'], ['tab|learn', '누적 학습'], ['tab|mct', '공정별 모델'], ['tab|cold', '처음 보는 공정'], ['tab|raw', '적산 · 비정형'], ['tab|trace', '원인 추적'], ['tab|dx', '데이터 진단']])}
      <span class="muted" style="font-size:12px;margin-left:auto">${esc(A.version)} · ${esc(A.built)}</span></div>${body()}`;
  };
  const wrap = fn => () => { load(); if (err) return `<div class="card"><div class="empty">${esc(err)}</div></div>`;
    if (!A) return '<div class="card"><div class="empty">불러오는 중…</div></div>'; return fn(); };
  V.tx_report = wrap(vDiag);
  V.tx_lots = wrap(() => { const h = vTrace(), i = h.indexOf('<div class="card" style="margin-top:16px"><h2>원인 추적 검증'); return '<div class="trace-status"><span class="tag j-warn">데모 연결</span><span>공정 간 원인 미검증</span><details class="ui-details"><summary>데이터 출처</summary><p>서로 다른 공개 데이터의 LOT을 연결한 예시입니다. 실제 공장의 공정 간 원인이나 현장 성능을 검증한 결과가 아닙니다.</p></details></div>' + (i > 0 ? h.slice(0, i) : h); });
  M.aiModel = () => A;
  M.aiLoad = load;
  M.aiPick = id => { st.pick = String(id || ''); };
  M.mod({
    id: 'xai', group: '분석', order: 5.7,
    menu: [['xai', '공정 통합 AI', 'trend']],
    views: V,
    bind(root) {
      const ta = root.querySelector('#xa-memo');
      if (ta) ta.oninput = () => { memo = ta.value; const pos = ta.selectionStart; M.render(); const t2 = document.querySelector('#xa-memo'); if (t2) { t2.focus(); t2.setSelectionRange(pos, pos); } };
      const fi = root.querySelector('#xa-dx'), co = root.querySelector('#xa-cost');
      if (fi) fi.onchange = () => { if (fi.files && fi.files[0]) runDx(fi.files[0]); };
      if (co) co.onchange = () => { dx.cost = co.value.trim(); M.render(); };
      root.querySelectorAll('[data-xa]').forEach(b => b.onclick = e => { e.preventDefault(); const s = b.dataset.xa, i = s.indexOf('|');
        if (s === 'dxr|1') { dx.r = null; dx.err = ''; } else st[s.slice(0, i)] = s.slice(i + 1); M.render(); }); },
  });
})();
