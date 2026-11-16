const {test}=require('node:test');
const assert=require('node:assert/strict');
const {costText}=require('../ui/app/trace.js');
const {budgetText}=require('../ui/app/budget.js');
test('not sent is explained from local evidence',()=>{
 assert.match(costText({result:{cost:{status:'not_sent'}}},[]),/请求未发送.*释放/);
});
test('held fee never looks like an actual charge or zero',()=>{
 const text=costText({result:{cost:{status:'unknown',budget_handling:'reserved_upper_bound',currency:'CNY',reserved_cny:'.02'}}},[]);
 assert.match(text,/待核对.*预留 CNY .02/);assert.match(text,/未计作实际扣费/);
});
test('budget explains held unknown separately from blocking unknown',()=>{
 const text=budgetText({personal:true,monetary_budget_enabled:true,paid_calls_blocked:false,held_unknown_calls:1,held_upper_cny:'.03',note:''});
 assert.match(text,/1条费用待核对/);assert.match(text,/适用累计授权/);assert.doesNotMatch(text,/暂停新调用/);
});
