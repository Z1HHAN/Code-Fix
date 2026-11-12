'use strict';
const {createHash}=require('node:crypto');
function endpoint(raw,tunnel=false){
  if(typeof raw!=='string'||raw.length>2048)throw Error('请填写有效的服务地址');
  let u;try{u=new URL(raw);}catch{throw Error('服务地址格式无效');}
  if(u.username||u.password||u.search||u.hash)throw Error('地址不能包含密码、查询参数或片段');
  if(u.protocol!=='https:'&&!(tunnel&&u.protocol==='http:'&&u.hostname==='127.0.0.1'))throw Error('请使用HTTPS；SSH隧道只允许http://127.0.0.1');
  const blocked=new Set([0,22,25,53,135,136,137,138,139,445]);
  if(u.port&&blocked.has(Number(u.port)))throw Error('端口'+u.port+'不适用于HTTPS模型或执行服务，请使用服务商提供的HTTPS端口');
  return u.href.replace(/\/+$/,'');
}
function validate(value,previous={}){
  const allowed=['base_url','model','api_key','execution_url','execution_token','ssh_tunnel','monetary_budget',
    'daily_usd','input_price','output_price','max_output_tokens','token_parameter','approved','models',
    'model_prices','budget_currency','daily_limit'];
  if(!value||typeof value!=='object'||Object.keys(value).some(k=>!allowed.includes(k)))throw Error('配置字段无效');
  const v={...value};
  for(const k of ['api_key','execution_token']){
    v[k]=v[k]||previous[k];
    if(typeof v[k]!=='string'||!v[k].trim()||v[k].length>4096||/[\r\n\0]/.test(v[k]))throw Error('请填写自己的API Key和执行访问码');
  }
  if(!/^[\x21-\x7e]{32,256}$/.test(v.execution_token))throw Error('执行访问码格式无效');
  if(typeof v.model!=='string'||!v.model.trim()||v.model.length>200||/[\r\n\0]/.test(v.model))throw Error('请填写模型ID');
  if(typeof v.ssh_tunnel!=='boolean'||typeof v.monetary_budget!=='boolean')throw Error('连接及预算选项无效');
  v.base_url=endpoint(v.base_url);v.execution_url=endpoint(v.execution_url,v.ssh_tunnel);
  if(new URL(v.execution_url).pathname!=='/')throw Error('云端执行地址须为根地址');
  if(!Number.isInteger(v.max_output_tokens)||v.max_output_tokens<256||v.max_output_tokens>32768)throw Error('输出上限须为256至32768');
  if(!['max_tokens','max_completion_tokens'].includes(v.token_parameter))throw Error('输出参数名无效');
  if(v.approved!==true)throw Error('请确认使用自己的模型额度；保存不会自动调用模型');
  v.model_prices=v.model_prices||previous.model_prices||{};
  if(typeof v.model_prices!=='object'||Array.isArray(v.model_prices)||Object.keys(v.model_prices).length>12)throw Error('逐模型价格无效');
  const priced=Object.keys(v.model_prices).length>0;
  if(priced){
    if(!['CNY','USD'].includes(v.budget_currency)||!(Number(v.daily_limit)>0)||!Number.isFinite(Number(v.daily_limit)))throw Error('请选择币种并填写正数每日费用上限');
    for(const [model,p] of Object.entries(v.model_prices)){
      if(!p||p.model!==model||p.currency!==v.budget_currency||p.unit!=='per_million_tokens'||new URL(v.base_url).hostname!==p.endpoint_host)throw Error('价格币种、型号或服务地址不匹配');
    }
    v.daily_limit=String(Number(v.daily_limit));
  }
  for(const k of ['daily_usd','input_price','output_price']){
    if(v.monetary_budget&&!priced&&(!(Number(v[k])>0)||!Number.isFinite(Number(v[k]))))throw Error('启用金额预算需导入逐模型价格或填写原美元单价');
    v[k]=v.monetary_budget&&!priced?String(Number(v[k])):null;
  }
  v.model=v.model.trim();
  v.models=v.models||[v.model];
  if(!Array.isArray(v.models)||v.models.length>12||v.models.some(m=>typeof m!=='string'||!m.trim()||m.length>200||/[\r\n\0]/.test(m)))throw Error('可用模型最多12个，请填写有效模型ID');
  v.models=[...new Set([v.model,...v.models.map(m=>m.trim())])];
  if(v.models.length>12)throw Error('可用模型最多12个');
  if(v.monetary_budget&&priced&&v.models.some(m=>!v.model_prices[m]))throw Error('请为每个登记模型导入价格');
  v.price_version='personal-'+createHash('sha256').update(JSON.stringify([v.daily_usd,v.input_price,v.output_price,v.model_prices])).digest('hex').slice(0,16);
  return v;
}
function publicSettings(v){
  if(!v)return null;
  const {api_key,execution_token,price_version,...safe}=v;
  return {...safe,api_key:'',execution_token:'',api_key_configured:!!api_key,execution_token_configured:!!execution_token};
}
function checkSettings(value,previous={},mode){
  if(!['models','execution','all','test_model','prices'].includes(mode))throw Error('检查类型无效');
  if(mode==='prices')return {price_text:value.price_text||'',price_format:value.price_format||'json'};
  if(mode==='all')return validate(value,previous);
  if(mode==='test_model'){
    const v=validate({...value,execution_url:'https://unused.invalid',execution_token:'x'.repeat(40),ssh_tunnel:false},previous);
    delete v.execution_url;delete v.execution_token;delete v.ssh_tunnel;return v;
  }
  const v={...value};
  if(mode==='models'){
    v.base_url=endpoint(v.base_url);v.api_key=v.api_key||previous.api_key;
    if(typeof v.api_key!=='string'||!v.api_key.trim()||v.api_key.length>4096||/[\r\n\0]/.test(v.api_key))throw Error('请填写API Key');
    return {base_url:v.base_url,api_key:v.api_key};
  }
  v.execution_token=v.execution_token||previous.execution_token;
  if(typeof v.execution_token!=='string'||! /^[\x21-\x7e]{32,256}$/.test(v.execution_token))throw Error('请填写有效的执行访问码');
  v.execution_url=endpoint(v.execution_url,v.ssh_tunnel===true);
  if(new URL(v.execution_url).pathname!=='/')throw Error('执行服务须为根地址');
  return {execution_url:v.execution_url,execution_token:v.execution_token,ssh_tunnel:v.ssh_tunnel===true};
}
module.exports={endpoint,validate,publicSettings,checkSettings};
