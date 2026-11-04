// Read-only real Edge/HTTP check against explicitly seeded offline fixtures.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const out=path.resolve(process.env.CODE_FIX_TRACE_BROWSER_OUTPUT || 'runs/trace-panel-browser');assert.ok(!fs.existsSync(out));fs.mkdirSync(out,{recursive:true});
const fixture=JSON.parse(fs.readFileSync('runs/trace-panel-platform/seed.json','utf8'));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const browser=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',[
  '--headless=new','--disable-gpu','--no-first-run','--disable-background-networking',
  '--remote-debugging-address=127.0.0.1','--remote-debugging-port=9326',
  '--user-data-dir='+path.join(out,'profile'),'about:blank'],{windowsHide:true,stdio:'ignore'});
let socket;
(async()=>{
  let version;
  for(let i=0;i<100;i++){try{version=await(await fetch('http://127.0.0.1:9326/json/version')).json();break;}catch{}await sleep(100);}
  assert.ok(version?.webSocketDebuggerUrl);
  socket=new WebSocket(version.webSocketDebuggerUrl);await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j;});
  let id=0;const pending=new Map(),writes=[];
  socket.onmessage=event=>{const data=JSON.parse(event.data);if(data.method==='Network.requestWillBeSent'&&data.params.request.method!=='GET')writes.push(data.params.request);
    if(pending.has(data.id)){const {resolve,reject}=pending.get(data.id);pending.delete(data.id);data.error?reject(Error(JSON.stringify(data.error))):resolve(data.result);}};
  const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const mid=++id;pending.set(mid,{resolve,reject});socket.send(JSON.stringify({id:mid,method,params,sessionId}));});
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
  await send('Page.enable',{},sessionId);await send('Network.enable',{},sessionId);
  const evaluate=async expression=>{const v=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},sessionId);if(v.exceptionDetails)throw Error(JSON.stringify(v.exceptionDetails));return v.result?.value;};
  const wait=async expression=>{for(let i=0;i<150;i++){if(await evaluate(expression))return;await sleep(100);}throw Error('Timed out: '+expression);};
  await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1100,deviceScaleFactor:1,mobile:false},sessionId);
  await send('Page.navigate',{url:'http://127.0.0.1:8767/'},sessionId);
  await wait("document.getElementById('connection')?.textContent.includes('已连接')");
  await wait("document.getElementById('history')?.options.length===2&&document.querySelectorAll('.trace-node').length>0");
  await evaluate(`document.getElementById('history').value='${fixture.trace_run}';document.getElementById('history').onchange()`);
  await wait("document.querySelectorAll('.trace-node').length===3");
  assert.match(await evaluate("document.getElementById('trace-flow').textContent"),/离线／模拟/);
  await evaluate("[...document.querySelectorAll('.trace-node')].find(x=>x.textContent.includes('程序调度')).click()");
  assert.match(await evaluate("document.getElementById('trace-detail').textContent"),/模拟预算不足/);
  await evaluate("[...document.querySelectorAll('.trace-file')].find(x=>x.textContent.endsWith('stdout.txt')).click()");
  await wait("document.querySelector('.trace-artifact')?.textContent.includes('离线展示夹具日志')");
  await sleep(1500);
  assert.match(await evaluate("document.querySelector('.trace-artifact').textContent"),/离线展示夹具日志/);
  let shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true},sessionId);fs.writeFileSync(path.join(out,'trace-panel.png'),Buffer.from(shot.data,'base64'));
  await send('Page.reload',{ignoreCache:true},sessionId);
  await wait(`document.getElementById('history')?.value==='${fixture.trace_run}'&&document.querySelectorAll('.trace-node').length===3`);
  assert.equal((await evaluate("fetch('/api/v3/tasks').then(r=>r.json())")).length,2);
  await evaluate(`document.getElementById('history').value='${fixture.legacy_run}';document.getElementById('history').onchange()`);
  await wait("document.querySelectorAll('.trace-node').length===1");
  assert.match(await evaluate("document.getElementById('trace-flow').textContent"),/不补造连线/);
  assert.equal(await evaluate("document.querySelectorAll('.trace-edge').length"),0);
  assert.equal(await evaluate("document.getElementById('decisions').textContent.includes('undefined')"),false);
  assert.match(await evaluate("document.getElementById('summary').textContent"),/历史记录未提供说明/);
  await send('Emulation.setDeviceMetricsOverride',{width:420,height:900,deviceScaleFactor:1,mobile:false},sessionId);
  assert.equal(await evaluate('document.documentElement.scrollWidth<=window.innerWidth'),true);
  shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true},sessionId);fs.writeFileSync(path.join(out,'legacy-narrow.png'),Buffer.from(shot.data,'base64'));
  assert.deepEqual(writes,[]);
  const report={browser:version.Browser,scope:'real browser and HTTP, offline display fixtures only',checks:{node_selection:true,artifact_read:true,poll_preserves_viewer:true,reload_preserves_run:true,legacy_without_fabricated_edges:true,narrow_without_horizontal_overflow:true,no_write_requests:true},fixture};
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));await send('Browser.close');
})().catch(error=>{fs.writeFileSync(path.join(out,'failure.txt'),String(error.stack));console.error(error);process.exitCode=1;}).finally(()=>{socket?.close();browser.kill();});
