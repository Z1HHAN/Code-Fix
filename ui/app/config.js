'use strict';
window.CodeFixConfig = (() => {
  const roles={repairer:['修复员','生成候选修改，不能修改测试'],detector:['检测员','分析失败证据，不能覆盖测试结论'],scheduler:['模型调度员','提出路由建议，由程序最终裁决'],locator:['定位员','按分支参与，提供位置与假设']};
  let catalog=null,saved=null,snapshotId=null,revision=0,loading=false;
  const accounts={sf_account1:'账号1',sf_account2:'账号2'};
  const accountName=p=>`${p.provider} / ${accounts[p.account]||p.account||'未记录账号'}`;
  function tab(name){
    for(const b of document.querySelectorAll('[data-config-tab]'))b.setAttribute('aria-selected',String(b.dataset.configTab===name));
    for(const n of ['roles','conditions','snapshot'])byId('config-'+n).hidden=n!==name;
  }
  function dirty(){revision++;saved=null;key=null;byId('use-saved-config').checked=false;byId('use-saved-config').disabled=true;display('task-config-label','尚未保存');display('config-draft-id','未保存');display('config-message','草稿已修改，请重新校验并保存。');}
  function selectProfiles(role,accountSelect,modelSelect,preferred){
    const rows=catalog.profiles.filter(p=>p.account===accountSelect.value);
    modelSelect.replaceChildren(...rows.map(p=>{const o=element('option',p.model);o.value=p.profile_ref;return o;}));
    if(rows.some(p=>p.profile_ref===preferred))modelSelect.value=preferred;
  }
  function draft(){
    const bindings={};for(const role of Object.keys(roles))bindings[role]=byId('binding-'+role).value;
    return {bindings,strategy:byId('config-strategy').value,parameters:{max_rounds:Number(byId('config-rounds').value),max_calls:Number(byId('config-calls').value),total_seconds:Number(byId('config-seconds').value)}};
  }
  async function save(validateOnly){
    if(loading||!catalog)return;loading=true;const version=revision;
    byId('validate-config').disabled=byId('save-config').disabled=true;
    try{
      const data=await(await api(validateOnly?'/configurations/validate':'/configurations',{method:'POST',body:JSON.stringify(draft())})).json();
      if(version!==revision){display('config-message','草稿已变更，本次响应不应用；请重新保存。');return;}
      display('config-message',data.note);
      if(!validateOnly){saved=data;display('config-draft-id',data.config_id);byId('use-saved-config').disabled=false;byId('use-saved-config').checked=true;display('task-config-label',data.config_id.slice(0,16)+'…');display('config-message',data.note+'。已选用于下次新建任务；创建不会自动启动。');}
    }catch(error){display('config-message','未保存：'+error.message);}
    finally{loading=false;byId('validate-config').disabled=byId('save-config').disabled=false;}
  }
  async function conditions(){
    const button=byId('refresh-conditions');button.disabled=true;
    display('conditions-note','正在读取本机配置与账本；不会发起模型请求。');
    // Clear stale values before refresh so a failed request never leaves an old green state.
    byId('account-rows').replaceChildren();byId('isolation-values').replaceChildren();byId('model-readiness').replaceChildren();
    try{
      const [health,budget,models]=await Promise.all([api('/health').then(r=>r.json()),api('/budget').then(r=>r.json()),api('/configuration/catalog').then(r=>r.json())]);
      display('health',health);display('budget',budgetText(budget));
      for(const a of budget.accounts||[]){const row=element('tr');for(const v of [accounts[a.account_id]||a.account_id,a.authorized_cny,a.estimated_spent_cny,a.reserved_cny,a.estimated_remaining_cny,a.warning_threshold_cny])row.append(element('td',v==null?'未知':String(v)));if(a.low_balance)row.append(element('td','低余额提醒','warn'));byId('account-rows').append(row);}
      display('budget-note',(budget.paid_calls_blocked?'存在未结算费用，阻止所有账号新付费请求。 ':'')+(budget.note||'仅本项目估算，不是实时平台余额。')+' 金额单位：人民币元；累计额度不按天重置。');
      const values=[['Docker命令',health.docker_available?'可找到；尚不代表隔离通过':'未找到'],['隔离探针',health.isolation_verified===true?'已记录通过':'待本次任务启动验证'],['模型调用配置',health.model_configured?'默认配置可解析，未请求验证':'默认配置未就绪'],['执行机','当前服务所在本机；不代表B机'],['前置阻塞',(health.blockers||[]).join('；')||'未报告；仍须每次运行检查']];
      for(const [k,v]of values)byId('isolation-values').append(element('dt',k),element('dd',v));
      for(const p of models.profiles){const line=element('p',`${accountName(p)} · ${p.model}：${p.configured?'本机配置可解析':'待核对'}；${p.note}`);byId('model-readiness').append(line);}
      display('conditions-note','已读取本机状态；模型、费用预留和隔离仍以任务启动检查为准。');
    }catch(e){display('conditions-note','状态读取失败：'+e.message+'；旧状态不作当前依据。');display('health','未核对');display('budget','未核对');}
    finally{button.disabled=false;}
  }
  async function snapshot(run){
    const id=run.task.config_id||'';
    if(snapshotId===id)return;snapshotId=id;
    display('saved-snapshot',id?'读取保存配置中…':'历史未关联保存配置；不能按当前默认值补填。');
    if(!id)return;
    try{const data=await(await api('/configurations/'+encodeURIComponent(id))).json();if(snapshotId===id)display('saved-snapshot',{config_id:data.config_id,created_at:data.created_at,bindings:data.bindings,parameters:data.parameters,strategy:data.strategy,profiles_sha256:data.profiles_sha256});}
    catch(e){if(snapshotId===id){snapshotId=null;display('saved-snapshot','未能读取保存配置：'+e.message);}}
  }
  function apply(task){
    if(!byId('use-saved-config').checked)return;
    if(!saved)throw new Error('配置已修改或尚未保存，请重新保存。');
    Object.assign(task,saved.parameters,{config_id:saved.config_id});
  }
  async function init(){
    for(const b of document.querySelectorAll('[data-config-tab]'))b.onclick=()=>{tab(b.dataset.configTab);if(b.dataset.configTab==='conditions')conditions();};
    byId('validate-config').onclick=()=>save(true);byId('save-config').onclick=()=>save(false);byId('refresh-conditions').onclick=conditions;
    for(const id of ['config-rounds','config-calls','config-seconds','config-strategy'])byId(id).oninput=dirty;
    byId('use-saved-config').onchange=()=>{key=null;syncTaskForm();};
    const originalOpen=byId('new-task').onclick;byId('new-task').onclick=()=>{originalOpen();syncTaskForm();};
    try{
      catalog=await(await api('/configuration/catalog')).json();byId('binding-rows').replaceChildren();
      for(const [role,[name,description]]of Object.entries(roles)){
        const row=element('tr'),labelCell=element('td'),aCell=element('td'),mCell=element('td'),a=element('select'),m=element('select');m.id='binding-'+role;m.setAttribute('aria-label',name+'模型');a.setAttribute('aria-label',name+'账号');
        labelCell.append(element('strong',name),element('small',description));
        const unique=[...new Set(catalog.profiles.map(p=>p.account))];
        for(const account of unique){const p=catalog.profiles.find(p=>p.account===account),o=element('option',accountName(p));o.value=account;a.append(o);}
        const preferred=catalog.profiles.find(p=>p.profile_ref===catalog.bindings[role]);if(preferred)a.value=preferred.account;
        selectProfiles(role,a,m,catalog.bindings[role]);a.onchange=()=>{selectProfiles(role,a,m);dirty();};m.onchange=dirty;
        aCell.append(a);mCell.append(m);row.append(labelCell,aCell,mCell);byId('binding-rows').append(row);
      }
      for(const [id,k]of [['config-rounds','max_rounds'],['config-calls','max_calls'],['config-seconds','total_seconds']])byId(id).max=catalog.limits[k];
      const seen=new Set();for(const p of catalog.profiles){if(seen.has(p.provider+p.model))continue;seen.add(p.provider+p.model);const row=element('div',p.model,'candidate-model');row.append(element('small',p.provider+' · 受控档案；能力以实际检查为准'));byId('model-candidates').append(row);}
      display('config-message','已读取当前服务的角色绑定；修改仅用于新配置，不改变全局配置或历史任务。');
    }catch(e){display('config-message','配置读取失败：'+e.message);byId('validate-config').disabled=byId('save-config').disabled=true;}
  }
  function syncTaskForm(){
    const using=byId('use-saved-config').checked&&saved;
    for(const [id,k]of [['rounds','max_rounds'],['calls','max_calls'],['seconds','total_seconds']]){byId(id).disabled=Boolean(using);if(using)byId(id).value=saved.parameters[k];}
  }
  return {init,tab,snapshot,apply};
})();
