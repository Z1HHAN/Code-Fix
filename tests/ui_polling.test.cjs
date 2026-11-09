const test=require('node:test'),assert=require('node:assert/strict');
const {createPoller}=require('../ui/app/polling.js');
function fixture(refresh=async()=>true){
  let running=false,hidden=false,id=0;const timers=new Map();
  const poller=createPoller({refresh,isRunning:()=>running,isHidden:()=>hidden,
    schedule:(fn,ms)=>{timers.set(++id,{fn,ms});return id;},cancel:id=>timers.delete(id)});
  return {poller,timers,setRunning:v=>running=v,setHidden:v=>hidden=v,
    fire:async()=>{const [[id,{fn}]]=timers;timers.delete(id);fn();await new Promise(setImmediate);},
    delay:()=>[...timers.values()][0]?.ms};
}
test('idle slows down, running stays responsive, background pauses and foreground refreshes',async()=>{
  let calls=0;const f=fixture(async()=>{calls++;return true;});
  await f.poller.start();assert.equal(f.delay(),15000);
  f.setRunning(true);await f.poller.request();assert.equal(f.delay(),1200);
  f.setHidden(true);await f.poller.visibilityChanged();assert.equal(f.timers.size,0);
  assert.equal(calls,2);f.setHidden(false);await f.poller.visibilityChanged();assert.equal(calls,3);
  f.setRunning(false);await f.fire();assert.equal(f.delay(),15000);
  f.poller.stop();assert.equal(f.timers.size,0);
});
test('failed refresh backs off to a cap and successful manual refresh resets delay',async()=>{
  let ok=false;const f=fixture(async()=>ok);f.setRunning(true);await f.poller.start();
  assert.equal(f.delay(),2400);await f.fire();assert.equal(f.delay(),4800);
  for(let i=0;i<8;i++)await f.fire();assert.equal(f.delay(),60000);
  ok=true;await f.poller.request();assert.equal(f.delay(),1200);f.poller.stop();
});
test('inflight refreshes never overlap; user actions get one fresh follow-up',async()=>{
  let release,calls=0,active=0,max=0;
  const f=fixture(async()=>{calls++;active++;max=Math.max(max,active);await new Promise(r=>release=r);active--;return true;});
  const started=f.poller.start();f.poller.request();f.poller.request();
  assert.equal(calls,1);release();await started;assert.equal(f.delay(),0);
  const next=f.fire();await new Promise(setImmediate);assert.equal(calls,2);release();await next;
  assert.equal(max,1);f.poller.stop();
});
test('background completion does not schedule polling; foreground catches up',async()=>{
  let release;const f=fixture(()=>new Promise(r=>release=r));const pending=f.poller.start();
  f.setHidden(true);f.poller.visibilityChanged();release(true);await pending;assert.equal(f.timers.size,0);
  f.setHidden(false);const resumed=f.poller.visibilityChanged();release(true);await resumed;assert.equal(f.delay(),15000);
  f.poller.stop();
});
test('thrown errors retry, stop prevents late completion from rearming',async()=>{
  const f=fixture(async()=>{throw Error('offline');});await f.poller.start();assert.equal(f.delay(),30000);f.poller.stop();
  let release;const g=fixture(()=>new Promise(r=>release=r));const work=g.poller.start();g.poller.stop();release(true);await work;assert.equal(g.timers.size,0);
});
