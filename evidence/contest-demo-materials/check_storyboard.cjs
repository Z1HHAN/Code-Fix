// Render the offline storyboard in Edge. Never contact the application or a model.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),{pathToFileURL}=require('node:url');
const root=path.resolve(__dirname,'../..'),out=path.join(__dirname,'storyboard-preview');fs.mkdirSync(out,{recursive:true});
const profile=path.join(root,'runs/storyboard-edge-profile');
const browser=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--remote-debugging-address=127.0.0.1','--remote-debugging-port=9336','--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:'ignore'});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));let socket;const errors=[],requests=[];
const watchdog=setTimeout(()=>{browser.kill();process.exit(1)},90000);
(async()=>{
 let version;for(let i=0;i<100;i++){try{version=await(await fetch('http://127.0.0.1:9336/json/version')).json();break}catch{}await sleep(100)}assert.ok(version,'Edge debugging endpoint');
 socket=new WebSocket(version.webSocketDebuggerUrl);await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j});
 let seq=0;const pending=new Map();socket.onmessage=e=>{const d=JSON.parse(e.data);if(d.method==='Runtime.exceptionThrown')errors.push(d.params.exceptionDetails);if(d.method==='Network.requestWillBeSent')requests.push(d.params.request.url);if(pending.has(d.id)){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
 const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params,sessionId}))});
 const {targetId}=await send('Target.createTarget',{url:'about:blank'});const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});const s=(m,p)=>send(m,p,sessionId);
 await s('Page.enable');await s('Runtime.enable');await s('Network.enable');await s('Emulation.setDeviceMetricsOverride',{width:1280,height:800,deviceScaleFactor:1,mobile:false});
 const ev=async expression=>{const r=await s('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
 await s('Page.navigate',{url:pathToFileURL(path.join(__dirname,'technical-storyboard.html')).href});
 for(let i=0;i<100;i++){if(await ev("document.querySelectorAll('section').length===12"))break;await sleep(100)}
 assert.equal(await ev("document.querySelectorAll('section').length"),12);
 const bounds=[];
 for(let i=0;i<12;i++){
  await ev(`at=${i};step=Number(slides[at].dataset.maxStep);draw()`);await sleep(250);
  bounds.push(...await ev(`Array.from(slides[at].querySelectorAll('text')).flatMap(e=>{const b=e.getBBox();return b.x < -1||b.y < -1||b.x+b.width >1281||b.y+b.height >721?[{slide:${i+1},text:e.textContent,bounds:{x:b.x,y:b.y,width:b.width,height:b.height}}]:[]})`));
  const png=await s('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(out,String(i+1).padStart(2,'0')+'.png'),Buffer.from(png.data,'base64'));
 }
 await ev('at=3;step=0;draw()');assert.equal(await ev("slides[at].querySelectorAll('.hidden-step').length"),4);
 await ev("document.querySelector('#next').click()");assert.equal(await ev("slides[at].querySelectorAll('.hidden-step').length"),3);
 await ev("document.querySelector('#expand').click()");assert.equal(await ev("slides[at].querySelectorAll('.hidden-step').length"),0);
 await ev('at=6;step=0;draw()');assert.match(await ev("document.querySelector('#fixed-model-count').textContent"),/0次$/);
 await ev("document.querySelector('#next').click()");assert.match(await ev("document.querySelector('#fixed-model-count').textContent"),/1次$/);
 await ev("document.querySelector('#next').click()");assert.match(await ev("document.querySelector('#fixed-model-count').textContent"),/3次$/);assert.match(await ev("document.querySelector('#dynamic-model-count').textContent"),/1次$/);
 const result={slides:12,js_errors:errors,text_outside_canvas:bounds,network_requests:requests.filter(x=>/^https?:/.test(x)),step_controls:'passed',task_starts:0,model_requests:0,scope:'offline visual storyboard only; not a PPTX or real UI-start acceptance'};
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2));assert.deepEqual(errors,[]);assert.deepEqual(bounds,[]);assert.deepEqual(result.network_requests,[]);console.log(JSON.stringify(result));
})().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>{clearTimeout(watchdog);if(socket)socket.close();browser.kill()});
