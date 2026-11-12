// Real HTTP + background subprocess + operator page; synthetic data in a temporary tenant only.
const fs=require('fs'),path=require('path'),assert=require('assert'),net=require('net');
const {spawn,spawnSync}=require('child_process'),{chromium}=require('playwright');
const repo=path.resolve(__dirname,'..'),ai=process.env.MILG_TEST_AI_ROOT||path.join(repo,'ai');
const python=process.env.MILG_TEST_PYTHON||'python',source=process.env.MILG_TEST_STATE||path.join(ai,'state');
const out=path.join(repo,'ai','reports','delivery-learning');fs.mkdirSync(out,{recursive:true});
const state=fs.mkdtempSync(path.join(out,'state-'));fs.copyFileSync(path.join(source,'pilot-active.json'),path.join(state,'pilot-active.json'));
const context={machine_id:'ISOLATED-E2E',part_number:'SYNTHETIC',program:'test-v1'};
const policy=path.join(state,'policy.json');fs.writeFileSync(policy,JSON.stringify({process:'pressfit',context}));
const configured=spawnSync(python,['-X','utf8','-m','mlng_ai.learning','configure','--state',state,'--input',policy],{cwd:ai,encoding:'utf8'});
assert.strictEqual(configured.status,0,configured.stderr);
const inbox=path.join(state,'inbox');fs.mkdirSync(inbox);
const cycles=[],feedback=[],lots=[];
for(let i=0;i<100;i++){
 const failed=i%20>=10,cycle='synthetic-'+i;
 cycles.push({process:'pressfit',context,cycle_id:cycle,lot_id:'LOT'+Math.floor(i/20),demo:false,
   observed_at:new Date(Date.UTC(2020,0,1,0,0,i)).toISOString(),rows:[{units:{force:'N',displacement:'mm'},
   displacement:Array.from({length:8},(_,j)=>j),force:Array.from({length:8},(_,j)=>j*(failed?100:10)+(i%5)*j*.01)}]});
 feedback.push({event_id:cycle,outcome:failed?'fail':'pass',actor:'test-inspector',evidence:'SYNTHETIC-FIXTURE',source_id:'test-inspection-'+i});
}
for(let i=0;i<5;i++)lots.push({process:'pressfit',context,lot_id:'LOT'+i,actor:'test-MES'});
for(const [name,kind,items] of [['001','cycles',cycles],['002','feedback',feedback],['003','lot_close',lots]])
 fs.writeFileSync(path.join(inbox,name+'.ready.json'),JSON.stringify({format:'milg_inbox_v1',kind,items}));
let server,browser;
(async()=>{try{
 const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p))})});
 const base='http://127.0.0.1:'+port;
 server=spawn(python,['-X','utf8','-u','-m','mlng_ai.pilot','serve','--state',state,'--port',String(port),'--auto-learn'],{cwd:ai,stdio:'ignore',env:{...process.env,OMP_NUM_THREADS:'2',OPENBLAS_NUM_THREADS:'2'}});
 for(let i=0;i<150;i++){try{if((await fetch(base+'/operations')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 const token=fs.readFileSync(path.join(state,'pilot-token.txt'),'utf8').trim(),admin=fs.readFileSync(path.join(state,'pilot-admin-token.txt'),'utf8').trim();
 const api=async(name,body={},auth=token)=>{const r=await fetch(base+'/api/'+name,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+auth},body:JSON.stringify(body)});return {status:r.status,data:await r.json()}};
 assert.strictEqual((await api('learning/configure',{process:'pressfit',context})).status,401);
 let status;
 for(let i=0;i<200;i++){status=(await api('learning/status')).data;if(status.active.some(x=>x.model_id))break;await new Promise(r=>setTimeout(r,250))}
 assert(status.active.some(x=>x.model_id),'Automatic worker did not activate the isolated fixture');
 assert.strictEqual(status.runs.length,1);assert.strictEqual(status.runs[0].status,'activated');
 const request={...cycles[0],cycle_id:'after-training',lot_id:'NEWLOT',observed_at:new Date().toISOString()};
 const inferred=await api('cycle',request);assert.strictEqual(inferred.status,200);assert(inferred.data.field_model_id);
 browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],external=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{if(new URL(route.request().url()).origin!==base){external.push(route.request().url());return route.abort()}return route.continue()});
 await page.goto(base+'/operations');await page.fill('#token',token);await page.fill('#admin',admin);await page.click('#refresh');
 await page.locator('#runs').filter({hasText:'적용 완료'}).waitFor();assert((await page.locator('#health').innerText()).includes('작동 중'));
 await page.selectOption('#process','pressfit');await page.fill('#machine',context.machine_id);await page.fill('#part',context.part_number);await page.fill('#program',context.program);
 await page.click('#pause');await page.waitForFunction(()=>document.querySelector('#notice').textContent==='완료');
 assert.strictEqual((await api('learning/status')).data.policies[0].enabled,false);
 await page.screenshot({path:path.join(out,'operations.png'),fullPage:true});
 page.once('dialog',dialog=>dialog.accept());await page.click('#rollback');
 await page.waitForFunction(()=>document.querySelector('#details').textContent.includes('Operator requested rollback'));
 assert.strictEqual((await api('learning/status')).data.active[0].model_id,null);
 assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(external,[]);
 assert.strictEqual(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}).includes(document.querySelector('#admin').value)),false);
 const report={passed:true,scope:'isolated_synthetic_operational_test_not_factory_accuracy',background_training:true,
   model_inference:true,operator_auth_separated:true,operator_pause:true,operator_rollback:true,external_requests:external.length};
 fs.writeFileSync(path.join(out,'live-verification.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{if(browser)await browser.close();if(server){if(process.platform==='win32')spawnSync('taskkill',['/PID',String(server.pid),'/T','/F'],{stdio:'ignore'});else server.kill()}}})().catch(e=>{console.error(e);process.exitCode=1});
