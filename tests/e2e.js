// 화면 · 시나리오 자동 시험 — node tests/e2e.js  (먼저 python tools/build_preview.py)
// 1.0 전체 화면 · 2.0 신뢰 계층 · 3.0 판단 · 4.0 확률 · 자기 의심 · 판단 증명까지, 실패하면 종료 코드 1
const { chromium } = require('playwright');
const path = require('path'), url = 'file://' + path.resolve(__dirname, '../preview.html');
const opt = process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : (require('fs').existsSync('/opt/pw-browsers/chromium') ? { executablePath: '/opt/pw-browsers/chromium' } : {});
let pass = 0; const fails = [];
const ok = (c, m) => { if (c) pass++; else fails.push(m); console.log(`${c ? '✓' : '✕'} ${m}`); };

(async () => {
  const b = await chromium.launch(opt);
  // ── 1. 모든 화면 (라이트 · 다크)
  for (const th of ['light', 'dark']) {
    const p = await b.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: th }), errs = []; p.on('pageerror', e => errs.push(e.message));
    await p.goto(url); await p.waitForFunction(() => window.MLNGTrust && MLNGTrust.ready, null, { timeout: 60000 });
    const ks = await p.evaluate(() => Object.keys(MLNG.views())), empty = [];
    for (const k of ks) { await p.evaluate(k => MLNG.go(k), k); await p.waitForTimeout(60); if ((await p.$eval('#view', e => e.innerText.length)) < 50) empty.push(k); }
    ok(!empty.length && !errs.length, `${th} 화면 ${ks.length}개 — 빈 화면 ${empty.join(',') || '없음'} · 오류 ${errs.length}`);
    await p.close();
  }
  // ── 2~4. 시나리오
  const ctx = await b.newContext({ viewport: { width: 1440, height: 1000 } }), p = await ctx.newPage(), errs = [];
  p.on('pageerror', e => errs.push(e.message)); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto(url); await p.waitForFunction(() => window.MLNGTrust && MLNGTrust.ready, null, { timeout: 60000 }); await p.waitForTimeout(300);
  const W = ms => p.waitForTimeout(ms || 300), go = k => p.evaluate(k => MLNG.nav(k), k), ev = fn => p.evaluate(fn);
  const note = () => p.evaluate(() => [...document.querySelectorAll('#view .note')].map(n => n.innerText).join(' | '));

  // 2.0 신뢰 계층
  await go('net_ledger'); await W(); await p.click('#lg-verify'); await W(800); ok(/모두 사슬과 서명이 맞습니다/.test(await note()), '원장 전체 검증');
  await p.click('#lg-tamper'); await W(800); ok(/바뀌었|끊|불일치|다릅/.test(await note()), '원장 위변조 감지');
  await p.click('#lg-restore'); await W(500);
  await go('pass_home'); await W(); await p.click('#pp-verify'); await W(700); ok(await ev(() => !!document.querySelector('#view .pp')), 'LOT 여권 검증 화면');
  const nDel = await ev(() => MLNGTrust.orders[0].deliveries.length); await go('deal_home'); await W(); await p.click('#so-deliver'); await W(1000);
  ok(await ev(() => MLNGTrust.orders[0].deliveries.length) === nDel + 1, '스마트 발주 납품 → 자동 검수');

  // 3.0 판단
  ok(await ev(() => MLNG.caseApi.open().length >= 3), '알림을 사건으로 묶음');
  await go('cases'); await W(); await p.click('[data-csel^="CS-HEAT"]'); await W();
  await p.click('.cdet .cnext [data-crun]'); await W(600); ok(/LOT|보류|요청|지시/.test(await p.$eval('.cdet .note', e => e.innerText)), '사건 다음 조치 실행');
  ok(await ev(() => { const c = MLNG.caseApi.list().find(x => x.tpl === 'heat'); return c.causes.some(x => x.cmp && x.cmp.p != null); }), '원인 후보에 순열 검정 p');
  await go('gate'); await W(); ok(await ev(() => MLNG.gate.candidates().some(l => MLNG.gate.predict(l.id).prob)), '출하 게이트 LOT 클레임 확률');
  await go('whatif'); await W(); await p.click('#wi-opt'); await W(2500); ok(await ev(() => /최적 조합/.test(document.querySelector('#view').innerText)), 'What-if 최적 찾기');
  await go('replay'); await W(); await p.click('[data-rp="-1"]'); await W(500); ok(await ev(() => /당시 라인/.test(document.querySelector('#view').innerText)), '공장 되감기');
  await go('shift'); await W(); await p.click('#sh-out'); await W(400); ok(await ev(() => /인계 서명 완료/.test(document.querySelector('#view').innerText)), '교대 넘김 서명');
  await go('overview'); await W(); await p.keyboard.press('Control+k'); await W(150); await p.keyboard.type('HT-147004'); await W(150); await p.keyboard.press('Enter'); await W(300);
  ok(await ev(() => MLNG.cur === 'trace'), '찾기 · 이동 (Ctrl K)');
  await ev(() => MLNG.askApi.ask('열처리 2존 점검 지시하고 히터 발주 올려줘')); await W(700); ok(await ev(() => document.querySelectorAll('#chatbox [data-aok]').length >= 2), '말로 지시 → 승인 카드 2장');

  // 4.0 확률 · 자기 의심 · 판단 증명
  ok(await ev(() => MLNG.ml.all().length === 6 && MLNG.ml.all().every(x => x.trust >= 0 && x.trust <= 1)), '모델 6개 신뢰도');
  ok(await ev(() => MLNGTrust.agent.items.some(x => x.doubt)), '자기 의심 — 근거 모델 신뢰가 낮은 자동 조치를 승인 대기로');
  ok(await ev(() => { const r = MLNG.ml.rul('T15', 0), r3 = MLNG.ml.rul('T15', 3); return r.t90 - r.t10 >= r3.t90 - r3.t10; }), '공구 수명 — 측정이 쌓이면 구간이 좁아짐');
  ok(await ev(() => !!MLNG.ml.changeFor('heat')), '히터 변화점');
  await go('gate'); await W(); const holdBtn = await p.$('[data-ghold]'); if (holdBtn) { await holdBtn.click(); await W(700); }
  ok(await ev(() => MLNG.prove.log.length > 0), '판단 증명 기록');
  ok(await ev(() => MLNG.prove.log.filter(r => MLNG.prove.canReplay(r.id)).every(r => MLNG.prove.replay(r.id).same)), '판단 재현 — 같은 입력 → 같은 결과');
  await go('models'); await W(); ok(await ev(() => /자기 의심 정책/.test(document.querySelector('#view').innerText)), '모델 상태 화면');
  await go('prove'); await W(); await p.click('#pv-all').catch(() => {}); await W(300); ok(await ev(() => /같은 결과/.test(document.querySelector('#view').innerText)), '판단 증명 화면 재현');

  // 5.0 정돈: 메뉴 7개 · 주문 → 출하 대시보드
  ok(await ev(() => document.querySelectorAll('#nav .nmain button').length === 9), '왼쪽 메뉴 9개 (나머지는 더보기)');
  ok(await ev(() => Object.keys(MLNG.views()).every(k => MLNG.where(k))), '모든 화면이 메뉴 · 탭 · 더보기 어딘가에 있음');
  await go('overview'); await W(); ok(await ev(() => document.querySelectorAll('.mtx th.sth').length === 5 && document.querySelectorAll('.mtx tbody tr').length > 0), '대시보드 주문 현황판 — 주문 × 5단계');
  ok(await ev(() => [...document.querySelectorAll('.mtx tbody tr')].every(r => r.querySelectorAll('.mc').length === 5)), '현황판 모든 행에 5단계 칸 (열 맞춤)');
  ok(await ev(() => { const cs = [...document.querySelectorAll('.dgrid > .card')].map(c => c.getBoundingClientRect()); return cs.length === 2 && Math.abs(cs[0].top - cs[1].top) < 1 && Math.abs(cs[0].height - cs[1].height) < 1; }), '나란한 카드 위·높이 맞춤');
  await p.click('.mtx th.sth[data-fsel="qual"]'); await W(); ok(await ev(() => [...document.querySelectorAll('.itab tbody tr td:nth-child(2)')].every(x => x.textContent === '품질')), '단계 머리를 누르면 문제 목록이 그 단계로 좁혀짐');
  await p.click('.mtx th.sth[data-fsel="qual"]'); await W();
  ok(await ev(() => MLNG.flow.book().orders.every(o => o.lots.every(l => MLNG.S.heat.lots.some(h => h.id === l.id)))), '주문은 실제 열처리 LOT 에 묶임');
  await go('orders'); await W(); await p.click('#view tr.click'); await W(300); ok(await ev(() => /수주 OR-/.test(document.querySelector('#dttl').textContent)), '주문 상세 서랍');
  await ev(() => MLNG.closeDrawer());
  ok(await ev(() => { MLNG.go('heat_lots'); return document.querySelectorAll('#tabs .tabs button').length === 6 && document.querySelectorAll('#tabs .subtabs button').length === 4; }), '공정 실적: 전체 공정 · 일보 + 공정 탭 + 세부 탭');

  // 5.3 가정값 관리 · 전체 공정
  ok(await ev(() => !document.querySelector('#nav .nsub').textContent.includes('기타')), '더보기에 중복 «기타» 없음');
  ok(await ev(() => /가정 포함/.test((MLNG.go('cost'), document.querySelector('#view').innerText))), '손실 금액에 「가정 포함」 표시');
  const w0 = await ev(() => MLNG.gate.predict(MLNG.gate.candidates()[0].id).claimCost);
  await go('assume'); await W(); await p.fill('[data-ap="COST.CLAIM_FIXED"]', '1000000'); await p.click('#as-save'); await W(500);
  ok(await ev(() => MLNG.CFG.COST.CLAIM_FIXED === 1000000), '가정값 저장 → 설정 반영');
  ok(await p.evaluate(w0 => MLNG.gate.predict(MLNG.gate.candidates()[0].id).claimCost > w0, w0), '가정값 바꾸면 클레임 비용 · 출하 판정 다시 계산');
  ok(await ev(() => MLNGTrust.ledger.some(e => e.kind === 'config')), '가정값 변경이 신뢰 원장에 기록');
  await p.click('#as-reset'); await W(500); ok(await ev(() => MLNG.CFG.COST.CLAIM_FIXED === 500000), '기본값으로 되돌리기');
  await go('procmap'); await W(); ok(await ev(() => document.querySelectorAll('.pmap .pnode').length === 7 && document.querySelectorAll('.lftab tbody tr').length > 0), '전체 공정 흐름 7단계 + LOT 흐름');
  ok(await ev(() => { const t = [...document.querySelectorAll('.pmap .pnode')].map(n => n.getBoundingClientRect()); return t.every(r => Math.abs(r.top - t[0].top) < 1 && Math.abs(r.height - t[0].height) < 1); }), '공정 카드 위·높이 맞춤');

  // 5.5 현업 기본: 데모 날짜 · 물량 · 알람 등급 · OEE · POP · 엑셀 · 일보 · 안돈
  ok(await ev(() => { const a = new Date(MLNG.S.asof.replace(' ', 'T')), n = new Date(); return (n - a) / 864e5 < 1.01 && (n - a) >= -60000; }), '데모 날짜가 오늘(또는 어제) 기준');
  ok(await ev(() => { const d7 = p => MLNG.S.lots.filter(l => l.proc === p && new Date(String(l.end_at || l.start_at).replace(' ', 'T')) >= MLNG.sinceDay(14)).reduce((s, l) => s + l.qty, 0); return d7('press') >= d7('heat') * 0.9; }), '프레스 생산 ≥ 열처리 투입 (물량 역전 없음)');
  ok(await ev(() => { const a = MLNG.alerts(), A = a.filter(x => x.grade === 'A').length; return a.every(x => 'ABC'.includes(x.grade)) && A / a.length <= 0.25; }), '알람 A/B/C 등급 · A 비율 25% 이하');
  ok(await ev(() => { const r = MLNG.oeeApi.period(7); return r.oee > 0.2 && r.oee < 1 && r.A <= 1 && r.P <= 1 && r.Q <= 1; }), 'OEE = 가동률 × 성능 × 양품률 (0~1)');
  await go('downtime'); await W(); const u0 = await ev(() => MLNG.oeeApi.unclassified().length); await p.selectOption('.dtsel >> nth=0', 'MAT'); await W(300);
  ok(await p.evaluate(u0 => MLNG.oeeApi.unclassified().length === u0 - 1, u0), '미분류 비가동 사유 입력 → OEE 반영');
  await go('pop'); await W(); await p.click('[data-peq="SB-01"]'); await W(); await p.click('.pjob'); await W(); await p.click('[data-pact="start"]'); await W();
  for (const k of ['5', '0', '0', '0']) await p.click(`[data-pkey="${k}"]`); await p.click('[data-ptgt="bad"]'); await p.click('[data-pkey="3"]'); await W();
  ok(await ev(() => document.querySelector('[data-pact="done"]').disabled), 'POP 불량 사유 없이는 완료 불가');
  await p.click('.pcodes button'); await p.click('[data-pact="stop"]'); await W(); await p.click('[data-pstop="MAT"]'); await W(); await p.click('[data-pact="resume"]'); await W(); await p.click('[data-pact="done"]'); await W(600);
  ok(await ev(() => MLNG.S.lots.some(l => l.pop && l.good === 5000 && l.bad === 3) && MLNGTrust.ledger.some(e => e.kind === 'pop')), 'POP 실적 등록 → LOT · 원장');
  ok(await ev(() => MLNG.oeeApi.days().some(r => r.ev.some(e => e.src === '작업자 입력' && e.code === 'MAT'))), 'POP 비가동 → OEE 비가동');
  await go('daily'); await W(); ok(await ev(() => document.querySelectorAll('#rpt .rt').length >= 4 && /결/.test(document.querySelector('.rsign').innerText)), '생산 일보 (결재란 · 5개 표)');
  ok(await ev(() => MLNG_XLSX.build([{ name: 't', rows: [['a', 1]] }]).size > 500 && document.querySelectorAll('.xbtn').length >= 0), '엑셀(.xlsx) 생성');
  await go('oee'); await W(); ok(await ev(() => document.querySelectorAll('#view .card .xbtn').length >= 1), '표 카드마다 엑셀 단추');
  await ev(() => MLNG.andon.open()); await W(); ok(await ev(() => document.querySelectorAll('#andon .an-r').length === 5), '현장 모니터(안돈) 설비 5줄'); await ev(() => MLNG.andon.close());

  // 5.6 품질 데이터 분석
  await go('spc'); await W(); ok(await ev(() => document.querySelectorAll('#view svg.cc').length >= 1 && /Laney/.test(document.querySelector('#view').innerText)), 'SPC 관리도 (열처리 Laney p′)');
  ok(await ev(() => { const D = MLNG.spc.spcData('heat'); return D.extra.sz > 1 && MLNG.spc.judge(D.x, D.cl, D.s).size < D.extra.plainOut + 30; }), 'Laney σz 과산포 보정');
  ok(await ev(() => { const v = MLNG.spc.judge([0, 0, 0, 0, 0, 0, 0, 0, 0, 5], 0, 1); return v.get(9).includes(1); }) && await ev(() => MLNG.spc.judge([1, 2, 3, 4, 5, 6].map(x => x * 0.1), 0, 1).get(5).includes(5)), '판정 규칙 ① 3σ · ⑤ 6점 추세');
  for (const k of ['press', 'weld', 'zone']) { await p.click(`[data-sx="spc|${k}"]`); await W(300); ok(await ev(() => document.querySelectorAll('#view svg.cc').length === 2), `관리도 ${k} + 산포 관리도`); }
  await go('capa'); await W(); ok(await ev(() => MLNG.spc.capaItems().length >= 10 && MLNG.spc.capaItems().every(c => c.cpkCI[0] <= c.cpk && c.cpk <= c.cpkCI[1] && c.ne <= c.n)), '공정능력 Cp·Cpk·Ppk + 95% 구간');
  await p.click('[data-sx^="capa|heat"]'); await W(300); ok(await ev(() => /LSL/.test(document.querySelector('#view svg.chart').textContent)), '히스토그램 + 규격선');
  await go('strat'); await W(); ok(await ev(() => { const r = MLNG.spc.strat('all').rows; return r.length === 7 && r.every(x => x.p > 0 && x.p <= 1) && new Set(r.map(x => x.m4)).size === 4; }), '불량 층별 분석 4M · 순열 검정 p');
  await p.click('[data-sx="strat|mat"]'); await W(300); ok(await ev(() => /원자재 LOT — 수준별/.test(document.querySelector('#view').innerText)), '층 선택 → 수준별 PPM 구간');
  await go('mval'); await W(); ok(await ev(() => { const r = MLNG.spc.mvRows(); return r.length === 7 && r.filter(x => x.st === 'none').length >= 3 && r.find(x => x.k === 'heat').st !== 'none'; }), '예측 모델 검증 · 미검증 표시');
  for (const k of ['tool', 'vb', 'demand']) { await p.click(`[data-sx="mv|${k}"]`); await W(300); ok(await ev(() => document.querySelectorAll('#view .card:last-child svg, #view .card:last-child .ivl').length >= 1), `모델 검증 상세 ${k}`); }
  ok(await ev(() => MLNG.ml.trustOf('heater').level !== 'ok' && MLNG.ml.demand().bt && MLNG.ml.demand().bt.n > 10), '미검증 모델 신뢰 낮춤 · 수요 백테스트');
  await go('cases'); await W(); await p.click('[data-csel^="CS-HEAT"]'); await W(); ok(await ev(() => document.querySelectorAll('.cdet .arpt .ar-s').length === 6 && !!document.querySelector('.cdet .arpt svg.cc')), '품질 이슈 상세 — 분석 보고서 6단계');

  // 5.7 공정 통합 AI
  await go('xai'); await W(500); ok(await ev(() => !!MLNG.aiModel() && MLNG.aiModel().sources.length >= 5 && /처음 보는 칼럼 해석/.test(document.querySelector('#view').innerText)), '공정 통합 AI 결과물 · 요약');
  ok(await ev(() => document.querySelectorAll('#view .xtab tbody tr').length >= 10), '칼럼 의미 해석 표');
  for (const t of ['learn', 'cold', 'trace']) { await p.click(`[data-xa="tab|${t}"]`); await W(300); ok(await ev(() => document.querySelector('#view').innerText.length > 300), `공정 통합 AI — ${t}`); }
  ok(await ev(() => document.querySelectorAll('#view .xc').length >= 2), '원인 추적 — 불량 기여');
  await p.click('[data-xa="tab|raw"]'); await W(300); ok(await ev(() => /적산/.test(document.querySelector('#view').innerText) && !!document.querySelector('#xa-memo')), '적산 · 비정형 화면');
  await p.fill('#xa-memo', '주축 소음 있고 진동 느낌'); await W(300); ok(await ev(() => document.querySelectorAll('.xmemo-out mark').length >= 2), '메모 이상 징후 단어 표시');
  await p.click('[data-xa="tab|dx"]'); await W(300);
  ok(await ev(() => /불량 원인 후보/.test(document.querySelector('#view').innerText) && /데모 결과/.test(document.querySelector('#view').innerText)), '데이터 진단 — 데모 결과');
  ok(await ev(() => [...document.querySelectorAll('#view .xtab')].some(t => /나쁜 구간/.test(t.innerText) && t.querySelectorAll('tbody tr').length >= 3)), '데이터 진단 — 원인 후보 표');
  ok(await ev(() => /원인 분석 보고서 초안 \(8D\)/.test(document.querySelector('#view').innerText) && /D5 대책/.test(document.querySelector('#view').innerText)), '데이터 진단 — 8D 보고서 초안');
  await p.route('**/ai/v1/diagnose', async r => { const b = JSON.parse(r.request().postData() || '{}'); const s = await p.evaluate(() => MLNG.aiModel().diagnose_sample);
    await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...s, name: b.name, unit_cost: b.unit_cost }) }); });
  await p.evaluate(() => { window.MLNG_CONFIG.AI_URL = 'https://ai.mlng.test'; });
  await p.setInputFiles('#xa-dx', { name: '고객사_라인2.csv', mimeType: 'text/csv', buffer: Buffer.from('온도,NG\n850,0\n870,1\n') }); await W(800);
  ok(await ev(() => /고객사_라인2\.csv/.test(document.querySelector('#view').innerText) && /업로드한 파일/.test(document.querySelector('#view').innerText)), '데이터 진단 — CSV 업로드 → 서버 진단');
  await p.unroute('**/ai/v1/diagnose'); await p.evaluate(() => { window.MLNG_CONFIG.AI_URL = ''; });

  // 끝: 모든 동작 뒤에도 원장이 온전한가
  await go('net_ledger'); await W(); await p.click('#lg-verify'); await W(1000); ok(/모두 사슬과 서명이 맞습니다/.test(await note()), '모든 시나리오 뒤 원장 검증');
  ok(!errs.length, `페이지 오류 ${errs.length}건 ${errs.slice(0, 3).join(' / ')}`);
  await b.close();
  console.log(`\ne2e ${fails.length ? '✕' : '✓'} ${pass}/${pass + fails.length}`);
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
