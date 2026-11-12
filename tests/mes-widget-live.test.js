// Existing MES selection -> exact source sensors -> real Python inference -> persistent review.
const fs=require('fs'),path=require('path'),assert=require('assert'),net=require('net');
const {spawn}=require('child_process'),{chromium}=require('playwright');
const repo=path.resolve(__dirname,'..'),ai=process.env.MILG_TEST_AI_ROOT||path.join(repo,'ai'),out=path.join(ai,'reports','delivery-mes');
fs.mkdirSync(out,{recursive:true});const state=fs.mkdtempSync(path.join(out,'state-'));
fs.copyFileSync(path.join(process.env.MILG_TEST_STATE||path.join(ai,'state'),'pilot-active.json'),path.join(state,'pilot-active.json'));
let server,browser;
(async()=>{try{
 const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});}),base='http://127.0.0.1:'+port;
 server=spawn(process.env.MILG_TEST_PYTHON||'python',['-X','utf8','-u','-m','mlng_ai.pilot','serve','--state',state,'--port',String(port)],{cwd:ai,stdio:'ignore',env:{...process.env,OMP_NUM_THREADS:'2',OPENBLAS_NUM_THREADS:'2'}});
 for(let i=0;i<100;i++){try{if((await fetch(base+'/mes/')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
 const token=fs.readFileSync(path.join(state,'pilot-token.txt'),'utf8').trim();
 const api=async(name,body={})=>{const r=await fetch(base+'/api/'+name,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body)});assert.strictEqual(r.status,200);return r.json();};
 // Static serving must not expose any state or arbitrary filesystem path.
 for(const p of ['/mes/%2e%2e/ai/state/pilot-token.txt','/mes/%2e%2e%2fai/state/pilot-token.txt','/mes/%2e%2e%2fai/mlng_ai/pilot.py','/mes/data/missing.json'])assert.strictEqual((await fetch(base+p)).status,404);
 browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1536,height:1000}}),errors=[],external=[],sent=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.url()===base+'/api/cycle')sent.push(r.postDataJSON());});
 await page.route('**/*',route=>{if(new URL(route.request().url()).origin!==base){external.push(route.request().url());return route.abort();}return route.continue();});
 await page.goto(base+'/mes/');await page.waitForFunction(()=>window.MLNG?.S&&MLNG.engineWidget&&MLNG.cur==='engine_cycles');
 assert.strictEqual(await page.locator('#aiw').count(),0);assert.strictEqual(await page.locator('milg-ai-widget').count(),1);
 const widget=page.locator('#mes-live-widget');
 await page.click('#mes-engine-connect');await page.fill('#mes-engine-token','wrong-token');await page.click('#mes-engine-login');await page.locator('#mes-engine-error').filter({hasText:'token'}).waitFor();
 await page.fill('#mes-engine-token',token);await page.fill('#mes-engine-actor','MES 시연 담당자');await page.click('#mes-engine-login');await page.locator('#mes-engine-dialog').waitFor({state:'hidden'});
 const selection=await page.evaluate(()=>{const t=MLNG.S.mach.tools.at(-1),rs=MLNG.S.mach.runs.filter(r=>r.tool===t.id&&!r.bad);return {tool:t.id,lot:t.lot,first:rs[0].run,middle:rs[1].run,last:rs.at(-1).run};});
 await page.selectOption('#engine-tool',selection.tool);
 await page.click(`[data-engine-select="${selection.first}"]`);
 const expected=await page.evaluate(()=>{const p=MLNG.engineWidget.payload(),s=MLNG.engineWidget.selected(),r=MLNG.S.mach.runs.find(r=>r.tool===s.tool_id&&r.run===s.run);return {payload:p,source:r.s};});
 const keys=['smcAC','smcDC','vib_table','vib_spindle','AE_table','AE_spindle'];
 assert.deepStrictEqual(expected.payload.rows[0],Object.fromEntries(keys.map(k=>[k,expected.source[k]])));
 await widget.locator('.fab').click();await page.waitForFunction(id=>document.querySelector('#mes-live-widget').result?.event_id===id,expected.payload.cycle_id);
 assert.deepStrictEqual(sent[0],expected.payload);assert.strictEqual(sent[0].lot_id,selection.lot);assert(sent[0].demo);
 assert(!Object.keys(sent[0].rows[0]).some(k=>/vb|bad|quality|defect/i.test(k)));
 const secondSelector=`[data-engine-select="${selection.last}"]`;
 await page.click(secondSelector);assert.strictEqual(await widget.locator('#evidence li').count(),0);
 await widget.locator('.fab').click();await page.waitForFunction(run=>document.querySelector('#mes-live-widget').payload?.mes_selection.run===run&&document.querySelector('#mes-live-widget').result,selection.last);
 const current=await page.evaluate(()=>document.querySelector('#mes-live-widget').payload);
 assert.notStrictEqual(current.cycle_id,expected.payload.cycle_id);assert.notDeepStrictEqual(current.rows[0],expected.payload.rows[0]);
 const result=await page.evaluate(()=>document.querySelector('#mes-live-widget').result);
 assert.deepStrictEqual(await api('cycle',current),result);
 await widget.locator('#investigate').click();await widget.locator('#message').filter({hasText:'저장 완료'}).waitFor();
 await page.evaluate(()=>MLNG.go('engine_reviews'));await page.locator('#view').filter({hasText:current.cycle_id}).waitFor();
 let history=await api('history');assert.strictEqual(history.reviews[0].event_id,current.cycle_id);assert.strictEqual(history.reviews[0].actor,'MES 시연 담당자');assert.strictEqual((await api('status')).counts.feedback,0);
 await page.evaluate(()=>MLNG.go('engine_cycles'));await page.click(secondSelector);await widget.locator('.fab').click();await page.waitForFunction(id=>document.querySelector('#mes-live-widget').result?.event_id===id,current.cycle_id);
 await page.screenshot({path:path.join(out,'mes-widget-live.png'),fullPage:true});
 // Unsupported LOT clears the selected cycle and cannot silently run MCT.
 const before=sent.length;await page.evaluate(()=>MLNG.aiContext('UNCONNECTED-HEAT-LOT'));await widget.locator('.fab').click();assert.strictEqual(sent.length,before);assert.strictEqual(await widget.locator('#evidence li').count(),0);assert((await widget.locator('#message').innerText()).includes('선택'));
 // A delayed old request must never replace a subsequently selected cycle.
 await page.click(`[data-engine-select="${selection.first}"]`);
 let intercepted;const waiting=new Promise(resolve=>intercepted=resolve);
 await page.route('**/api/cycle',async route=>{intercepted();await new Promise(r=>setTimeout(r,400));try{await route.continue();}catch{}});
 await widget.locator('.fab').click();await waiting;await page.click(secondSelector);await page.unroute('**/api/cycle');await widget.locator('.fab').click();
 await page.waitForFunction(id=>document.querySelector('#mes-live-widget').result?.event_id===id,current.cycle_id);await page.waitForTimeout(500);
 assert.strictEqual(await page.evaluate(()=>document.querySelector('#mes-live-widget').result?.event_id),current.cycle_id);
 // Missing sensors fail closed and cannot leave the prior successful evidence.
 await page.unroute('**/api/cycle');
 await page.click(`[data-engine-select="${selection.middle}"]`);
 const original=await page.evaluate(()=>{const s=MLNG.engineWidget.selected(),r=MLNG.S.mach.runs.find(r=>r.tool===s.tool_id&&r.run===s.run),v=r.s.smcDC;r.s.smcDC=null;return v;});
 const beforeMissing=(await api('status')).counts.events;
 await widget.locator('.fab').click();await widget.locator('#message').filter({hasText:'finite'}).waitFor();
 assert.strictEqual(await widget.locator('#evidence li').count(),0);assert.strictEqual((await api('status')).counts.events,beforeMissing);
 await page.evaluate(v=>{const s=MLNG.engineWidget.selected();MLNG.S.mach.runs.find(r=>r.tool===s.tool_id&&r.run===s.run).s.smcDC=v;},original);
 // Reload clears credentials; authenticated review history remains on the server.
 await page.reload();await page.waitForFunction(()=>window.MLNG?.S&&MLNG.engineWidget);
 assert.strictEqual(await page.locator('#mes-engine-token').inputValue(),'');
 history=await api('history');assert.strictEqual(history.reviews.length,1);
 assert.deepStrictEqual(external,[]);assert.deepStrictEqual(errors,[]);
 const report={passed:true,exact_selected_mes_sensors:true,no_label_inputs:true,no_saved_prediction_fallback:true,review_persisted:true,old_response_cannot_replace_new_selection:true,credentials_not_persisted:true,external_requests:external.length,source_state_unchanged:true,scope:'public_MCT_replay_actual_local_inference_not_new_quality_validation'};
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{if(browser)await browser.close();if(server)server.kill();}})().catch(e=>{console.error(e);process.exitCode=1;});
