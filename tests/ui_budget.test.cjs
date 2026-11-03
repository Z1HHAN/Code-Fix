const {test} = require('node:test');
const assert = require('node:assert/strict');
const {budgetText} = require('../ui/app/budget.js');
test('remaining three warns without claiming platform balance', () => {
  const text = budgetText({accounts:[{account_id:'a', estimated_remaining_cny:'3', authorized_cny:'16', warning_threshold_cny:'3', low_balance:true}]});
  assert.match(text, /不是平台实时余额/);
  assert.match(text, /低余额提醒/);
  assert.match(text, /估算剩余 ¥3/);
});
test('unknown charge is not rendered as zero balance', () => {
  const text = budgetText({accounts:[{account_id:'a', status:'unknown', estimated_remaining_cny:null, authorized_cny:'16'}], paid_calls_blocked:true});
  assert.match(text, /余额未知/);
  assert.match(text, /所有账号/);
  assert.doesNotMatch(text, /剩余 ¥0/);
});
test('empty registry is explicit', () => assert.match(budgetText({accounts:[]}), /未登记人民币账号/));
