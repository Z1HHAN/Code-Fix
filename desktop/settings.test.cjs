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

test('connection checks work before unrelated fields are configured',()=>{
  const {checkSettings}=require('./settings.cjs');
  assert.equal(checkSettings({base_url:'https://models.example/v1',api_key:'only-model-key'}, {}, 'models').api_key,'only-model-key');
  assert.equal(checkSettings({execution_url:'https://execution.example',execution_token:'x'.repeat(40)}, {}, 'execution').execution_url,'https://execution.example');
  const v=checkSettings(draft({execution_url:'',execution_token:'',ssh_tunnel:false}),{},'test_model');
  assert.equal(v.model,'my-model');assert.equal(v.execution_token,undefined);
  assert.throws(()=>checkSettings(draft({approved:false}),{},'test_model'));
});
test('multiple chosen models are bounded and available without returning credentials',()=>{
  const v=validate(draft({models:['other','my-model','other']}));
  assert.deepEqual(v.models,['my-model','other']);assert.deepEqual(publicSettings(v).models,v.models);
  assert.throws(()=>validate(draft({models:Array.from({length:13},(_,i)=>'m'+i)})));
});

test('personal HTTPS supports user-selected web ports with clear rejected ports',()=>{
  assert.equal(endpoint('https://models.example:8443/v1'),'https://models.example:8443/v1');
  for(const port of [0,22,25,53,135,139,445])assert.throws(()=>endpoint('https://models.example:'+port+'/v1'),new RegExp('端口'+port));
  assert.throws(()=>endpoint('http://127.0.0.1:3000/v1'));
});
const priced={provider:'manual',model:'my-model',region:'custom',mode:'standard',currency:'CNY',unit:'per_million_tokens',endpoint_host:'models.example',source_url:'https://models.example/prices',verified_at:'2026-11-11',tiers:[{max_input_tokens:1000000,input:'2',output:'8'}]};
test('personal per-model CNY prices preserve secrets and reject partial model coverage',()=>{
  const v=validate(draft({monetary_budget:true,budget_currency:'CNY',daily_limit:'5',model_prices:{'my-model':priced}}));
  assert.equal(v.daily_limit,'5');assert.equal(v.input_price,null);assert.deepEqual(publicSettings(v).model_prices,{'my-model':priced});
  assert.throws(()=>validate(draft({monetary_budget:true,budget_currency:'CNY',daily_limit:'5',models:['other'],model_prices:{'my-model':priced}})),/每个登记模型/);
  assert.throws(()=>validate(draft({monetary_budget:true,budget_currency:'USD',daily_limit:'5',model_prices:{'my-model':priced}})),/币种/);
});
