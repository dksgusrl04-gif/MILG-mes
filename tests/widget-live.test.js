// Real Python engine -> independent widget -> server review log. No production-state writes.
const fs=require('fs'),path=require('path'),assert=require('assert'),{spawn}=require('child_process');
const {chromium}=require('playwright');
const repo=path.resolve(__dirname,'..'),root=path.join(repo,'ai'),out=path.join(root,'reports','widget-engine');
fs.mkdirSync(out,{recursive:true});const state=fs.mkdtempSync(path.join(out,'verification-'));
fs.copyFileSync(path.join(root,'state','pilot-active.json'),path.join(state,'pilot-active.json'));
const port=Number(process.env.MILG_TEST_PORT||8937),url='http://127.0.0.1:'+port;
const child=spawn('python',['-X','utf8','-m','mlng_ai.pilot','serve','--state',state,'--port',String(port),'--allow-origin','https://customer-mes.example'],{cwd:root,stdio:'ignore'});
let browser;
(async()=>{
 try{
  for(let i=0;i<70;i++){try{if((await fetch(url)).ok)break;}catch(e){}await new Promise(r=>setTimeout(r,100));}
  const token=fs.readFileSync(path.join(state,'pilot-token.txt'),'utf8').trim();
  browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.fill('#token',token);
  const pointer=JSON.parse(fs.readFileSync(path.join(state,'pilot-active.json'),'utf8'));
  const payload=JSON.parse(fs.readFileSync(path.join(pointer.release,'examples',(process.env.MILG_TEST_PROCESS||'mct_hmotp')+'.json'),'utf8'));
  payload.cycle_id='widget-test-'+Date.now();payload.demo=true;
  await page.fill('#payload',JSON.stringify(payload));await page.click('#submit');
  const widget=page.locator('milg-ai-widget');await page.waitForFunction(id=>document.querySelector('milg-ai-widget')?.shadowRoot.querySelector('#title').textContent.includes(id),payload.cycle_id);
  assert((await widget.locator('#title').innerText()).includes(payload.cycle_id));
  await widget.locator('#investigate').click();await widget.locator('#message').filter({hasText:'저장 완료'}).waitFor();
  const result=await page.evaluate(async()=>document.querySelector('milg-ai-widget').request('/api/status',{}));
  assert.strictEqual(result.counts.reviews,1);assert.strictEqual(result.counts.feedback,0);
  if(process.env.MILG_TEST_PROCESS==='wld_rsw_scenario'){
    const missing=structuredClone(payload);missing.cycle_id+='-missing';missing.rows[0][Object.keys(missing.rows[0])[0]]=null;
    const response=await page.evaluate(async p=>document.querySelector('milg-ai-widget').analyze(p),missing);
    assert.strictEqual(response.rows[0].decision,'UNKNOWN');
    assert((await widget.locator('#message').innerText()).includes('센서 누락'));
    assert.strictEqual(await widget.locator('#evidence li').count(),0);
    await page.evaluate(async p=>document.querySelector('milg-ai-widget').analyze(p),payload);
  }
  await widget.screenshot({path:path.join(out,'live-widget.png')});
  const good=await fetch(url+'/api/status',{method:'OPTIONS',headers:{Origin:'https://customer-mes.example'}});assert.strictEqual(good.status,204);assert.strictEqual(good.headers.get('Access-Control-Allow-Origin'),'https://customer-mes.example');
  const bad=await fetch(url+'/api/status',{method:'OPTIONS',headers:{Origin:'https://not-authorized.example'}});assert.strictEqual(bad.status,403);
  await page.route('**/api/cycle',async route=>route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Unsupported process'})}));
  const failed=await page.evaluate(async p=>{try{await document.querySelector('milg-ai-widget').analyze({...p,cycle_id:'bad-widget-test',process:'unsupported'});}catch(e){return e.message;}},payload);
  assert.strictEqual(failed,'Unsupported process');assert.strictEqual(await widget.locator('#evidence li').count(),0);assert(await widget.locator('.actions').isHidden());
  assert.deepStrictEqual(errors,[]);
  console.log('widget-live: real inference, matching cycle, persistent review, no quality-label contamination, CORS allowlist, failed request clears stale evidence: PASS');
 }finally{if(browser)await browser.close();child.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
