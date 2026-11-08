// Real browser/HTTP interaction with a disposable local store. No task is started.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const out=path.resolve(process.env.CODE_FIX_CONFIG_BROWSER_OUTPUT||'runs/config-console-browser');fs.mkdirSync(out,{recursive:true});
const base=process.env.CODE_FIX_CONFIG_BROWSER_URL||'http://127.0.0.1:8769';
assert.match(base,/^http:\/\/127\.0\.0\.1:\d+$/);
const browser=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--remote-debugging-address=127.0.0.1','--remote-debugging-port=9329','--user-data-dir='+path.join(out,'profile'),'about:blank'],{windowsHide:true,stdio:'ignore'});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));let socket;const errors=[],writes=[];
const watchdog=setTimeout(()=>{browser.kill();process.exit(1);},90000);
(async()=>{
  let v;for(let i=0;i<100;i++){try{v=await(await fetch('http://127.0.0.1:9329/json/version')).json();break;}catch{}await sleep(100);}
  assert.ok(v);socket=new WebSocket(v.webSocketDebuggerUrl);await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j;});
  let id=0;const pending=new Map();socket.onmessage=e=>{const d=JSON.parse(e.data);if(d.method==='Runtime.exceptionThrown')errors.push(d.params.exceptionDetails);if(d.method==='Network.requestWillBeSent'&&d.params.request.method==='POST')writes.push(d.params.request.url);if(pending.has(d.id)){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result);}};
  const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const mid=++id;pending.set(mid,{resolve,reject});socket.send(JSON.stringify({id:mid,method,params,sessionId}));});
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
  const s=(m,p)=>send(m,p,sessionId);await s('Page.enable');await s('Runtime.enable');await s('Network.enable');await s('Emulation.setDeviceMetricsOverride',{width:1600,height:1100,deviceScaleFactor:1,mobile:false});
  const ev=async expression=>{const r=await s('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  const wait=async condition=>{for(let i=0;i<120;i++){if(await ev(condition))return;await sleep(100);}throw Error('Timeout '+condition);};
  const shot=async name=>{const r=await s('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});fs.writeFileSync(path.join(out,name+'.png'),Buffer.from(r.data,'base64'));};
  await s('Page.navigate',{url:base+'/#config'});await wait("document.querySelectorAll('#binding-rows tr').length===4");
  assert.equal(await ev("document.querySelectorAll('#binding-rows select').length"),8);
  await ev("document.querySelector('[aria-label=修复员账号]').value='sf_account1';document.querySelector('[aria-label=修复员账号]').dispatchEvent(new Event('change'));document.getElementById('binding-repairer').value='sf_account1_v32';document.getElementById('binding-repairer').dispatchEvent(new Event('change'));document.getElementById('config-rounds').value=2;document.getElementById('config-rounds').dispatchEvent(new Event('input'))");
  await ev("document.getElementById('validate-config').click()");await wait("document.getElementById('config-message').textContent.includes('结构校验通过')");
  await ev("document.getElementById('save-config').click()");await wait("document.getElementById('config-draft-id').textContent.startsWith('cfg-')");const config=await ev("document.getElementById('config-draft-id').textContent");await shot('roles');
  await ev("document.getElementById('new-task').click()");assert.equal(await ev("document.getElementById('rounds').value"),'2');assert.equal(await ev("document.getElementById('rounds').disabled"),true);
  await ev("document.getElementById('create').click()");await wait("!busy && !document.getElementById('new-task-dialog').open && document.getElementById('history').value.startsWith('run-')");
  const rid=await ev("document.getElementById('history').value");const run=await(await fetch(base+'/api/v3/tasks/'+rid)).json();assert.equal(run.task.config_id,config);assert.equal(run.status,'created');
  await ev("document.getElementById('show-config').click()");await wait("document.getElementById('saved-snapshot').textContent.includes('sf_account1_v32')");await shot('snapshot');
  await ev("document.querySelector('[data-config-tab=conditions]').click()");await wait("document.getElementById('account-rows').children.length>0");
  assert.equal(await ev("document.querySelector('.readiness-table tbody').children.length"),5);
  await ev("document.querySelectorAll('.readiness-table .readiness-status')[1].click()");
  assert.match(await ev("document.querySelector('.readiness-detail').textContent"),/账号2/);
  assert.match(await ev("document.querySelector('.readiness-detail').textContent"),/调用授权.*凭据读取.*计费价格.*计费输出上限/s);
  await shot('conditions');
  await ev("document.querySelector('[data-config-tab=roles]').click();document.getElementById('config-calls').value=10;document.getElementById('config-calls').dispatchEvent(new Event('input'))");assert.equal(await ev("document.getElementById('use-saved-config').disabled"),true);
  assert.equal((await(await fetch(base+'/api/v3/tasks/'+rid)).json()).task.max_calls,12);
  await s('Emulation.setDeviceMetricsOverride',{width:720,height:1000,deviceScaleFactor:1,mobile:false});await shot('narrow');
  assert.equal(errors.length,0,JSON.stringify(errors));assert.ok(writes.every(url=>!url.endsWith('/start')));
  const result={scope:'local-browser-no-model-no-docker',run_id:rid,config_id:config,passed:true,errors,writes};fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{clearTimeout(watchdog);socket?.close();browser.kill();});
