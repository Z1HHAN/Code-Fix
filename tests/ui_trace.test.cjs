const {test}=require('node:test');
const assert=require('node:assert/strict');
const trace=require('../ui/app/trace.js');
const run={run_id:'run-one',status:'stopped',task:{mode:'repair'},result:{mode:'test_stub'}};
const events=[
  {sequence:1,event:'step_started',step_id:'a',parent_step_id:null,node_id:'repairer',round:1},
  {sequence:2,event:'agent_call',step_id:'a',role:'repairer',call_id:'m-abcd',mode:'test_stub',status:'ok'},
  {sequence:3,event:'step_completed',step_id:'a',node_id:'repairer',round:1,status:'ok'},
  {sequence:4,event:'step_started',step_id:'t',parent_step_id:'a',node_id:'pytest',round:1},
  {sequence:5,event:'test_completed',step_id:'t',evidence_id:'e1',evidence_path:'test-1',stdout_path:'stdout.txt',verification_status:'failed'},
  {sequence:6,event:'step_completed',step_id:'t',parent_step_id:'a',node_id:'pytest',round:1,status:'ok'},
  {sequence:7,event:'step_started',step_id:'d',parent_step_id:'t',node_id:'program_dispatch'},
  {sequence:8,event:'decision',step_id:'d',parent_step_id:'t',evidence_ids:['e1'],action:'repair',effective_action:'stop',
    rule_id:'R18',model_advice:{action:'repair'},override_reason:'预算不足',reason:'达到上限',artifact_path:'trace/decisions/d.json'},
  {sequence:9,event:'step_completed',step_id:'d',parent_step_id:'t',node_id:'program_dispatch',status:'ok'},
  {sequence:10,event:'step_started',step_id:'b',parent_step_id:'d',node_id:'repairer',round:2},
  {sequence:11,event:'step_completed',step_id:'b',parent_step_id:'d',node_id:'repairer',round:2,status:'blocked'}
];
test('one node per real invocation, actual decision overrides advisory, no inferred edges',()=>{
  const graph=trace.project(run,events);
  assert.equal(graph.nodes.length,4);
  assert.match(graph.nodes.find(n=>n.id==='t').statusLabel,/验证未通过/);
  assert.deepEqual(graph.nodes.filter(n=>n.title==='修复员').map(n=>n.round),[1,2]);
  assert.equal(graph.edges.find(e=>e.from==='d').action,'stop');
  assert.equal(graph.nodes.find(n=>n.id==='d').decision.override_reason,'预算不足');
  const partial=trace.project(run,[events[9],events[10]]);
  assert.equal(partial.edges.length,0);assert.equal(partial.gaps,1);
});
test('legacy rows preserve evidence without inventing real step IDs or parents',()=>{
  const graph=trace.project(run,[{event:'decision',reason:'old',action:'repair'}]);
  assert.equal(graph.nodes[0].legacy,true);assert.equal(graph.edges.length,0);assert.equal(graph.gaps,1);
  assert.match(graph.nodes[0].title,/历史事件/);
});
test('configured live mode alone cannot claim real model requests or successful repair',()=>{
  const live={...run,result:{mode:'live',final_result:'passed'}};
  assert.match(trace.provenance(live,[]),/未确认/);
  assert.match(trace.provenance(live,[{event:'agent_call',mode:'live',model_api_called:true}]),/已记录真实模型请求/);
  assert.match(trace.provenance(live,[{event:'agent_call',mode:'test_transport',model_api_called:true}]),/离线/);
  assert.match(trace.provenance({...run,task:{mode:'baseline'},result:{}},[]),/基线检查/);
});
test('overlapping incremental pages deduplicate and preserve sequence',()=>{
  const rows=trace.mergeEvents(events.slice(0,6),events.slice(4));
  assert.equal(rows.length,events.length);assert.deepEqual(rows.map(e=>e.sequence),events.map(e=>e.sequence));
});
test('decision evidence IDs resolve real tool logs and reject injected paths',()=>{
  const node=trace.project(run,events).nodes.find(n=>n.id==='d');
  assert.deepEqual(trace.artifacts(node,events),['trace/decisions/d.json','test-1/stdout.txt']);
  for(const p of ['../secret','/absolute','C:/secret','test/../../secret','a\\b'])assert.equal(trace.safePath(p),false);
  assert.match(trace.costText({...run,result:{cost:{status:'unknown',amount_usd:0}}},[]),/未知/);
});
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attributes={};this.textContent='';}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  setAttribute(key,value){this.attributes[key]=value;}
}
function descendants(e){return [e,...e.children.flatMap(descendants)];}
function ui(loader){const doc={createElement:tag=>new Element(tag)};const graph=new Element('div'),detail=new Element('div');
  return {graph,detail,render:trace.mount(doc,graph,detail,loader)};}
test('clicking a node only reads artifacts; unchanged polls preserve viewer',async()=>{
  const calls=[];const view=ui(async(rid,path)=>{calls.push([rid,path]);return '<script>literal output</script>';});
  view.render(run,events);
  const decision=descendants(view.graph).find(e=>e.dataset.stepId==='d');decision.onclick();
  const file=descendants(view.detail).find(e=>e.className==='trace-file');await file.onclick();
  const viewer=descendants(view.detail).find(e=>e.className==='trace-artifact');
  assert.equal(viewer.textContent,'<script>literal output</script>');
  view.render(run,events);assert.ok(descendants(view.detail).includes(viewer));
  assert.equal(calls.length,1);assert.deepEqual(calls[0],['run-one','trace/decisions/d.json']);
});
test('late artifact response cannot contaminate a different run',async()=>{
  let resolve;const view=ui(()=>new Promise(r=>resolve=r));view.render(run,events);
  const file=descendants(view.detail).find(e=>e.className==='trace-file');const pending=file.onclick();
  view.render({...run,run_id:'run-two'},[]);resolve('old run secret');await pending;
  assert.equal(view.detail.children.length,0);
});
test('missing artifact reports error and never substitutes sample output',async()=>{
  const view=ui(async()=>{throw new Error('404');});view.render(run,events);
  await descendants(view.detail).find(e=>e.className==='trace-file').onclick();
  assert.match(descendants(view.detail).find(e=>e.className==='trace-artifact').textContent,/404.*未替换/);
});
test('terminal run without completion cannot leave stale running detail',()=>{
  const view=ui(async()=>''),partial=[events[0]];
  view.render({...run,status:'running'},partial);
  assert.match(descendants(view.detail).find(e=>e.tag==='pre').textContent,/执行中/);
  view.render({...run,status:'interrupted'},partial);
  assert.match(descendants(view.detail).find(e=>e.tag==='pre').textContent,/步骤未闭合/);
});

test('layout positions do not introduce edges across unrelated or missing parents',()=>{
  const graph=trace.project(run,events),positions=trace.layout(graph).positions;
  assert.equal(positions.size,4);assert.ok(positions.get('b').y>positions.get('a').y);
  assert.equal(graph.edges.length,3);
  const disconnected=trace.project(run,[{event:'decision',step_id:'orphan',parent_step_id:'missing',reason:'old'}]);
  assert.equal(trace.layout(disconnected).positions.size,1);assert.equal(disconnected.edges.length,0);
});

test('workbench external evidence viewer resets on task switch and rejects late responses',async()=>{
  let finish;const doc={createElement:tag=>new Element(tag)},graph=new Element('div'),detail=new Element('div'),bottom=new Element('div'),notifications=[];
  const render=trace.mount(doc,graph,detail,()=>new Promise(resolve=>finish=resolve),{artifactContainer:bottom,onArtifact:p=>notifications.push(p)});
  render(run,events);const file=descendants(detail).find(e=>e.className==='trace-file');const request=file.onclick();
  const old=bottom.children[0];render({...run,run_id:'new'},[]);finish('old artifact');await request;
  assert.equal(bottom.children.length,0);assert.notEqual(old.textContent,'old artifact');assert.ok(notifications.includes('calls/m-abcd/input.json'));
});
