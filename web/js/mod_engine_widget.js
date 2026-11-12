/* Local MES -> actual engine bridge. Loaded only by the local pilot /mes/ server.
 * Uses the SELECTED recorded MCT cycle's six measured summaries; no quality labels,
 * no unrelated example substitution, no saved prediction fallback, no API token storage.
 */
(function () {
  'use strict';
  const M = window.MLNG;
  if (!M || !M.CFG.LOCAL_ENGINE_MODE) return;
  const {esc, f} = M;
  const channels = ['smcAC','smcDC','vib_table','vib_spindle','AE_table','AE_spindle'];
  const labels = {smcAC:'주축 전류 AC',smcDC:'주축 전류 DC',vib_table:'테이블 진동',vib_spindle:'주축 진동',AE_table:'테이블 AE',AE_spindle:'주축 AE'};
  const state = {selection:null, token:'', actor:'발표 담당자', release:'', contract:null, tool:'', history:[], historyLoaded:false, historyBusy:false, historyError:''};
  const widget = document.createElement('milg-ai-widget'); widget.id='mes-live-widget'; document.body.append(widget);
  const style = document.createElement('style');
  style.textContent = '.engine-tools{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.engine-tools .in{max-width:260px}.engine-cycle-selected{background:var(--pri-bg,#f1f4ff)!important}.engine-replay{font-size:12px;color:var(--muted,#66758a);margin-left:auto}.engine-connect{border:1px solid #d8e1ec;border-radius:7px;padding:7px 12px;background:white;color:#4556b8;cursor:pointer;white-space:nowrap}.engine-dialog{border:1px solid #dce4ef;border-radius:14px;padding:25px;width:min(430px,calc(100vw - 32px));box-shadow:0 16px 70px #172a4a2b;color:#25354d;background:white}.engine-dialog::backdrop{background:#182c4e44}.engine-dialog label{display:block;margin:12px 0 6px;font-size:13px}.engine-dialog input{width:100%;padding:11px;border:1px solid #d8e1ec;border-radius:7px}.engine-dialog h3{margin:0 0 15px}.engine-dialog small{color:#768498}.engine-dialog .act{display:flex;gap:10px;margin-top:18px}#mes-engine-error{color:#a43b4a;min-height:26px;font-size:13px;padding-top:12px}.engine-summary{display:flex;align-items:center;gap:12px;padding:12px 0;font-size:13px}.engine-summary b{color:#4657bd}.engine-row-action{white-space:nowrap}';
  document.head.append(style);
  style.textContent+='@media(min-width:1350px){body.engine-panel-open #view{margin-right:444px}}';
  new MutationObserver(()=>document.body.classList.toggle('engine-panel-open',!widget.$('.panel').hidden)).observe(widget.$('.panel'),{attributes:true,attributeFilter:['hidden']});
  const dialog = document.createElement('dialog'); dialog.className='engine-dialog'; dialog.id='mes-engine-dialog';
  dialog.innerHTML='<h3>MILG 엔진 연결</h3><small>이 PC의 엔진에 연결합니다.</small><label for="mes-engine-token">API 토큰</label><input id="mes-engine-token" type="password" autocomplete="off" placeholder="state/pilot-token.txt의 값"><label for="mes-engine-actor">담당자</label><input id="mes-engine-actor" value="발표 담당자" maxlength="100"><div id="mes-engine-error" role="status"></div><div class="act"><button type="button" class="btn pri" id="mes-engine-login">연결</button><button type="button" class="btn" id="mes-engine-cancel">닫기</button></div>';
  document.body.append(dialog);
  const topButton=document.createElement('button');topButton.id='mes-engine-connect';topButton.className='engine-connect';topButton.textContent='엔진 연결';document.querySelector('#top').append(topButton);
  const operations=document.createElement('a');operations.href='/operations';operations.textContent='학습 운영';operations.className='btn';document.querySelector('#top').append(operations);
  const openSettings=()=>{if (!dialog.open) dialog.showModal();};topButton.onclick=openSettings;
  dialog.querySelector('#mes-engine-cancel').onclick=()=>dialog.close();

  async function api(name, body, token=state.token) {
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch('/api/'+name,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal,credentials:'omit',cache:'no-store'});
      const result=await response.json();if(!response.ok)throw Error(result.error||'엔진 응답 오류');return result;
    } finally {clearTimeout(timer);}
  }
  function resetWidget(message='MES에서 MCT 작업을 선택해 주세요.') {
    state.revision=(state.revision||0)+1;
    widget.sequence++;widget.controller?.abort();widget.clear();
    widget.$('#message').textContent=message;
  }
  dialog.querySelector('#mes-engine-login').onclick=async()=>{
    const button=dialog.querySelector('#mes-engine-login');button.disabled=true;
    try {
      const token=dialog.querySelector('#mes-engine-token').value.trim(),actor=dialog.querySelector('#mes-engine-actor').value.trim();
      if(!token||!actor)throw Error('토큰과 담당자를 입력해 주세요.');
      const status=await api('status',{},token),contract=await api('contract',{process:'mct_nasa'},token);
      if(!status.processes.mct_nasa || channels.some(c=>!contract.required_features.includes(c)) || contract.required_features.length!==channels.length)throw Error('선택한 MCT 입력과 등록 모델 규격이 다릅니다.');
      state.token=token;state.actor=actor;state.release=status.release;state.contract=contract;
      topButton.textContent='엔진 연결됨';topButton.title='로컬 모델 '+status.release.slice(-8);
      state.historyLoaded=false;dialog.querySelector('#mes-engine-token').value='';dialog.querySelector('#mes-engine-error').textContent='';dialog.close();resetWidget();M.render();
    } catch(error){dialog.querySelector('#mes-engine-error').textContent=error.message;}
    finally{button.disabled=false;}
  };

  const tools=()=>M.S?.mach?.tools||[];
  const runs=tool=> (M.S?.mach?.runs||[]).filter(r=>r.tool===tool).sort((a,b)=>a.run-b.run);
  function select(toolId, runNumber) {
    const tool=tools().find(t=>t.id===toolId),record=runs(toolId).find(r=>r.run===Number(runNumber));
    state.selection=tool&&record?{tool,record}:null;
    if(state.selection)state.tool=toolId;
    resetWidget(state.selection?`${tool.lot} · ${tool.id} · ${record.run}회차 — 분석 대기`:'해당 작업의 입력 규격이 연결되지 않았습니다.');
    return !!state.selection;
  }
  function payloadForSelection() {
    if(!state.selection)throw Error('먼저 MCT 작업을 선택해 주세요.');
    if(!state.release)throw Error('엔진을 먼저 연결해 주세요.');
    const {tool,record}=state.selection;
    if(record.bad)throw Error('센서 이상이 표시된 작업입니다. 측정 기록을 먼저 확인해 주세요.');
    const row=Object.fromEntries(channels.map(c=>[c,record.s?.[c]??null]));
    return {process:'mct_nasa',demo:true,cycle_id:['mes',state.release.slice(-12),tool.lot,tool.id,record.run].join(':'),
      lot_id:tool.lot,context:{machine_id:'MC-01',part_number:'NASA-MCT-public-record',program:'nasa-six-rms-v1'},rows:[row],
      display_label:`${tool.id} · ${record.run}회차`,
      mes_selection:{tool_id:tool.id,run:record.run,display_time:record.t,source:'MES public NASA MCT replay'}};
  }
  async function analyze() {
    if(!state.token){openSettings();return;}
    const revision=state.revision;
    try {
      const payload=payloadForSelection();
      const units=Object.fromEntries(Object.entries(state.contract.input_units).map(([k,v])=>[k,v==='source_sensor_unit'?'원자료 단위':v]));
      widget.configure({endpoint:location.origin,token:state.token,actor:state.actor,featureLabels:labels,units});
      await widget.analyze(payload);
    } catch(error) {
      if(revision!==state.revision)return;
      resetWidget(error.name==='AbortError'?'분석 요청이 취소됐습니다.':error.message);widget.$('.panel').hidden=false;
    }
  }
  widget.$('.fab').onclick=()=>analyze();

  function view() {
    if(!state.tool||!tools().some(t=>t.id===state.tool))state.tool=tools().slice(-1)[0]?.id||'';
    const tool=tools().find(t=>t.id===state.tool),records=runs(state.tool);
    return `<div class="card"><div class="engine-tools"><b>MCT 작업 기록</b><select class="in" id="engine-tool" aria-label="공구·LOT 선택">${tools().slice().reverse().map(t=>`<option value="${esc(t.id)}"${t.id===state.tool?' selected':''}>${esc(t.id)} · ${esc(t.lot)}</option>`).join('')}</select><button class="btn" id="engine-open-settings">${state.token?'연결 설정':'엔진 연결'}</button><span class="engine-replay">공개 작업 기록 재생 · 위젯은 현재 모델 추론</span></div>
      <div class="engine-summary"><b>${esc(state.selection?.tool.lot||'작업 선택 대기')}</b><span>${state.selection?`${esc(state.selection.tool.id)} · ${state.selection.record.run}회차`: '행을 선택한 뒤 우측 하단 MILG 버튼으로 분석'}</span></div>
      ${M.table([['회차',r=>`<b>${r.run}</b>`],['선택',r=>`<button class="btn sm engine-row-action" data-engine-select="${r.run}" data-engine-tool="${esc(tool.id)}">${state.selection?.tool.id===tool.id&&state.selection.record.run===r.run?'선택됨':'작업 선택'}</button>`],['AI 분석',r=>`<button class="btn pri sm engine-row-action" data-engine-analyze="${r.run}" data-engine-tool="${esc(tool.id)}">MILG 분석</button>`],['작업 시각','t'],['주축 전류 DC',r=>f(r.s?.smcDC,3),'n'],['테이블 진동',r=>f(r.s?.vib_table,3),'n'],['테이블 AE',r=>f(r.s?.AE_table,3),'n'],['입력 상태',r=>r.bad?'<span class="tag j-warn">센서 확인</span>':'<span class="tag j-ok">기록 있음</span>']],records,{wrap:false,hl:r=>state.selection?.tool.id===tool?.id&&state.selection?.record.run===r.run})}</div>`;
  }
  async function loadHistory() {
    if(!state.token||state.historyBusy)return;
    state.historyBusy=true;state.historyError='';
    try {state.history=(await api('history',{limit:50})).reviews;state.historyLoaded=true;}
    catch(error){state.historyError=error.message;state.historyLoaded=true;}
    finally{state.historyBusy=false;if(M.cur==='engine_reviews')M.render();}
  }
  function historyView() {
    if(!state.historyLoaded&&!state.historyBusy&&state.token)loadHistory();
    return `<div class="card"><div class="engine-tools"><h2>MILG 조사 이력</h2><button class="btn" id="engine-refresh-history">새로고침</button><span class="engine-replay">로컬 엔진 저장 기록</span></div>${!state.token?'<div class="empty">엔진 연결 후 확인할 수 있습니다.</div>':state.historyError?`<div class="empty">${esc(state.historyError)}</div>`:M.table([['처리 시각','created'],['LOT','lot_id'],['작업 ID','event_id'],['담당자','actor'],['처리',r=>r.action==='investigate'?'조사 등록':'보류'],['메모','note']],state.history,{wrap:false,empty:state.historyBusy?'불러오는 중…':'처리 기록 없음'})}</div>`;
  }
  const mct=M.NAV.find(n=>n.id==='proc')?.tabs.find(t=>t[0]==='mach_home');mct?.[2].push(['engine_cycles','작업별 AI']);
  M.NAV.find(n=>n.id==='qual')?.tabs.push(['engine_reviews','MILG 조사 이력']);
  M.mod({id:'engine_widget',group:'MCT 가공',order:40.1,views:{engine_cycles:view,engine_reviews:historyView},bind(root){
    const input=root.querySelector('#engine-tool');if(input)input.onchange=()=>{state.tool=input.value;state.selection=null;resetWidget();M.render();};
    root.querySelectorAll('[data-engine-select]').forEach(b=>b.onclick=()=>{select(b.dataset.engineTool,b.dataset.engineSelect);M.render();});
    root.querySelectorAll('[data-engine-analyze]').forEach(b=>b.onclick=()=>{select(b.dataset.engineTool,b.dataset.engineAnalyze);M.render();analyze();});
    const settings=root.querySelector('#engine-open-settings');if(settings)settings.onclick=openSettings;
    const refresh=root.querySelector('#engine-refresh-history');if(refresh)refresh.onclick=()=>state.token?loadHistory():openSettings();
    root.querySelectorAll('[data-trace]').forEach(a=>{const click=a.onclick;a.onclick=event=>{selectContext(a.dataset.trace);if(click)click(event);};});
    const query=root.querySelector('#tq'),find=root.querySelector('#tgo');
    if(query){query.addEventListener('keydown',e=>{if(e.key==='Enter')selectContext(query.value.trim());});if(find)find.addEventListener('click',()=>selectContext(query.value.trim()));}
  }});
  function selectContext(id) {
    const tool=tools().find(t=>t.id===id||t.lot===id);
    if(tool)select(tool.id,runs(tool.id).slice(-1)[0]?.run);
    else{state.selection=null;resetWidget('선택한 LOT의 센서 입력이 연결되지 않았습니다.');}
    return tool;
  }
  const nav=M.nav;
  M.nav=key=>{
    if(/^(mach:|trace:)/.test(key)){
      selectContext(key.slice(key.indexOf(':')+1));
    }else if(/^(heat:|weld:|press:|insp:)/.test(key)){state.selection=null;resetWidget('선택한 공정의 입력 연결이 필요합니다.');}
    return nav(key);
  };
  const go=M.go;
  M.go=key=>{if(/^(heat_|weld_|press_|insp_)/.test(key)){state.selection=null;resetWidget('선택한 공정의 입력 연결이 필요합니다.');}return go(key);};
  const drawer=M.drawer;
  M.drawer=(sub,title,content)=>{
    const result=drawer(sub,title,content);
    if(title.startsWith('공구 ')&&state.selection){
      const button=document.createElement('button');button.className='btn pri';button.id='engine-drawer-analyze';button.textContent='선택 공구의 가공 기록 · MILG 분석';
      button.onclick=()=>{M.closeDrawer();M.go('engine_cycles');};document.querySelector('#dbody').prepend(button);
      document.querySelectorAll('#dbody h4').forEach(h=>{if(h.textContent==='MILG 위험도')h.textContent='MILG 위험도 · 저장된 평가 기록';});
    }
    return result;
  };
  widget.addEventListener('milg:review',()=>{state.historyLoaded=false;loadHistory();});
  M.aiContext=id=>{const tool=tools().find(t=>t.id===id||t.lot===id);if(tool){select(tool.id,runs(tool.id).slice(-1)[0]?.run);analyze();}else{state.selection=null;resetWidget('이 LOT의 공정 입력 규격을 먼저 연결해야 합니다.');widget.$('.panel').hidden=false;}};
  M.aiWidget={draw:()=>{},items:()=>[],open:analyze};
  M.engineWidget={select,analyze,payload:payloadForSelection,selected:()=>state.selection?{tool_id:state.selection.tool.id,run:state.selection.record.run}:null};
})();
