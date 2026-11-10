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
function setup(motion){const doc={createElement:t=>new E(t),createElementNS:(ns,t)=>new E(t),defaultView:motion?{matchMedia:()=>motion}:undefined},host=new E('div'),selections=[];const render=arch.mount(doc,host,trace,(...args)=>selections.push(args));const walk=n=>[n,...n.children.flatMap(walk)];return{render,host,selections,find:fn=>walk(host).filter(fn),cleanup:()=>render({...run,run_id:'empty',status:'created'},[])};}
function withClock(check){
 const original={setTimeout,clearTimeout,now:Date.now},pending=new Map();let now=1000,id=0;
 global.setTimeout=(fn,delay)=>{pending.set(++id,{fn,at:now+delay});return id;};global.clearTimeout=id=>pending.delete(id);Date.now=()=>now;
 const advance=ms=>{const end=now+ms;let count=0;while(true){const next=[...pending].sort((a,b)=>a[1].at-b[1].at)[0];if(!next||next[1].at>end)break;assert.ok(count++<1000);pending.delete(next[0]);now=next[1].at;next[1].fn();}now=end;};
 try{check(advance);}finally{global.setTimeout=original.setTimeout;global.clearTimeout=original.clearTimeout;Date.now=original.now;}
}

test('real replay keeps full-record provenance at entry, pause and end without exposing future steps',()=>withClock(advance=>{
 const ui=setup(),history={...run,status:'success',result:{mode:'live'}};
 const page=[{sequence:1,event:'started'},
  {sequence:2,event:'agent_call',mode:'live',model_api_called:true,step_id:'r',node_id:'repairer'},
  {sequence:3,event:'finished'}];
 ui.render(history,page);assert.match(ui.find(n=>n.attributes.role==='status')[0].textContent,/真实历史回放.*不执行任务/);
 ui.find(n=>n.textContent==='历史回放')[0].onclick();
 assert.match(ui.find(n=>n.attributes.role==='status')[0].textContent,/真实历史回放 · 1\/3/);
 assert.equal(ui.selections.at(-1)[2].length,1);
 assert.equal(ui.find(n=>n.className==='arch-terminal').length,0);
 ui.find(n=>n.textContent==='暂停回放')[0].onclick();
 assert.match(ui.find(n=>n.attributes.role==='status')[0].textContent,/真实历史回放.*回放已暂停/);
 ui.find(n=>n.textContent==='继续回放')[0].onclick();advance(10000);
 assert.match(ui.find(n=>n.attributes.role==='status')[0].textContent,/真实历史回放.*回放结束/);ui.cleanup();
}));

for(const [result,task,calls,label] of [
 [{mode:'live'},{mode:'repair'},[],/来源未确认/],
 [{mode:'test_stub'},{mode:'repair'},[{event:'agent_call',mode:'live',model_api_called:true}],/离线／模拟回放/],
 [{},{mode:'baseline'},[],/基线历史回放/]
])test(`history source does not infer a real run from ${label}`,()=>withClock(()=>{
 const ui=setup();ui.render({...run,status:'stopped',result,task},[{event:'started'},...calls]);
 ui.find(n=>n.textContent==='历史回放')[0].onclick();
 assert.match(ui.find(n=>n.attributes.role==='status')[0].textContent,label);ui.cleanup();
}));

test('packet density uses travelled bends with explicit short/medium/long boundaries',()=>{
 for(const [d,count]of [['M0 0H259',1],['M0 0H260',2],['M0 0V300H299',2],['M0 0V300H300',3],['M100 100h-100v-100h100v100',2]])assert.equal(arch.packetCount(d),count);
});

test('upward return stays beside its nodes and short repair handoff has one packet',()=>{
 const ui=setup();ui.render(run,events.slice(0,3));ui.render(run,events.slice(0,4));
 assert.equal(ui.find(n=>n.attributes.class==='arch-packet'&&n.attributes['data-from']==='a').length,1);
 ui.render(run,events);
 ui.render(run,[...events,{sequence:11,event:'step_completed',step_id:'l',node_id:'locator',status:'ok'},{sequence:12,event:'step_started',step_id:'back',parent_step_id:'l',node_id:'program_dispatch'}]);
 const path=ui.find(n=>n.attributes.class==='arch-transmission'&&n.attributes['data-from']==='l')[0].attributes.d;
 assert.equal(path,'M628 345V333H724V295H628V286');assert.equal(arch.packetCount(path),1);ui.cleanup();
});

for(const status of ['success','failed'])test(`normal ${status} fades only existing paths for 300ms, never terminal execution`,()=>withClock(advance=>{
 const ui=setup();ui.render(run,events.slice(0,3));ui.render(run,events.slice(0,4));
 const paths=ui.find(n=>n.attributes.class==='arch-transmission').map(n=>n.attributes.d);
 ui.render({...run,status},[...events.slice(0,4),{sequence:5,event:'finished'}]);
 assert.deepEqual(ui.find(n=>n.attributes.class==='arch-settling').map(n=>n.attributes.d),paths);
 assert.equal(ui.find(n=>n.attributes.class==='arch-packet'||n.tagName==='animateMotion'||n.tagName==='animate'||n.className?.includes('arch-active')).length,0);
 assert.equal(ui.find(n=>n.className==='arch-terminal').length,1);
 advance(299);assert.ok(ui.find(n=>n.attributes.class==='arch-settling').length);advance(1);assert.equal(ui.find(n=>n.attributes.class==='arch-settling').length,0);ui.cleanup();
}));

for(const status of ['stopped','interrupted','paused','blocked'])test(`${status} removes all motion and highlights immediately`,()=>withClock(advance=>{
 const ui=setup();ui.render(run,events.slice(0,3));ui.render(run,events.slice(0,4));
 ui.render({...run,status},[...events.slice(0,4),{sequence:5,event:'finished'}]);
 assert.equal(ui.find(n=>['arch-transmission','arch-settling','arch-packet'].includes(n.attributes.class)).length,0);const count=ui.selections.length;advance(4000);assert.equal(ui.selections.length,count);ui.cleanup();
}));

test('terminal fade requires recorded finish and current motion, respects reduced motion and cancels on task switch',()=>withClock(advance=>{
 for(const motion of [undefined,{matches:true,addEventListener(){}}]){
  const ui=setup(motion);ui.render(run,events.slice(0,3));ui.render(run,events.slice(0,4));
  ui.render({...run,status:'success'},events.slice(0,4));assert.equal(ui.find(n=>n.attributes.class==='arch-settling').length,0);ui.cleanup();
 }
 const ui=setup({matches:true,addEventListener(){}});ui.render(run,events.slice(0,3));ui.render(run,events.slice(0,4));ui.render({...run,status:'success'},[...events,{sequence:11,event:'finished'}]);assert.equal(ui.find(n=>n.attributes.class==='arch-settling').length,0);ui.cleanup();
 const other=setup();other.render(run,events.slice(0,3));other.render(run,events.slice(0,4));other.render({...run,status:'success'},[...events,{sequence:11,event:'finished'}]);assert.ok(other.find(n=>n.attributes.class==='arch-settling').length);
 other.cleanup();const count=other.selections.length;advance(4000);assert.equal(other.selections.length,count);assert.equal(other.find(n=>n.attributes.class==='arch-settling').length,0);
}));

test('return-current control has a semantic action independent of button order',()=>{
 const ui=setup();ui.render(run,events);ui.find(n=>n.dataset.module==='repairer')[0].onclick();
 const follow=ui.find(n=>n.attributes['data-action']==='follow')[0];assert.equal(follow.textContent,'返回当前步骤');follow.onclick();assert.equal(ui.selections.at(-1)[0],'l');ui.cleanup();
});

test('replay pause resumes the same prefix and remaining arrow travel, including after polling',()=>withClock(advance=>{
 const ui=setup();ui.render({...run,status:'stopped'},events);
 ui.find(n=>n.textContent==='历史回放')[0].onclick();advance(300);
 assert.equal(ui.find(n=>n.attributes.class==='arch-packet').length,2);
 ui.find(n=>n.textContent==='暂停回放')[0].onclick();const prefix=ui.selections.at(-1)[2].length;
 advance(10000);ui.render({...run,status:'stopped',result:{mode:'test_stub',note:'poll'}},events);
 ui.find(n=>n.textContent==='继续回放')[0].onclick();assert.equal(ui.selections.at(-1)[2].length,prefix);
 const motion=ui.find(n=>n.tagName==='animateMotion')[0];assert.equal(motion.attributes.begin,'-0.3s');
 advance(349);assert.equal(ui.selections.at(-1)[2].length,1);advance(1);assert.equal(ui.selections.at(-1)[2].length,2);ui.cleanup();
}));

test('entry replay illuminates both entry nodes and gives every segment a directed arrow',()=>withClock(()=>{
 const ui=setup();ui.render({...run,status:'stopped'},events);ui.find(n=>n.textContent==='历史回放')[0].onclick();
 for(const key of ['entry','service'])assert.ok(ui.find(n=>n.dataset.module===key)[0].className.includes('arch-active'));
 const packets=ui.find(n=>n.attributes.class==='arch-packet');assert.equal(packets.length,arch.packetCount(packets[0].children.find(n=>n.tagName==='animateMotion').attributes.path));
 for(const packet of packets){assert.ok(packet.children.some(n=>n.attributes.class==='arch-packet-arrow'));assert.equal(packet.children.find(n=>n.tagName==='animateMotion').attributes.rotate,'auto');}ui.cleanup();
}));

test('last started event retains the full handoff window then ends without claiming task completion',()=>withClock(advance=>{
 const ui=setup();const page=[events[1],events[2],events[3]];ui.render(run,page);ui.find(n=>n.textContent==='历史回放')[0].onclick();
 advance(2150);assert.equal(ui.selections.at(-1)[2].length,3);assert.ok(ui.find(n=>n.attributes['data-from']==='a'&&n.attributes.class==='arch-packet').length);
 advance(3799);assert.equal(ui.find(n=>n.textContent==='暂停回放')[0].disabled,false);
 advance(1);assert.equal(ui.find(n=>n.attributes.class==='arch-packet').length,0);assert.equal(ui.find(n=>n.textContent==='重新回放').length,1);
 assert.equal(ui.find(n=>n.className==='arch-terminal').length,0);ui.cleanup();
}));

test('completion holds for 1500ms while evidence preserves elapsed travel',()=>withClock(advance=>{
 const ui=setup();ui.render(run,[events[1],events[2],events[3],{sequence:5,event:'observation'}]);ui.find(n=>n.textContent==='历史回放')[0].onclick();
 advance(650);assert.equal(ui.selections.at(-1)[2].length,2);advance(1499);assert.equal(ui.selections.at(-1)[2].length,2);advance(1);assert.equal(ui.selections.at(-1)[2].length,3);
 const packet=ui.find(n=>n.attributes['data-evidence-step']==='a'&&n.attributes.class==='arch-packet')[0];assert.equal(packet.children.find(n=>n.tagName==='animateMotion').attributes.begin,'-1.5s');ui.cleanup();
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
 const packets=ui.find(n=>n.attributes.class==='arch-packet'&&n.attributes['data-from']==='d');assert.equal(packets.length,arch.packetCount(packets[0].children.find(n=>n.tagName==='animateMotion').attributes.path));
 for(const p of packets)assert.ok(p.children.some(n=>n.attributes.class==='arch-packet-arrow'));
 const packet=packets[0];
 const motion=packet.children.find(n=>n.tagName==='animateMotion');
 assert.equal(motion.attributes.path,edge.attributes.d);assert.equal(motion.attributes.rotate,'auto');assert.equal(motion.attributes.repeatCount,'1');assert.equal(motion.attributes.dur,'2.2s');
 const completed=[...events,{sequence:11,event:'step_completed',step_id:'l',node_id:'locator',status:'ok'},{sequence:12,event:'step_started',step_id:'back',parent_step_id:'l',node_id:'program_dispatch'}];
 ui.render(run,completed);assert.ok(ui.find(n=>n.attributes.class==='arch-packet'&&n.attributes['data-from']==='l'&&n.attributes['data-to']==='back').length);
 ui.render({...run,status:'stopped'},completed);assert.equal(ui.find(n=>n.attributes.class==='arch-packet').length,0);ui.cleanup();
});

test('baseline labels preserve raw round zero and do not label preparation as a repair round',()=>{
 const page=[{sequence:1,event:'step_started',step_id:'base',node_id:'pytest',phase:'development',round:0}];
 const graph=trace.project(run,page);assert.equal(trace.roundLabel(graph.nodes[0]),'基线');assert.equal(graph.nodes[0].round,0);
 assert.equal(trace.roundLabel({round:0,facts:{node_id:'isolation_preflight'}}),'');
 assert.equal(trace.roundLabel({round:1,facts:{node_id:'repairer'}}),'第1轮');
 const ui=setup();ui.render(run,page);assert.ok(ui.find(n=>n.textContent==='基线').length);assert.equal(ui.find(n=>n.textContent.includes('第0轮')).length,0);ui.cleanup();
});

test('completion evidence travels only to recorded trace/log/diff/cost destinations',()=>withClock(()=>{
 const ui=setup();const page=[
 {sequence:1,event:'step_started',step_id:'p',node_id:'apply_candidate'},
 {sequence:2,event:'step_completed',step_id:'p',node_id:'apply_candidate',status:'ok',artifact_path:'patch-1/patch.diff'},
 {sequence:3,event:'step_started',step_id:'t',node_id:'pytest'},
 {sequence:4,event:'step_completed',step_id:'t',node_id:'pytest',status:'ok',evidence_path:'test',stdout_path:'stdout.txt'},
 {sequence:5,event:'step_started',step_id:'d',node_id:'program_dispatch'},
 {sequence:6,event:'decision',step_id:'d',node_id:'program_dispatch',counters:{cost:{status:'unknown'}}},
 {sequence:7,event:'step_completed',step_id:'d',node_id:'program_dispatch',status:'ok'}];
 ui.render(run,[]);ui.render(run,page);
 const targets=id=>new Set(ui.find(n=>n.attributes.class==='arch-transmission'&&n.attributes['data-evidence-step']===id).map(n=>n.attributes['data-evidence-target']));
 assert.deepEqual(targets('p'),new Set(['trace','diff']));assert.deepEqual(targets('t'),new Set(['trace','logs']));assert.deepEqual(targets('d'),new Set(['trace','cost']));
 for(const [target,x]of [['trace',308],['logs',548],['diff',788],['cost',1028]]){
  const lines=ui.find(n=>n.attributes.class==='arch-transmission'&&n.attributes['data-evidence-target']===target);assert.ok(lines.length);
  for(const line of lines)assert.ok(line.attributes.d.endsWith(`H${x}V637`));
 }
 assert.equal(ui.find(n=>n.attributes.class==='arch-packet'&&n.attributes['data-evidence-target']==='logs').length,1);ui.cleanup();
}));

test('missing evidence never creates logs/diff/cost transfers',()=>withClock(()=>{
 const ui=setup();ui.render(run,events.slice(0,2));ui.render(run,events.slice(0,3));
 assert.deepEqual(ui.find(n=>n.attributes.class==='arch-transmission'&&n.attributes['data-evidence-step']==='a').map(n=>n.attributes['data-evidence-target']),['trace']);ui.cleanup();
}));

test('entry advances in 650ms while its packets retain elapsed travel',()=>withClock(advance=>{
 const ui=setup();ui.render(run,events);ui.find(n=>n.textContent==='历史回放')[0].onclick();advance(649);assert.equal(ui.selections.at(-1)[2].length,1);
 advance(1);assert.equal(ui.selections.at(-1)[2].length,2);
 const entry=ui.find(n=>n.attributes.class==='arch-packet'&&!n.attributes['data-from']&&!n.attributes['data-evidence-step']);assert.equal(entry.length,2);
 assert.equal(entry[0].children.find(n=>n.tagName==='animateMotion').attributes.begin,'-0.65s');ui.cleanup();
}));

test('entry-only replay drains the final packet instead of cutting it off',()=>withClock(advance=>{
 const ui=setup();ui.render(run,[events[0]]);ui.find(n=>n.textContent==='历史回放')[0].onclick();advance(650);assert.equal(ui.find(n=>n.attributes.class==='arch-packet').length,2);
 advance(3150);assert.equal(ui.find(n=>n.attributes.class==='arch-packet').length,0);assert.ok(ui.find(n=>n.textContent==='重新回放').length);ui.cleanup();
}));

test('click keeps the result frame; explicit replay starts at that step start',()=>withClock(()=>{
 const ui=setup();ui.render({...run,status:'stopped'},events);ui.find(n=>n.dataset.module==='repairer')[0].onclick();assert.equal(ui.selections.at(-1)[2].at(-1).sequence,3);
 const from=ui.find(n=>n.textContent==='从此步骤回放')[0];assert.equal(from.disabled,false);from.onclick();assert.equal(ui.selections.at(-1)[2].at(-1).sequence,2);
 assert.ok(ui.find(n=>n.dataset.module==='repairer')[0].className.includes('arch-active'));ui.cleanup();
}));

test('reduced motion omits all SMIL and responds to preference changes',()=>withClock(()=>{
 let change;const preference={matches:true,addEventListener:(type,fn)=>change=fn};const ui=setup(preference);ui.render(run,events.slice(0,9));ui.render(run,events);
 assert.ok(ui.find(n=>n.attributes.class==='arch-transmission').length);assert.equal(ui.find(n=>['animateMotion','animate'].includes(n.tagName)).length,0);
 preference.matches=false;change();assert.ok(ui.find(n=>n.tagName==='animateMotion').length);
 preference.matches=true;change();assert.equal(ui.find(n=>['animateMotion','animate'].includes(n.tagName)).length,0);ui.cleanup();
}));

test('paused/history views count fresh events without moving inspector, deduplicate, and clear on return',()=>{
 const ui=setup();ui.render(run,events);ui.find(n=>n.dataset.module==='repairer')[0].onclick();const count=ui.selections.length;
 const page=[...events,{sequence:11,event:'step_completed',step_id:'l',node_id:'locator',status:'ok'}];ui.render(run,page);
 assert.equal(ui.selections.length,count);assert.equal(ui.find(n=>n.className==='arch-new-events')[0].textContent,'新增 1 个事件，返回当前步骤查看');
 ui.render({...run,result:{mode:'test_stub',updated:true}},page);assert.equal(ui.find(n=>n.className==='arch-new-events')[0].textContent,'新增 1 个事件，返回当前步骤查看');
 ui.find(n=>n.textContent==='返回当前步骤')[0].onclick();assert.equal(ui.selections.at(-1)[2].length,11);assert.equal(ui.find(n=>n.className==='arch-new-events')[0].hidden,true);ui.cleanup();
});

test('paused replay receives a new-event notice without changing its prefix or resuming',()=>withClock(advance=>{
 const ui=setup();ui.render(run,events.slice(0,2));ui.find(n=>n.textContent==='历史回放')[0].onclick();advance(300);ui.find(n=>n.textContent==='暂停回放')[0].onclick();
 const prefix=ui.selections.at(-1)[2].length,count=ui.selections.length;ui.render(run,events.slice(0,3));
 assert.equal(ui.selections.length,count);assert.equal(ui.find(n=>n.className==='arch-new-events')[0].textContent,'新增 1 个事件，返回当前步骤查看');
 advance(5000);assert.equal(ui.selections.at(-1)[2].length,prefix);assert.ok(ui.find(n=>n.textContent==='继续回放').length);ui.cleanup();
}));

test('a legacy step without a start event cannot replay a fabricated execution',()=>{
 const ui=setup();ui.render(run,[{sequence:1,event:'step_completed',node_id:'repairer',status:'ok'}]);ui.find(n=>n.dataset.module==='repairer')[0].onclick();
 assert.equal(ui.find(n=>n.textContent==='从此步骤回放')[0].disabled,true);ui.cleanup();
});
