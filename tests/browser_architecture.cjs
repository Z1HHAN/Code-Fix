// Real browser/HTTP interaction with a disposable local store. No task is started.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const out=path.resolve(process.env.CODE_FIX_ARCH_BROWSER_OUTPUT||'runs/architecture-browser');fs.mkdirSync(out,{recursive:true});
const base=process.env.CODE_FIX_CONFIG_BROWSER_URL||'http://127.0.0.1:8769';
assert.match(base,/^http:\/\/127\.0\.0\.1:\d+$/);
const browser=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--remote-debugging-address=127.0.0.1','--remote-debugging-port=9331','--user-data-dir='+path.join(out,'profile'),'about:blank'],{windowsHide:true,stdio:'ignore'});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));let socket;const errors=[],writes=[];
const watchdog=setTimeout(()=>{browser.kill();process.exit(1);},90000);
(async()=>{
  let v;for(let i=0;i<100;i++){try{v=await(await fetch('http://127.0.0.1:9331/json/version')).json();break;}catch{}await sleep(100);}
  assert.ok(v);socket=new WebSocket(v.webSocketDebuggerUrl);await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j;});
  let id=0;const pending=new Map();socket.onmessage=e=>{const d=JSON.parse(e.data);if(d.method==='Runtime.exceptionThrown')errors.push(d.params.exceptionDetails);if(d.method==='Network.requestWillBeSent'&&d.params.request.method==='POST')writes.push(d.params.request.url);if(pending.has(d.id)){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result);}};
  const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const mid=++id;pending.set(mid,{resolve,reject});socket.send(JSON.stringify({id:mid,method,params,sessionId}));});
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
  const s=(m,p)=>send(m,p,sessionId);await s('Page.enable');await s('Runtime.enable');await s('Network.enable');await s('Emulation.setDeviceMetricsOverride',{width:1600,height:1100,deviceScaleFactor:1,mobile:false});
  const ev=async expression=>{const r=await s('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  const wait=async condition=>{for(let i=0;i<120;i++){if(await ev(condition))return;await sleep(100);}throw Error('Timeout '+condition);};
  const shot=async name=>{const r=await s('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});fs.writeFileSync(path.join(out,name+'.png'),Buffer.from(r.data,'base64'));};

  await s('Page.navigate',{url:base+'/#workspace'});await wait("document.querySelector('.architecture-canvas') && document.getElementById('connection').textContent.includes('已连接') && !refreshing");
  await ev("poller.stop()");
  const fixture={run_id:'offline-animation-fixture',status:'running',task:{mode:'repair'},result:{mode:'test_stub'}};
  const sequence=[{sequence:1,event:'started'},
    {sequence:2,event:'step_started',step_id:'a',node_id:'repairer',round:1},
    {sequence:3,event:'step_completed',step_id:'a',node_id:'repairer',round:1,status:'ok'},
    {sequence:4,event:'step_started',step_id:'t',parent_step_id:'a',node_id:'pytest',round:1},
    {sequence:5,event:'test_completed',step_id:'t',node_id:'pytest',verification_status:'failed',failure_class:'assertion_failed'},
    {sequence:6,event:'step_completed',step_id:'t',node_id:'pytest',status:'ok'},
    {sequence:7,event:'step_started',step_id:'d',parent_step_id:'t',node_id:'program_dispatch'},
    {sequence:8,event:'decision',step_id:'d',node_id:'program_dispatch',effective_action:'locate',reason:'离线注入：断言失败需定位'},
    {sequence:9,event:'step_completed',step_id:'d',node_id:'program_dispatch',status:'ok'},
    {sequence:10,event:'step_started',step_id:'l',parent_step_id:'d',node_id:'locator',round:1}];
  async function inject(n,status='running'){await ev(`renderTrace(${JSON.stringify({...fixture})},${JSON.stringify(sequence.slice(0,n))})`);}
  await ev(`display('page-title','离线注入演示 · 不是真实执行');renderTrace(${JSON.stringify({...fixture,status:'created'})},[])`);
  await inject(2);assert.equal(await ev("document.querySelector('[data-module=repairer]').classList.contains('arch-active')"),true);
  const layout=await ev(`(()=>{const n=document.querySelector('.arch-node'),r=n.getBoundingClientRect(),i=document.querySelector('.workbench-grid>.inspector').getBoundingClientRect();return {font:parseFloat(getComputedStyle(n.querySelector('strong')).fontSize),nodeWidth:r.width,inspectorWidth:i.width,zoom:getComputedStyle(document.querySelector('.architecture-canvas')).zoom};})()`);
  assert.ok(layout.font>=15);assert.ok(layout.nodeWidth>=156);assert.ok(layout.inspectorWidth>=260&&layout.inspectorWidth<=300);assert.equal(Number(layout.zoom),1);
  const overlaps=await ev(`(()=>{const a=[...document.querySelectorAll('.arch-node')].map(n=>n.getBoundingClientRect());return a.some((r,i)=>a.slice(i+1).some(s=>r.left<s.right&&r.right>s.left&&r.top<s.bottom&&r.bottom>s.top));})()`);assert.equal(overlaps,false);
  await inject(4);assert.ok(await ev("document.querySelector('.arch-transmission[data-from=a][data-to=t]')!==null"));await shot('transfer');
  await inject(10);assert.equal(await ev("document.querySelector('[data-module=locator]').classList.contains('arch-active')"),true);assert.ok(await ev("document.querySelector('.arch-transmission[data-from=d][data-to=l]')!==null"));
  async function direction(from,to,name){
    const samples=await ev(`(()=>{const packets=[...document.querySelectorAll('.arch-packet[data-from="${from}"][data-to="${to}"]')],svg=packets[0].ownerSVGElement,path=document.querySelector('.arch-transmission[data-from="${from}"][data-to="${to}"]');svg.pauseAnimations();const len=path.getTotalLength();return packets.map(packet=>{const motion=packet.querySelector('animateMotion'),begin=parseFloat(motion.getAttribute('begin')),duration=parseFloat(motion.getAttribute('dur'));return [.24,.68].map(fraction=>{const offset=fraction*duration,t=begin+offset;svg.setCurrentTime(t);const f=offset/duration,p=path.getPointAtLength(len*f),q=path.getPointAtLength(Math.min(len,len*f+.1)),m=svg.getScreenCTM().inverse().multiply(packet.getScreenCTM()),norm=Math.hypot(q.x-p.x,q.y-p.y);return {fraction:f,x:m.e,y:m.f,error:Math.hypot(m.e-p.x,m.f-p.y),dot:(m.a*(q.x-p.x)+m.b*(q.y-p.y))/norm};});});})()`);
    const length=await ev(`document.querySelector('.arch-transmission[data-from="${from}"][data-to="${to}"]').getTotalLength()`);assert.equal(samples.length,length<260?1:length<600?2:3);for(const segment of samples){assert.ok(segment[1].fraction>segment[0].fraction);for(const v of segment){assert.ok(v.error<1,JSON.stringify(v));assert.ok(v.dot>.99,JSON.stringify(v));}}
    await ev("document.querySelector('.arch-links').setCurrentTime(1.8)");
    assert.equal(await ev(`Array.from(document.querySelectorAll('.arch-packet[data-from="${from}"][data-to="${to}"]')).filter(n=>Number(getComputedStyle(n).opacity)>0).length`),samples.length);
    await shot(name);await ev("document.querySelector('.arch-links').setCurrentTime(4)");assert.equal(await ev(`Array.from(document.querySelectorAll('.arch-packet[data-from="${from}"][data-to="${to}"]')).every(n=>getComputedStyle(n).opacity==='0')`),true);
    return samples;
  }
  const forwardSamples=await direction('d','l','locator');
  assert.equal(await ev("Array.from(document.querySelectorAll('.arch-packet[data-from=d][data-to=l] animateMotion')).every(n=>parseFloat(n.getAttribute('dur'))>=2.2)"),true);
  assert.equal(await ev("Array.from(document.querySelectorAll('.arch-packet[data-from=d][data-to=l] .arch-packet-arrow')).every(n=>n.getBBox().height>=18&&getComputedStyle(n).fill==='rgb(7, 82, 223)')"),true);
  await sleep(4100);assert.equal(await ev("document.querySelectorAll('.arch-transmission').length"),0);assert.equal(await ev("document.querySelector('[data-module=locator]').classList.contains('arch-active')"),true);
  const returned=[...sequence,{sequence:11,event:'step_completed',step_id:'l',node_id:'locator',status:'ok'},{sequence:12,event:'step_started',step_id:'return',parent_step_id:'l',node_id:'program_dispatch'}];
  await ev(`renderTrace(${JSON.stringify(fixture)},${JSON.stringify(returned)})`);const returnSamples=await direction('l','return','return-route');
  assert.equal(await ev("document.querySelector('.arch-transmission[data-from=l][data-to=return]').getTotalLength()"),251);
  const visibilityRun={...fixture,run_id:'visible-duration-fixture'};
  await ev(`renderTrace(${JSON.stringify({...visibilityRun,status:'created'})},[]);renderTrace(${JSON.stringify(visibilityRun)},${JSON.stringify(sequence.slice(0,9))});renderTrace(${JSON.stringify(visibilityRun)},${JSON.stringify(sequence)})`);
  await sleep(1100);
  const wallClockVisible=await ev("Array.from(document.querySelectorAll('.arch-packet[data-from=d][data-to=l]')).filter(n=>Number(getComputedStyle(n).opacity)>.5).length");assert.ok(wallClockVisible>=1);
  await ev(`renderTrace(${JSON.stringify(visibilityRun)},${JSON.stringify([...sequence,{sequence:11,event:'observation',note:'unrelated event must not restart or remove the handoff'}])})`);
  assert.equal(await ev("document.querySelectorAll('.arch-packet[data-from=d][data-to=l]').length"),1);
  await ev("document.querySelector('[data-module=repairer]').click()");assert.match(await ev("document.querySelector('.arch-controls').textContent"),/历史查看/);assert.equal(await ev("document.querySelectorAll('.arch-active').length"),0);
  await ev("[...document.querySelectorAll('.arch-controls button')].find(x=>x.textContent==='返回当前步骤').click()");
  await ev(`renderTrace(${JSON.stringify({...fixture,status:'stopped'})},${JSON.stringify(sequence)})`);assert.equal(await ev("document.querySelectorAll('.arch-active,.arch-transmission,.arch-packet').length"),0);await shot('terminal');
  await ev("[...document.querySelectorAll('.arch-controls button')].find(x=>x.textContent==='历史回放').click()");assert.match(await ev("document.querySelector('.arch-controls').textContent"),/历史回放/);await sleep(300);await ev("[...document.querySelectorAll('.arch-controls button')].find(x=>x.textContent==='暂停回放').click()");
  assert.match(await ev("document.querySelector('.arch-controls').textContent"),/回放已暂停/);
  await ev("[...document.querySelectorAll('.arch-controls button')].find(x=>x.textContent==='继续回放').click()");
  assert.match(await ev("document.querySelector('.arch-controls [role=status]').textContent"),/1\/10/);
  assert.equal(await ev("document.querySelectorAll('.arch-packet').length"),2);
  await wait("document.querySelector('.arch-controls [role=status]').textContent.includes('2/10')");
  const replayTail={...fixture,run_id:'offline-replay-tail'};
  const tailEvents=sequence.slice(1,4);
  await ev(`renderTrace(${JSON.stringify(replayTail)},${JSON.stringify(tailEvents)});[...document.querySelectorAll('.arch-controls button')].find(x=>x.textContent==='历史回放').click()`);
  await wait("document.querySelector('.arch-controls [role=status]').textContent.includes('3/3')");
  await sleep(1100);assert.ok(await ev("[...document.querySelectorAll('.arch-packet[data-from=a][data-to=t]')].filter(n=>Number(getComputedStyle(n).opacity)>.5).length>=1"));
  await shot('replay-tail');await wait("document.querySelector('.arch-controls [role=status]').textContent.includes('回放结束')");
  assert.equal(await ev("document.querySelectorAll('.arch-packet').length"),0);
  assert.equal(await ev("document.querySelectorAll('.arch-terminal').length"),0);

  // A-01/A-02: raw baseline round stays zero; evidence routes reflect recorded paths.
  const evidenceRun={...fixture,run_id:'offline-evidence-routing'};
  const evidenceEvents=[
    {sequence:1,event:'step_started',step_id:'base',node_id:'pytest',phase:'development',round:0},
    {sequence:2,event:'test_completed',step_id:'base',node_id:'pytest',evidence_path:'test',stdout_path:'stdout.txt',verification_status:'failed'},
    {sequence:3,event:'step_completed',step_id:'base',node_id:'pytest',status:'ok'},
    {sequence:4,event:'step_started',step_id:'patch',node_id:'apply_candidate',round:1},
    {sequence:5,event:'step_completed',step_id:'patch',node_id:'apply_candidate',status:'ok',artifact_path:'patch-1/patch.diff'},
    {sequence:6,event:'step_started',step_id:'cost',node_id:'program_dispatch'},
    {sequence:7,event:'decision',step_id:'cost',node_id:'program_dispatch',counters:{cost:{status:'unknown'}}},
    {sequence:8,event:'step_completed',step_id:'cost',node_id:'program_dispatch',status:'ok'}];
  await ev(`renderTrace(${JSON.stringify({...evidenceRun,status:'created'})},[]);renderTrace(${JSON.stringify(evidenceRun)},${JSON.stringify(evidenceEvents)})`);
  assert.match(await ev("document.querySelector('[data-module=baseline] small').textContent"),/基线/);
  assert.equal(await ev("document.getElementById('architecture-flow').textContent.includes('第0轮')"),false);
  await ev("document.getElementById('actual-view').click()");
  const evidenceSamples=await ev(`(()=>{const svg=document.querySelector('.arch-links');svg.pauseAnimations();svg.setCurrentTime(1.8);return [...document.querySelectorAll('.arch-transmission[data-evidence-target]')].map(p=>{const target=p.dataset.evidenceTarget,step=p.dataset.evidenceStep,packet=document.querySelector('.arch-packet[data-evidence-step="'+step+'"][data-evidence-target="'+target+'"]'),m=svg.getScreenCTM().inverse().multiply(packet.getScreenCTM()),motion=packet.querySelector('animateMotion'),f=(1.8-parseFloat(motion.getAttribute('begin')))/2.2,point=p.getPointAtLength(p.getTotalLength()*f);return {target,step,path:p.getAttribute('d'),error:Math.hypot(m.e-point.x,m.f-point.y)};});})()`);
  assert.deepEqual([...new Set(evidenceSamples.map(x=>x.target))].sort(),['cost','diff','logs','trace']);for(const sample of evidenceSamples)assert.ok(sample.error<1,JSON.stringify(sample));
  await shot('evidence-routing');
  // A-04/A-06: click remains a result frame, new records only update the notice.
  await ev(`renderTrace(${JSON.stringify(fixture)},${JSON.stringify(sequence)});document.querySelector('[data-module=repairer]').click()`);
  const frozen=await ev("document.getElementById('trace-flow').textContent");
  await ev(`renderTrace(${JSON.stringify(fixture)},${JSON.stringify([...sequence,{sequence:11,event:'step_completed',step_id:'l',node_id:'locator',status:'ok'}])})`);
  assert.equal(await ev("document.getElementById('trace-flow').textContent"),frozen);
  assert.match(await ev("document.querySelector('.arch-new-events').textContent"),/新增 1 个事件/);await shot('new-event-notice');
  await ev("[...document.querySelectorAll('.arch-controls button')].find(x=>x.textContent==='从此步骤回放').click()");
  assert.match(await ev("document.querySelector('.arch-controls [role=status]').textContent"),/2\/11/);
  assert.equal(await ev("document.querySelector('[data-module=repairer]').classList.contains('arch-active')"),true);
  await ev("[...document.querySelectorAll('.arch-controls button')].find(x=>x.textContent==='返回当前步骤').click()");
  assert.equal(await ev("document.querySelector('.arch-new-events').hidden"),true);

  // Locate-current must use its action, even when a different control is first.
  await ev("document.querySelector('[data-module=repairer]').click();const controls=document.querySelector('.arch-controls');controls.prepend([...controls.querySelectorAll('button')].find(x=>x.textContent==='上一步'));document.getElementById('locate-last').click()");
  assert.equal(await ev("document.querySelector('.arch-controls [role=status]').textContent"),'实时事件视图');
  await ev("document.getElementById('zoom-in').click()");
  assert.equal(await ev("Number(getComputedStyle(document.querySelector('.architecture-canvas')).zoom)"),1.1);
  assert.equal(await ev("document.querySelector('.architecture-canvas').hasAttribute('data-zoom-factor')"),false);
  await ev("document.getElementById('zoom-out').click()");
  assert.equal(await ev("Number(getComputedStyle(document.querySelector('.architecture-canvas')).zoom)"),1);

  // A successful/failed recorded finish fades static highlights only. No terminal
  // packet, SMIL animation or blue executing node survives the status transition.
  const settlingSamples=[];
  for(const status of ['success','failed']){
    const r={...fixture,run_id:'offline-settle-'+status};
    await ev(`renderTrace(${JSON.stringify({...r,status:'created'})},[]);renderTrace(${JSON.stringify(r)},${JSON.stringify(sequence.slice(0,3))});renderTrace(${JSON.stringify(r)},${JSON.stringify(sequence.slice(0,4))});renderTrace(${JSON.stringify({...r,status})},${JSON.stringify([...sequence.slice(0,4),{sequence:5,event:'finished'}])})`);
    const sample=await ev(`(()=>{const paths=[...document.querySelectorAll('.arch-settling')];for(const p of paths)for(const a of p.getAnimations()){a.pause();a.currentTime=150;}return {status:${JSON.stringify(status)},paths:paths.length,opacity:paths.map(p=>Number(getComputedStyle(p).opacity)),motion:document.querySelectorAll('.arch-packet,.arch-links animateMotion,.arch-links animate,.arch-active').length,terminal:document.querySelectorAll('.arch-terminal').length};})()`);
    assert.ok(sample.paths>0);assert.ok(sample.opacity.every(x=>x>0&&x<1));assert.equal(sample.motion,0);assert.equal(sample.terminal,1);settlingSamples.push(sample);
    if(status==='success')await shot('completion-fade');
    await sleep(350);assert.equal(await ev("document.querySelectorAll('.arch-settling').length"),0);
  }

  await s('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});await ev("[...document.querySelectorAll('.arch-controls button')].find(x=>x.textContent==='返回当前步骤').click()");await inject(10);assert.equal(await ev("getComputedStyle(document.querySelector('.arch-active')).animationName"),'none');assert.equal(await ev("[...document.querySelectorAll('.arch-packet')].every(n=>getComputedStyle(n).display==='none')"),true);

  const reducedRun={...fixture,run_id:'offline-reduced-motion'};
  await ev(`renderTrace(${JSON.stringify({...reducedRun,status:'created'})},[]);renderTrace(${JSON.stringify(reducedRun)},${JSON.stringify(evidenceEvents)})`);
  assert.ok(await ev("document.querySelectorAll('.arch-transmission').length>0"));
  assert.equal(await ev("document.querySelectorAll('.arch-packet,.arch-links animateMotion,.arch-links animate').length"),0);
  await s('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  await wait("document.querySelectorAll('.arch-packet').length>0");
  await s('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  await wait("document.querySelectorAll('.arch-packet,.arch-links animateMotion,.arch-links animate').length===0");

  await s('Emulation.setDeviceMetricsOverride',{width:480,height:1000,deviceScaleFactor:1,mobile:false});assert.equal(await ev("document.documentElement.scrollWidth<=window.innerWidth"),true);await shot('narrow');
  assert.equal(errors.length,0,JSON.stringify(errors));assert.equal(writes.length,0);
  const result={scope:'browser-injected-offline-events-only; no model, Docker or real repair',passed:true,errors,writes,forwardSamples,returnSamples,evidenceSamples,wallClockVisible,settlingSamples,semanticLocateAfterReorder:true,zoomWithoutDataset:true};fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{clearTimeout(watchdog);socket?.close();browser.kill();});
