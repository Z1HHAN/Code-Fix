const {test}=require('node:test'),assert=require('node:assert/strict');
const metrics=require('../ui/app/comparison_metrics.js');
test('old strategy records retain their arms and agent records use single/dynamic',()=>{
  assert.deepEqual(metrics.arms({}),['fixed','dynamic']);
  assert.deepEqual(metrics.arms({kind:'agents'}),['single','dynamic']);
  assert.match(metrics.names({kind:'agents'}).single,/单Agent/);
});
test('unknown cost and duration remain unknown; true zero remains zero',()=>{
  const data={summary:{single:{cost:{cny:{total:null}}}},pairs:[{paired:false,single:{run_id:'r',elapsed_seconds:null}}]};
  assert.deepEqual(metrics.totals(data,'single'),{cost:null,elapsed:null,unpaired:1});
  data.summary.single.cost.cny.total='0';data.pairs[0].single.elapsed_seconds=0;
  assert.deepEqual(metrics.totals(data,'single'),{cost:'0',elapsed:0,unpaired:1});
});

test('opening agents chooses complete real record instead of newest empty formal plan',()=>{
 const rows=[{comparison_id:'empty',kind:'agents',scope:'formal',planned:80,ran:0,created_at:'2026-11-17'},
 {comparison_id:'full',kind:'agents',scope:'formal',planned:80,ran:80,created_at:'2026-11-16'},
 {comparison_id:'fixture',kind:'agents',scope:'offline',planned:80,ran:80,created_at:'2026-11-18'},
 {comparison_id:'legacy',scope:'formal',planned:6,ran:6,created_at:'2026-11-19'}];
 assert.equal(metrics.preferred(rows,'agents'),'full');
 assert.equal(metrics.preferred(rows,'agents','empty'),'empty');
 assert.equal(metrics.preferred(rows,'strategies'),'legacy');
 assert.equal(metrics.preferred([],'agents'),'');
});

test('complete record preference never picks by success count',()=>{
 assert.equal(metrics.preferred([{comparison_id:'old',kind:'agents',scope:'formal',planned:80,ran:80,success:80,created_at:'2026-11-15'},
 {comparison_id:'new',kind:'agents',scope:'formal',planned:80,ran:80,success:60,created_at:'2026-11-16'}],'agents'),'new');
});

test('means use actual recorded runs and handoff percentage needs matching evidence',()=>{
 const data={comparison_id:'id',summary:{single:{ran:2,requests:3,cost:{cny:{total:null}}}},pairs:[
 {paired:true,single:{run_id:'a',elapsed_seconds:2}},{paired:true,single:{run_id:'b',elapsed_seconds:4}}]};
 let s=metrics.statistics(data,'single',{comparison_id:'other',groups:{single:{handoff_rate:.5}}});
 assert.equal(s.averageCalls,1.5);assert.equal(s.averageSeconds,3);assert.equal(s.cost,null);assert.equal(s.handoffRate,null);
 s=metrics.statistics(data,'single',{comparison_id:'id',groups:{single:{handoff_rate:.5,handoff_packages:1}}});
 assert.equal(s.handoffRate,.5);assert.equal(s.handoffs,1);
});

test('latest means count original plus followup time over original sample count',()=>{
 const data={view:'latest_cumulative',summary:{dynamic:{ran:2,requests:8,elapsed_seconds:50,cost:{cny:{total:null}}}},pairs:[{paired:true,dynamic:{run_id:'new',elapsed_seconds:10}}]};
 const s=metrics.statistics(data,'dynamic',null);assert.equal(s.averageCalls,4);assert.equal(s.averageSeconds,25);
});

test('latest unresolved tasks are separate from historical handoff packages',()=>{
 const data={comparison_id:'id',view:'latest_cumulative',summary:{dynamic:{planned:40,success:40,ran:40,requests:112,elapsed_seconds:1760.583729,cost:{cny:{total:null}}}},pairs:[]};
 const s=metrics.statistics(data,'dynamic',{comparison_id:'id',groups:{dynamic:{handoff_rate:.075,handoff_packages:3}}});
 assert.equal(s.unresolved,0);assert.equal(s.unresolvedRate,0);assert.equal(s.handoffs,3);assert.equal(s.handoffRate,.075);
 assert.match(metrics.names({kind:'agents'}).single,/有界迭代基线/);
 data.summary.dynamic.success=37;assert.equal(metrics.statistics(data,'dynamic',null).unresolved,3);
});
