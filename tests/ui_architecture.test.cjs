const {test}=require('node:test'),assert=require('node:assert/strict');
const trace=require('../ui/app/trace.js'),arch=require('../ui/app/architecture.js');
const run={run_id:'offline-one',status:'running',task:{mode:'repair'},result:{mode:'test_stub'}};
const events=[{sequence:1,event:'started'},
 {sequence:2,event:'step_started',step_id:'a',node_id:'repairer',round:1},
 {sequence:3,event:'step_completed',step_id:'a',node_id:'repairer',round:1,status:'ok'},
 {sequence:4,event:'step_started',step_id:'b',parent_step_id:'a',node_id:'pytest',round:1},
 {sequence:5,event:'test_completed',step_id:'b',node_id:'pytest',verification_status:'failed',failure_class:'assertion_failed'},
 {sequence:6,event:'step_completed',step_id:'b',node_id:'pytest',status:'ok'},
 {sequence:7,event:'step_started',step_id:'d',parent_step_id:'b',node_id:'program_dispatch'},
 {sequence:8,event:'decision',step_id:'d',node_id:'program_dispatch',effective_action:'locate'},
 {sequence:9,event:'step_completed',step_id:'d',node_id:'program_dispatch',status:'ok'},
 {sequence:10,event:'step_started',step_id:'l',parent_step_id:'d',node_id:'locator',round:1}];
class E{constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.style={};this.dataset={};this.textContent='';}append(...xs){this.children.push(...xs);}prepend(...xs){this.children.unshift(...xs);}replaceChildren(...xs){this.children=xs;}setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k];}}
function setup(){const doc={createElement:t=>new E(t),createElementNS:(ns,t)=>new E(t)},host=new E('div'),selections=[];const render=arch.mount(doc,host,trace,(...args)=>selections.push(args));const walk=n=>[n,...n.children.flatMap(walk)];return{render,host,selections,find:fn=>walk(host).filter(fn),cleanup:()=>render({...run,run_id:'empty',status:'created'},[])};}
function withClock(check){
 const original={setTimeout,clearTimeout,now:Date.now},pending=new Map();let now=1000,id=0;
 global.setTimeout=(fn,delay)=>{pending.set(++id,{fn,at:now+delay});return id;};global.clearTimeout=id=>pending.delete(id);Date.now=()=>now;
 const advance=ms=>{const end=now+ms;let count=0;while(true){const next=[...pending].sort((a,b)=>a[1].at-b[1].at)[0];if(!next||next[1].at>end)break;assert.ok(count++<1000);pending.delete(next[0]);now=next[1].at;next[1].fn();}now=end;};
 try{check(advance);}finally{global.setTimeout=original.setTimeout;global.clearTimeout=original.clearTimeout;Date.now=original.now;}
}

test('replay pause resumes the same prefix and remaining arrow travel, including after polling',()=>withClock(advance=>{
 const ui=setup();ui.render({...run,status:'stopped'},events);
 ui.find(n=>n.textContent==='历史回放')[0].onclick();advance(1100);
 assert.equal(ui.find(n=>n.attributes.class==='arch-packet').length,3);
 ui.find(n=>n.textContent==='暂停回放')[0].onclick();const prefix=ui.selections.at(-1)[2].length;
 advance(10000);ui.render({...run,status:'stopped',result:{mode:'test_stub',note:'poll'}},events);
 ui.find(n=>n.textContent==='继续回放')[0].onclick();assert.equal(ui.selections.at(-1)[2].length,prefix);
 const motion=ui.find(n=>n.tagName==='animateMotion')[0];assert.equal(motion.attributes.begin,'-1.1s');
 advance(2699);assert.equal(ui.selections.at(-1)[2].length,1);advance(1);assert.equal(ui.selections.at(-1)[2].length,2);ui.cleanup();
}));

test('entry replay illuminates both entry nodes and gives every segment a directed arrow',()=>withClock(()=>{
 const ui=setup();ui.render({...run,status:'stopped'},events);ui.find(n=>n.textContent==='历史回放')[0].onclick();
 for(const key of ['entry','service'])assert.ok(ui.find(n=>n.dataset.module===key)[0].className.includes('arch-active'));
 const packets=ui.find(n=>n.attributes.class==='arch-packet');assert.equal(packets.length,3);
 for(const packet of packets){assert.ok(packet.children.some(n=>n.attributes.class==='arch-packet-arrow'));assert.equal(packet.children.find(n=>n.tagName==='animateMotion').attributes.rotate,'auto');}ui.cleanup();
}));

test('last started event retains the full handoff window then ends without claiming task completion',()=>withClock(advance=>{
 const ui=setup();const page=[events[1],events[2],events[3]];ui.render(run,page);ui.find(n=>n.textContent==='历史回放')[0].onclick();
 advance(1300);assert.equal(ui.selections.at(-1)[2].length,3);assert.ok(ui.find(n=>n.attributes['data-from']==='a'&&n.attributes.class==='arch-packet').length);
 advance(3799);assert.equal(ui.find(n=>n.textContent==='暂停回放')[0].disabled,false);
 advance(1);assert.equal(ui.find(n=>n.attributes.class==='arch-packet').length,0);assert.equal(ui.find(n=>n.textContent==='重新回放').length,1);
 assert.equal(ui.find(n=>n.className==='arch-terminal').length,0);ui.cleanup();
}));

test('completion metadata advances promptly while its evidence arrows preserve elapsed travel',()=>withClock(advance=>{
 const ui=setup();ui.render(run,[events[1],events[2],events[3],{sequence:5,event:'observation'}]);ui.find(n=>n.textContent==='历史回放')[0].onclick();
 advance(650);assert.equal(ui.selections.at(-1)[2].length,2);advance(650);assert.equal(ui.selections.at(-1)[2].length,3);
 const packet=ui.find(n=>n.attributes['data-evidence-step']==='a'&&n.attributes.class==='arch-packet')[0];assert.equal(packet.children.find(n=>n.tagName==='animateMotion').attributes.begin,'-0.65s');ui.cleanup();
}));

test('finished event removes motion and switching run cancels replay and resume state',()=>withClock(advance=>{
 const ui=setup();ui.render({...run,status:'stopped'},[{event:'started'},{event:'finished'}]);ui.find(n=>n.textContent==='历史回放')[0].onclick();advance(3800);
 assert.equal(ui.find(n=>n.attributes.class==='arch-packet').length,0);assert.equal(ui.find(n=>n.className==='arch-terminal').length,1);
 advance(650);ui.find(n=>n.textContent==='重新回放')[0].onclick();ui.find(n=>n.textContent==='暂停回放')[0].onclick();
 ui.render({...run,run_id:'other',status:'created'},[]);const count=ui.selections.length;advance(20000);assert.equal(ui.selections.length,count);assert.equal(ui.find(n=>n.textContent==='继续回放').length,0);ui.cleanup();
}));
test('layout maps baseline and final separately; no invented parent links',()=>{
 const p=arch.projection(run,[{event:'step_started',step_id:'a',node_id:'pytest',phase:'development',round:0},{event:'step_started',step_id:'b',node_id:'pytest',phase:'final_check'}],trace);
 assert.ok(p.mapped.has('baseline')&&p.mapped.has('final'));assert.equal(p.edges.length,0);assert.equal(p.mapped.has('locator'),false);
});
test('new actual edges transmit once, unchanged polls do not rebuild, terminal stops all motion',()=>{
 const ui=setup();ui.render(run,events.slice(0,3));ui.render(run,events.slice(0,4));
 assert.ok(ui.find(n=>n.attributes.class==='arch-transmission'&&n.attributes['data-from']==='a'&&n.attributes['data-to']==='b').length);
 assert.equal(ui.find(n=>n.dataset.module==='pytest')[0].className.includes('arch-active'),true);
 const count=ui.selections.length;ui.render(run,events.slice(0,4));assert.equal(ui.selections.length,count);
 ui.render({...run,status:'stopped'},events.slice(0,4));assert.equal(ui.find(n=>n.attributes.class==='arch-transmission').length,0);assert.equal(ui.find(n=>n.className?.includes('arch-active')).length,0);ui.cleanup();
});
test('locator only lights when actually invoked and failure survives completion',()=>{
 const ui=setup();ui.render(run,events.slice(0,9));assert.equal(ui.find(n=>n.dataset.module==='locator')[0].disabled,true);
 ui.render(run,events);assert.equal(ui.find(n=>n.dataset.module==='locator')[0].className.includes('arch-active'),true);
 assert.ok(ui.find(n=>n.dataset.module==='pytest')[0].className.includes('arch-failed'));ui.cleanup();
});
test('history selection freezes prefix and evidence inspector, new live events do not pull it back',()=>{
 const ui=setup();ui.render(run,events);ui.find(n=>n.dataset.module==='repairer')[0].onclick();
 const selected=ui.selections.at(-1);assert.equal(selected[0],'a');assert.equal(selected[2].at(-1).sequence,3);
 const count=ui.selections.length;ui.render(run,[...events,{sequence:11,event:'step_completed',step_id:'l',node_id:'locator',status:'ok'}]);assert.equal(ui.selections.length,count);
 ui.find(n=>n.textContent==='返回当前步骤')[0].onclick();assert.equal(ui.selections.at(-1)[0],'l');ui.cleanup();
});
test('initial historical load has no transfer animation and playback only consumes recorded events',()=>{
 const ui=setup();ui.render({...run,status:'stopped'},events);assert.equal(ui.find(n=>n.attributes.class==='arch-transmission').length,0);
 ui.find(n=>n.textContent==='历史回放')[0].onclick();assert.equal(ui.selections.at(-1)[2].length,1);
 ui.find(n=>n.textContent==='暂停回放')[0].onclick();assert.equal(ui.find(n=>n.className?.includes('arch-active')).length,0);ui.cleanup();
});

test('moving arrow follows the directed edge once and terminal removes the packet',()=>{
 const ui=setup();ui.render(run,events.slice(0,9));ui.render(run,events);
 const edge=ui.find(n=>n.attributes.class==='arch-transmission'&&n.attributes['data-from']==='d')[0];
 const packets=ui.find(n=>n.attributes.class==='arch-packet'&&n.attributes['data-from']==='d');assert.equal(packets.length,3);
 for(const p of packets)assert.ok(p.children.some(n=>n.attributes.class==='arch-packet-arrow'));
 const packet=packets[0];
 const motion=packet.children.find(n=>n.tagName==='animateMotion');
 assert.equal(motion.attributes.path,edge.attributes.d);assert.equal(motion.attributes.rotate,'auto');assert.equal(motion.attributes.repeatCount,'1');assert.equal(motion.attributes.dur,'2.2s');
 const completed=[...events,{sequence:11,event:'step_completed',step_id:'l',node_id:'locator',status:'ok'},{sequence:12,event:'step_started',step_id:'back',parent_step_id:'l',node_id:'program_dispatch'}];
 ui.render(run,completed);assert.ok(ui.find(n=>n.attributes.class==='arch-packet'&&n.attributes['data-from']==='l'&&n.attributes['data-to']==='back').length);
 ui.render({...run,status:'stopped'},completed);assert.equal(ui.find(n=>n.attributes.class==='arch-packet').length,0);ui.cleanup();
});
