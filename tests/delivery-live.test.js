// Presentation UI + real local model + HTTP recovery. Never writes production state.
const fs=require('fs'),path=require('path'),assert=require('assert'),net=require('net');
const {spawn}=require('child_process'),{chromium}=require('playwright');
const repo=path.resolve(__dirname,'..'),ai=path.join(repo,'ai'),out=path.join(ai,'reports','delivery-live');
fs.mkdirSync(out,{recursive:true});const state=fs.mkdtempSync(path.join(out,'state-'));
fs.copyFileSync(path.join(ai,'state','pilot-active.json'),path.join(state,'pilot-active.json'));
let child,browser,port,base;
async function start(){child=spawn('python',['-X','utf8','-u','-m','mlng_ai.pilot','serve','--state',state,'--port',String(port)],{cwd:ai,stdio:'ignore',env:{...process.env,OMP_NUM_THREADS:'2',OPENBLAS_NUM_THREADS:'2'}});for(let i=0;i<100;i++){try{if((await fetch(base+'/demo')).ok)return;}catch{}await new Promise(r=>setTimeout(r,100));}throw Error('Server did not start');}
async function stop(){if(!child)return;const p=child;child=null;await new Promise(resolve=>{p.once('exit',resolve);p.kill();});}
(async()=>{try{
  port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});base='http://127.0.0.1:'+port;
  await start();const token=fs.readFileSync(path.join(state,'pilot-token.txt'),'utf8').trim();
  const call=async(name,body={},auth=token)=>{const r=await fetch(base+'/api/'+name,{method:'POST',headers:{Authorization:'Bearer '+auth,'Content-Type':'application/json'},body:JSON.stringify(body)});return {status:r.status,data:await r.json()};};
  assert.strictEqual((await call('example',{process:'wld_rsw_scenario'},'wrong-token')).status,401);
  assert.strictEqual((await call('example',{process:'../../pilot-token'})).status,400);
  browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1536,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/demo');await page.fill('#token',token);await page.click('#connect');await page.locator('#process:not([disabled])').waitFor();
  await page.selectOption('#process','wld_rsw_scenario');await page.click('#load');await page.locator('#analyze:not([disabled])').waitFor();await page.click('#analyze');await page.waitForFunction(()=>document.getElementById('analysisState').textContent.startsWith('분석 완료'));
  const widget=page.locator('milg-ai-widget');await widget.locator('#investigate').click();await widget.locator('#message').filter({hasText:'저장 완료'}).waitFor();
  await page.click('#missing');await page.waitForFunction(()=>document.getElementById('analysisState').textContent.includes('판단 불가'));
  assert.strictEqual(await widget.locator('#evidence li').count(),0);
  await page.click('#analyze');await page.waitForFunction(()=>document.getElementById('analysisState').textContent.includes('분석 완료'));
  await page.screenshot({path:path.join(out,'presentation-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'presentation-mobile.png'),fullPage:true});
  // Concurrent, independent cycles; state remains isolated. These are runtime
  // tests of example inputs, not additional independent quality observations.
  const example=(await call('example',{process:'wld_rsw_scenario'})).data;
  const latencies=[];
  for(let batch=0;batch<5;batch++)await Promise.all(Array.from({length:5},async(_,i)=>{const p={...example,cycle_id:'load-'+batch+'-'+i};const t=performance.now();const r=await call('cycle',p);latencies.push(performance.now()-t);assert.strictEqual(r.status,200);assert.strictEqual(r.data.event_id,p.cycle_id);}));
  const before=(await call('status')).data;assert.strictEqual(before.counts.reviews,1);assert.strictEqual(before.counts.feedback,0);
  await stop();const failed=await page.evaluate(async p=>{try{await document.querySelector('milg-ai-widget').analyze(p);return false;}catch{return true;}},example);assert(failed);assert.strictEqual(await widget.locator('#evidence li').count(),0);
  await start();assert.strictEqual(fs.readFileSync(path.join(state,'pilot-token.txt'),'utf8').trim(),token);
  assert.deepStrictEqual((await call('status')).data.counts,before.counts);
  const recovered=await page.evaluate(p=>document.querySelector('milg-ai-widget').analyze(p),example);assert.strictEqual(recovered.process,'wld_rsw_scenario');
  assert.deepStrictEqual(errors,[]);latencies.sort((a,b)=>a-b);
  const report={passed:true,scope:'isolated_public_example_http_runtime_not_field_validation',concurrent_requests:5,requests:25,p95_ms:Math.round(latencies[Math.ceil(latencies.length*.95)-1]),restart_preserves_records:true,disconnect_clears_evidence:true,source_state_unchanged:true};
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{if(browser)await browser.close();await stop();}})().catch(e=>{console.error(e);process.exitCode=1;});
