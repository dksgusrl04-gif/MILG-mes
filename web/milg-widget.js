/* MILG embeddable widget: host MES sends an explicit cycle; no background data collection. */
(() => {
  'use strict';
  if (customElements.get('milg-ai-widget')) return;
  class MilgWidget extends HTMLElement {
    constructor() {
      super(); this.attachShadow({mode:'open'}); this.sequence=0;
      this.shadowRoot.innerHTML=`<style>
      :host{font:14px system-ui,sans-serif;position:fixed;right:20px;bottom:20px;z-index:10000;color:#17263c}
      button{font:inherit;cursor:pointer;border:1px solid #dce2ed;border-radius:9px;background:white;padding:9px 14px;color:inherit}button:disabled{opacity:.5;cursor:wait}
      .fab{box-shadow:0 4px 20px #12233c19;border-radius:28px;font-weight:700}.brand{color:#5145eb;font-weight:800}
      .panel{width:min(420px,calc(100vw - 32px));max-height:75vh;overflow:auto;background:white;border:1px solid #dce2ed;border-radius:16px;box-shadow:0 12px 50px #12233c28;margin-bottom:12px}
      header{display:flex;align-items:center;justify-content:space-between;padding:16px;border-bottom:1px solid #edf0f6}main{padding:18px}h3{font-size:16px;margin:0 0 12px}.muted{color:#63758a;font-size:12px;line-height:1.6}.status{padding:10px;background:#f4f6ff;border-radius:9px;margin:12px 0}dl{display:grid;grid-template-columns:100px 1fr;gap:8px;margin:12px 0}dt{color:#63758a}dd{margin:0;overflow-wrap:anywhere}ul{padding-left:20px}li{margin:8px 0;overflow-wrap:anywhere}.actions{display:flex;gap:8px;margin-top:16px}.primary{background:#5145eb;color:white;border-color:#5145eb}[hidden]{display:none!important}
      </style><section class="panel" hidden aria-label="MILG AI 분석 패널"><header><span><b class="brand">MILG | AI</b> · 공정 분석</span><button id="close" aria-label="패널 닫기">×</button></header><main><h3 id="title">작업을 선택해 주세요</h3><p id="message" role="status" class="status">MES에서 선택한 작업의 분석 결과가 표시됩니다.</p><dl id="details"></dl><strong id="evidenceTitle" hidden>측정 근거</strong><ul id="evidence"></ul><p id="limits" class="muted"></p><div class="actions" hidden><button class="primary" id="investigate">조사 등록</button><button id="hold">보류</button></div><p class="muted">조사 등록·보류는 품질 합불 정답이나 설비 제어 명령이 아닙니다.</p></main></section><button class="fab"><span class="brand">MILG | AI</span> · 분석 패널</button>`;
      this.$=q=>this.shadowRoot.querySelector(q);
      this.$('.fab').onclick=()=>{this.$('.panel').hidden=!this.$('.panel').hidden;};
      this.$('#close').onclick=()=>{this.$('.panel').hidden=true;};
      this.$('#investigate').onclick=()=>this.review('investigate');
      this.$('#hold').onclick=()=>this.review('hold');
    }
    configure({endpoint,token,actor,featureLabels={},units={}}) {
      const u=new URL(endpoint,location.href);
      if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.search||u.hash)throw Error('올바른 엔진 주소가 필요합니다.');
      if(typeof token!=='string'||!token.trim()||typeof actor!=='string'||!actor.trim())throw Error('인증 정보와 담당자가 필요합니다.');
      if(u.protocol==='http:'&&!['localhost','127.0.0.1','[::1]'].includes(u.hostname))throw Error('원격 엔진은 HTTPS 주소가 필요합니다.');
      this.sequence++;this.controller?.abort();this.clear();
      this.config={endpoint:u.href.replace(/\/$/,''),token,actor,featureLabels,units};
      return this;
    }
    async request(path,body,signal) {
      if(!this.config)throw Error('분석 엔진 연결을 먼저 설정해 주세요.');
      const r=await fetch(this.config.endpoint+path,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+this.config.token},body:JSON.stringify(body),signal,credentials:'omit',cache:'no-store'});
      const data=await r.json();if(!r.ok)throw Error(data.error||'분석 요청에 실패했습니다.');return data;
    }
    clear() {
      this.result=null;this.payload=null;this.$('#details').replaceChildren();this.$('#evidence').replaceChildren();this.$('.actions').hidden=true;this.$('#evidenceTitle').hidden=true;this.$('#limits').textContent='';
    }
    async analyze(payload) {
      const seq=++this.sequence;this.controller?.abort();this.controller=new AbortController();this.clear();
      this.$('.panel').hidden=false;this.$('#title').textContent=payload.lot_id||'선택 작업';this.$('#message').textContent='분석 중…';
      const timer=setTimeout(()=>this.controller?.abort(),30000);
      try{
        const result=await this.request('/api/cycle',payload,this.controller.signal);
        if(seq!==this.sequence)return null;
        this.setResult(result,payload);return result;
      }catch(e){if(seq===this.sequence){this.clear();this.$('#message').textContent=e.name==='AbortError'?'분석 요청 시간이 초과되었거나 취소됐습니다.':e.message;}throw e;}
      finally{clearTimeout(timer);}
    }
    setResult(result,payload) {
      this.sequence++;this.controller?.abort();
      if(result.event_id!==payload.cycle_id||!Array.isArray(result.rows)||result.rows.length!==1||(payload.process!=='auto'&&result.process!==payload.process))throw Error('요청한 작업과 분석 응답이 일치하지 않습니다.');
      this.clear();this.result=result;this.payload=payload;this.$('.panel').hidden=false;
      const row=result.rows[0];this.$('#title').textContent=payload.lot_id+' · '+payload.cycle_id;
      this.$('#message').textContent=row.alert===true?'확인이 필요한 이상 징후가 있습니다.':row.alert===false?'설정된 알림 기준을 넘지 않았습니다.':'현장 알림 기준 검증이 필요합니다.';
      const names={mct_hmotp:'MCT · 엔드밀',mct_nasa:'MCT · 밀링',mct_stiehl:'MCT · 숄더 밀링',pressfit:'압입 조립',inj_quality:'사출',screw_tightening:'나사 체결'};
      const states={experimental_alert:'참고 분석 · 현장 확인 필요',needs_threshold_validation:'알림 기준 검증 필요',benchmark_estimate:'공개 데이터 기반 참고 예측',normal_reference:'확인된 정상 작업과 비교',needs_normal_reference:'정상 기준 등록 필요'};
      const estimate=row[result.target],hasEstimate=Number.isFinite(estimate);
      const scoreLabel=hasEstimate?'예측값':Number.isFinite(row.anomaly_score)?'정상 기준 이탈 점수':'분류 점수';
      const scoreValue=hasEstimate?Number(estimate.toPrecision(4))+' '+(result.capability?.unit||'(원자료 단위)'):Number.isFinite(row.anomaly_score)?Number(row.anomaly_score.toPrecision(4)):Number.isFinite(row.p)?(row.p*100).toFixed(1)+'% · 현장 확률 보정 전':'기준 확보 후 표시';
      const details=[['공정',names[result.process]||result.process],['모델 버전',(result.release||'미확인').slice(-8)],['상태',states[result.decision_status||result.mode]||'담당자 확인 필요'],[scoreLabel,scoreValue]];
      for(const [k,v] of details){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=k;dd.textContent=v;this.$('#details').append(dt,dd);}
      for(const e of (row.top||[]).slice(0,4)){
        const key=e.feature||e.name||e.slot||'측정값';
        const label=this.config?.featureLabels?.[key]||key.replace(/^A_x/,'X축 진동').replace(/^A_y/,'Y축 진동').replace(/_p2p$/,' · 진폭 범위').replace(/_kurt$/,' · 첨도');
        const raw=e.measured_value??e.value??e.contribution??e.contrib;
        const value=Number.isFinite(raw)?Number(raw.toPrecision(4)):raw??'기록 확인';
        const li=document.createElement('li');li.textContent=label+' · '+value+' '+(this.config?.units?.[key]||'');this.$('#evidence').append(li);
      }
      this.$('#evidenceTitle').hidden=!this.$('#evidence').children.length;
      this.$('#limits').textContent='표시된 측정 근거는 원인 확정이 아닙니다. '+(result.field_validated?'현장 검사 결과와 함께 확인해 주세요.':'공개 데이터 기반 참고 분석이며, 현장 검증이 필요합니다.');
      this.$('.actions').hidden=false;this.dispatchEvent(new CustomEvent('milg:result',{detail:{event_id:result.event_id,process:result.process},bubbles:true,composed:true}));
    }
    async review(action){
      if(!this.result||!this.config)return;
      const seq=this.sequence,event=this.result.event_id,buttons=this.shadowRoot.querySelectorAll('.actions button');buttons.forEach(b=>b.disabled=true);
      try{const r=await this.request('/api/review',{event_id:event,action,actor:this.config.actor,note:''});if(seq===this.sequence&&this.result?.event_id===event)this.$('#message').textContent=(action==='hold'?'보류':'조사 등록')+' 저장 완료';this.dispatchEvent(new CustomEvent('milg:review',{detail:r,bubbles:true,composed:true}));}
      catch(e){if(seq===this.sequence)this.$('#message').textContent='처리 기록 저장 실패: '+e.message;}
      finally{buttons.forEach(b=>b.disabled=false);}
    }
    disconnectedCallback(){this.sequence++;this.controller?.abort();}
  }
  customElements.define('milg-ai-widget',MilgWidget);
})();
