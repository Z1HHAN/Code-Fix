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
