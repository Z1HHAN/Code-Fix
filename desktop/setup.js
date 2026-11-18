'use strict';
const $=id=>document.getElementById(id);
const fields=['base_url','model','api_key','execution_url','execution_token','ssh_tunnel','monetary_budget',
  'daily_usd','input_price','output_price','max_output_tokens','token_parameter','approved','budget_currency','daily_limit'];
let busy=false,hasExecutionToken=false,hasApiKey=false,autoTimer,modelTimer;
const rows=new Map();
let modelPrices={},priceCatalog=[],priceDraft=null;
function showRow(row){
  rows.set(row.id,row);$('checks').replaceChildren();
  const labels={pass:'通过',blocked:'未就绪',checking:'检查中',warning:'提示',pending:'待检查'};
  for(const r of rows.values()){
    const box=document.createElement('div');box.className='check '+r.status;
    const title=document.createElement('strong');title.textContent=`${r.title} · ${labels[r.status]||r.status}`;
    const detail=document.createElement('p');detail.textContent=r.detail;
    box.append(title,detail);if(r.action){const a=document.createElement('p');a.textContent='处理方式：'+r.action;box.append(a);}
    $('checks').append(box);
  }
}
function show(result){
  for(const row of result?.checks||[])showRow(row);
  if(result?.reply_excerpt)showRow({id:'model_reply',title:'测试模型回复',status:'pass',detail:result.reply_excerpt});
  if(result?.models){$('model-options').replaceChildren(...result.models.map(id=>{const o=document.createElement('option');o.value=id;return o;}));}
  if(result?.checks?.length)$('checks').scrollIntoView({block:'nearest'});
}
function value(){
  const v={};for(const k of fields)v[k]=$(k).type==='checkbox'?$(k).checked:$(k).value;
  v.model_prices=modelPrices;v.max_output_tokens=Number(v.max_output_tokens);v.models=$('models').value.split('\n').map(x=>x.trim()).filter(Boolean);return v;
}
function lock(active){busy=active;for(const element of $('settings').querySelectorAll('input,select,textarea,button'))element.disabled=active;$('confirm-prices').disabled=active||!priceDraft?.length;}
async function check(mode){
  if(busy)return;lock(true);$('message').textContent=mode==='test_model'?'正在检查预算和模型连接，最多等待30秒，最多发送一次测试请求…':'正在检查连接，不生成模型内容…';
  try{const r=await window.desktopSettings.check(mode,value());if(!r.ok){$('message').textContent=r.error;$('message').scrollIntoView({block:'center'});return;}
    show(r.result);$('message').textContent=r.result.ready?'本次检查通过。真实模型调用与隔离检查相互独立。':'有条件未满足，请查看逐项处理方式。';
  }catch{$('message').textContent='连接检查未完成，请重试。';}finally{lock(false);}
}
window.desktopSettings.onProgress(showRow);
$('read-models').onclick=()=>check('models');
$('test-model').onclick=()=>check('test_model');
$('check-execution').onclick=()=>check('execution');
function autoExecution(){clearTimeout(autoTimer);autoTimer=setTimeout(()=>{
  if(busy){autoExecution();return;}
  if($('execution_url').value&&($('execution_token').value||hasExecutionToken))check('execution');
},500);}
for(const id of ['execution_url','execution_token','ssh_tunnel'])$(id).addEventListener('change',autoExecution);
for(const id of ['base_url','api_key'])$(id).addEventListener('change',()=>{
  rows.delete('model_reply');
  showRow({id:'model_call',title:'真实模型调用',status:'pending',detail:'连接信息已改变，请主动点击测试验证；可能收费'});
  clearTimeout(modelTimer);modelTimer=setTimeout(()=>{if(!busy&&$('base_url').value&&($('api_key').value||hasApiKey))check('models');},600);
});
$('model').addEventListener('change',()=>{
  rows.delete('model_reply');showRow({id:'model_call',title:'真实模型调用',status:'pending',detail:'所选模型已改变，请主动点击测试验证；可能收费'});
});
$('monetary_budget').onchange=()=>{$('money').hidden=!$('monetary_budget').checked||Object.keys(modelPrices).length>0;};
window.desktopSettings.load().then(result=>{
  if(!result.ok){$('message').textContent=result.error;return;}
  show(result.checks);if(!result.settings)return;
  modelPrices=result.settings.model_prices||{};if(Object.keys(modelPrices).length)$('price-preview').textContent=JSON.stringify(modelPrices,null,2);
  for(const key of fields){const v=result.settings[key];if(v==null)continue;if($(key).type==='checkbox')$(key).checked=v;else $(key).value=v;}
  $('models').value=(result.settings.models||[]).filter(m=>m!==result.settings.model).join('\n');
  for(const key of ['api_key','execution_token'])if(result.settings[key+'_configured'])$(key).placeholder='已加密保存，留空继续使用；填写可更换';
  hasExecutionToken=result.settings.execution_token_configured;
  hasApiKey=result.settings.api_key_configured;
  $('monetary_budget').onchange();check('all');
});
$('settings').onsubmit=async event=>{
  event.preventDefault();if(busy)return;lock(true);$('message').textContent='正在加密保存并检查条件；不调用模型…';
  try{const r=await window.desktopSettings.save(value());show(r.result);if(!r.ok)$('message').textContent=r.error;}
  catch{$('message').textContent='保存未完成，请重试。';}finally{lock(false);}
};

async function previewPrices(text='',format='json'){
  if(busy)return;lock(true);
  try{
    const r=await window.desktopSettings.check('prices',{price_text:text,price_format:format});
    if(!r.ok||r.result?.ready===false)throw Error(r.error||r.result.checks?.[0]?.detail||'价格格式无效');
    priceCatalog=r.result.price_catalog||priceCatalog;
    $('price-catalog').replaceChildren(...priceCatalog.map((p,i)=>{const o=document.createElement('option');o.value=i;o.textContent=`${p.model} · ${p.endpoint_host} · ${p.region} · ${p.mode} · ${p.currency}`;return o;}));
    priceDraft=r.result.prices||[];
    $('price-preview').textContent=priceDraft.length?JSON.stringify(priceDraft.map(p=>({model:p.model,previous:modelPrices[p.model]||null,proposed:p})),null,2):'已读取官方核验快照；请选择型号、地域与模式，再预览。';
  }catch(e){priceDraft=null;$('message').textContent=e.message;}finally{lock(false);$('confirm-prices').disabled=!priceDraft?.length;}
}
$('load-price-catalog').onclick=()=>previewPrices();
$('use-price-catalog').onclick=()=>{const p=priceCatalog[Number($('price-catalog').value)];if(p){$('price-format').value='json';$('price-text').value=JSON.stringify([p],null,2);priceDraft=null;$('confirm-prices').disabled=true;}};
$('price-file').onchange=async()=>{const file=$('price-file').files[0];if(!file)return;if(file.size>65536){$('message').textContent='价格文件最多64KiB';return;}$('price-text').value=await file.text();$('price-format').value=file.name.endsWith('.csv')?'csv':'json';priceDraft=null;$('confirm-prices').disabled=true;};
$('price-text').oninput=()=>{priceDraft=null;$('confirm-prices').disabled=true;};
$('price-format').onchange=()=>{priceDraft=null;$('confirm-prices').disabled=true;};
$('preview-prices').onclick=()=>previewPrices($('price-text').value,$('price-format').value);
$('confirm-prices').onclick=()=>{if(!priceDraft?.length)return;for(const p of priceDraft)modelPrices[p.model]=p;$('budget_currency').value=priceDraft[0].currency;$('monetary_budget').checked=true;$('monetary_budget').onchange();$('price-preview').textContent=JSON.stringify(modelPrices,null,2);$('message').textContent='价格已确认到草稿；请保存配置后生效，旧任务价格不会改变。';priceDraft=null;$('confirm-prices').disabled=true;};

$('clear-prices').onclick=()=>{modelPrices={};priceDraft=null;$('confirm-prices').disabled=true;$('price-preview').textContent='已清除草稿价格，保存后生效；历史任务价格保留。';};
