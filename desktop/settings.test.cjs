const test=require('node:test'),assert=require('node:assert/strict');
const {validate,publicSettings,endpoint}=require('./settings.cjs');
function draft(extra={}){return {base_url:'https://models.example/v1',model:'my-model',api_key:'test-only-key',
  execution_url:'http://127.0.0.1:17880',execution_token:'t'.repeat(40),ssh_tunnel:true,
  monetary_budget:false,max_output_tokens:2048,token_parameter:'max_tokens',approved:true,...extra};}
test('personal configuration has no preset account, secrets do not return to renderer',()=>{
  const v=validate(draft()),p=publicSettings(v);assert.equal(p.api_key,'');assert.equal(p.execution_token,'');
  assert.equal(p.api_key_configured,true);assert.equal(v.input_price,null);assert.equal(p.model,'my-model');
});
test('blank credentials retain encrypted previous values',()=>{
  const old=validate(draft());const next=validate(draft({api_key:'',execution_token:'',model:'new'}),old);
  assert.equal(next.api_key,old.api_key);assert.equal(next.execution_token,old.execution_token);
});
test('explicit tunnel accepts only literal loopback HTTP',()=>{
  assert.equal(endpoint('http://127.0.0.1:17880',true),'http://127.0.0.1:17880');
  for(const address of ['http://localhost:17880','http://8.148.85.237:8780','https://user:pass@models.example/v1'])assert.throws(()=>endpoint(address,true));
  assert.throws(()=>endpoint('http://127.0.0.1:17880'));
});
test('prices and consent are required when enabling monetary budget',()=>{
  assert.throws(()=>validate(draft({monetary_budget:true})));
  assert.throws(()=>validate(draft({approved:false})));
  assert.equal(validate(draft({monetary_budget:true,daily_usd:2,input_price:1,output_price:2})).daily_usd,'2');
});
test('reject executable/unknown configuration fields, credential line breaks and invalid bounds',()=>{
  for(const bad of [{command:'whoami'},{api_key:'key\nheader'},{max_output_tokens:0},{token_parameter:'cmd'}])assert.throws(()=>validate(draft(bad)));
});
