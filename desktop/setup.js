'use strict';
const $=id=>document.getElementById(id);
const fields=['base_url','model','api_key','execution_url','execution_token','ssh_tunnel','monetary_budget',
  'daily_usd','input_price','output_price','max_output_tokens','token_parameter','approved'];
$('monetary_budget').onchange=()=>{$('money').hidden=!$('monetary_budget').checked;};
window.desktopSettings.load().then(result=>{
  if(!result.ok){$('message').textContent=result.error;return;}
  if(!result.settings)return;
  for(const key of fields){const value=result.settings[key];if(value==null)continue;
    if($(key).type==='checkbox')$(key).checked=value;else $(key).value=value;}
  for(const key of ['api_key','execution_token'])if(result.settings[key+'_configured'])$(key).placeholder='已加密保存，留空继续使用；填写可更换';
  $('monetary_budget').onchange();
});
$('settings').onsubmit=async event=>{
  event.preventDefault();$('save').disabled=true;$('message').textContent='正在保存并启动本机工作台…';
  const value={};for(const key of fields)value[key]=$(key).type==='checkbox'?$(key).checked:$(key).value;
  value.max_output_tokens=Number(value.max_output_tokens);
  try{const result=await window.desktopSettings.save(value);if(!result.ok)$('message').textContent=result.error;}
  catch{$('message').textContent='保存未完成，请重试。';}
  finally{$('save').disabled=false;}
};
