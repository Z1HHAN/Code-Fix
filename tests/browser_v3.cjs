// Real Edge -> UI -> HTTP -> actual frozen pytest, with a new browser profile.
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const out = path.resolve(process.env.CODE_FIX_BROWSER_OUTPUT || 'runs/v3-browser');
const base = process.env.CODE_FIX_BROWSER_BASE_URL || 'http://127.0.0.1:8765/';
const targetUrl = new URL(base);
assert.ok(targetUrl.protocol === 'http:' && ['127.0.0.1','localhost'].includes(targetUrl.hostname), 'Local server required');
assert.ok(!fs.existsSync(out), 'Use a fresh output directory to preserve earlier browser evidence');
fs.mkdirSync(out, {recursive:true});
const sleep = ms => new Promise(r => setTimeout(r, ms));
const browser = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
  '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=9325',
  '--user-data-dir='+path.join(out, 'profile'), 'about:blank'], {windowsHide:true, stdio:'ignore'});
let socket;
(async () => {
  let version;
  for(let i=0;i<100;i++) {
    try {version=await (await fetch('http://127.0.0.1:9325/json/version')).json();break;} catch {}
    await sleep(100);
  }
  assert.ok(version?.webSocketDebuggerUrl, 'Edge debugger did not start');
  socket = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((resolve,reject) => {socket.onopen=resolve;socket.onerror=reject;});
  let id=0; const pending=new Map();
  socket.onmessage=event => {const data=JSON.parse(event.data);if(pending.has(data.id)){
    const {resolve,reject}=pending.get(data.id);pending.delete(data.id);
    data.error ? reject(new Error(JSON.stringify(data.error))) : resolve(data.result);
  }};
  const send=(method,params={},sessionId) => new Promise((resolve,reject)=>{
    const mid=++id; pending.set(mid,{resolve,reject});socket.send(JSON.stringify({id:mid,method,params,sessionId}));
  });
  const {targetId}=await send('Target.createTarget',{url:base});
  const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
  await send('Page.enable',{},sessionId);
  await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1100,deviceScaleFactor:1,mobile:false},sessionId);
  const evaluate=async expression => {
    const value=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},sessionId);
    if(value.exceptionDetails) throw new Error(JSON.stringify(value.exceptionDetails));
    return value.result?.value;
  };
  const wait=async expression => {for(let i=0;i<300;i++){if(await evaluate(expression))return;await sleep(100);}throw new Error('Timed out: '+expression);};
  await wait("document.getElementById('connection')?.textContent.includes('已连接')");
  await evaluate("document.getElementById('new-task').click()");
  await evaluate("document.getElementById('create').click()");
  await wait("document.getElementById('history')?.value.startsWith('run-') && !document.getElementById('start').disabled");
  const rid=await evaluate("document.getElementById('history').value");
  await evaluate("document.getElementById('start').click()");
  await wait("document.getElementById('status')?.textContent.includes('stopped')");
  assert.ok(await evaluate("document.getElementById('development').textContent.includes('test_1900_is_not_leap')"));
  assert.equal(await evaluate("document.getElementById('final').textContent"), '未运行');
  const count=await evaluate("document.getElementById('history').options.length");
  await send('Page.reload',{ignoreCache:true},sessionId);
  await wait("document.getElementById('status')?.textContent.includes('stopped')");
  assert.equal(await evaluate("document.getElementById('history').value"),rid);
  assert.equal(await evaluate("document.getElementById('history').options.length"),count);
  const screenshot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true},sessionId);
  fs.writeFileSync(path.join(out,'baseline.png'),Buffer.from(screenshot.data,'base64'));
  const development=await evaluate("document.getElementById('development').textContent");
  const projects=await evaluate("Array.from(document.getElementById('project').options, option => option.value)");
  for (const project of ['leap','qb-get_factors','qb-gcd','qb-max_sublist_sum']) assert.ok(projects.includes(project));
  assert.equal(projects.length, 41);
  assert.ok(await evaluate("Array.from(document.getElementById('project').options).filter(option=>option.disabled).length === 2"));
  await evaluate("document.getElementById('new-task').click()");
  await evaluate("document.getElementById('project').value='qb-quicksort';document.getElementById('project').dispatchEvent(new Event('change'))");
  assert.ok(await evaluate("document.getElementById('description').value.includes('quicksort')"));
  await evaluate("document.getElementById('create').click()");
  await wait(`document.getElementById('history')?.value !== '${rid}' && !document.getElementById('start').disabled`);
  const publicRid=await evaluate("document.getElementById('history').value");
  await evaluate("document.getElementById('start').click()");
  await wait("document.getElementById('status')?.textContent.includes('blocked')");
  const publicRun=await evaluate(`fetch('/api/v3/tasks/${publicRid}').then(response => response.json())`);
  assert.equal(publicRun.task.project, 'qb-quicksort');
  assert.equal(publicRun.result.model_api_called, false);
  assert.equal(publicRun.result.tool_calls, 0);
  assert.equal(publicRun.result.final_result, 'not_run');
  assert.ok(await evaluate("document.getElementById('summary').textContent.includes('隔离')"));
  const publicScreenshot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true},sessionId);
  fs.writeFileSync(path.join(out,'public-blocked.png'),Buffer.from(publicScreenshot.data,'base64'));
  await send('Page.reload',{ignoreCache:true},sessionId);
  await wait("document.getElementById('status')?.textContent.includes('blocked')");
  assert.equal(await evaluate("document.getElementById('history').value"), publicRid);
  assert.equal(await evaluate("document.getElementById('history').options.length"), count+1);
  const evidence={browser:version.Browser,run_id:rid,checks:{real_page_created_task:true,real_http_start:true,
    real_pytest_failure_shown:true,final_not_run:true,refresh_preserved_run:true,refresh_created_no_duplicate:true,
    public_projects_selectable:true,public_task_created:true,public_execution_blocked_without_docker:true,
    public_model_and_tool_calls_zero:true,public_selection_survived_refresh:true,
    all_40_public_tasks_listed:true,two_native_skip_policy_tasks_disabled:true},
    projects,public_run_id:publicRid,public_result:publicRun.result,
    status:await evaluate("document.getElementById('status').textContent"),
    development,
    completed_at:new Date().toISOString(),scope:'real reviewed leap baseline and blocked public-task UI only; no public code, model or candidate execution'};
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(evidence,null,2));
  console.log(JSON.stringify(evidence,null,2));
  await send('Browser.close');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{socket?.close();browser.kill();});
