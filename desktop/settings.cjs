'use strict';
const {createHash}=require('node:crypto');
function endpoint(raw,tunnel=false){
  if(typeof raw!=='string'||raw.length>2048)throw Error('请填写有效的服务地址');
  let u;try{u=new URL(raw);}catch{throw Error('服务地址格式无效');}
  if(u.username||u.password||u.search||u.hash)throw Error('地址不能包含密码、查询参数或片段');
  if(u.protocol!=='https:'&&!(tunnel&&u.protocol==='http:'&&u.hostname==='127.0.0.1'))throw Error('请使用HTTPS；SSH隧道只允许http://127.0.0.1');
  if(!tunnel&&u.port&&u.port!=='443')throw Error('模型地址仅支持HTTPS标准端口');
  return u.href.replace(/\/+$/,'');
}
function validate(value,previous={}){
  const allowed=['base_url','model','api_key','execution_url','execution_token','ssh_tunnel','monetary_budget',
    'daily_usd','input_price','output_price','max_output_tokens','token_parameter','approved'];
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
  for(const k of ['daily_usd','input_price','output_price']){
    if(v.monetary_budget&&(!(Number(v[k])>0)||!Number.isFinite(Number(v[k]))))throw Error('启用金额预算需填写正数价格和每日美元额度');
    v[k]=v.monetary_budget?String(Number(v[k])):null;
  }
  v.model=v.model.trim();
  v.price_version='personal-'+createHash('sha256').update(JSON.stringify([v.daily_usd,v.input_price,v.output_price])).digest('hex').slice(0,16);
  return v;
}
function publicSettings(v){
  if(!v)return null;
  const {api_key,execution_token,price_version,...safe}=v;
  return {...safe,api_key:'',execution_token:'',api_key_configured:!!api_key,execution_token_configured:!!execution_token};
}
module.exports={endpoint,validate,publicSettings};
