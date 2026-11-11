'use strict';
window.CodeFixConfig = (() => {
  const roles={repairer:['修复员','生成候选修改，不能修改测试'],detector:['检测员','分析失败证据，不能覆盖测试结论'],scheduler:['模型调度员','提出路由建议，由程序最终裁决'],locator:['定位员','按分支参与，提供位置与假设']};
  let catalog=null,saved=null,snapshotId=null,revision=0,loading=false;
  let accountNames=new Map();
  const accountLabel=id=>accountNames.get(id)||'未命名账号';
  const readAccountNames=data=>{accountNames=new Map((data.accounts||[]).map(a=>[a.account_id,a.display_name]));};
  const accountName=p=>`${p.provider} / ${accountLabel(p.account)}`;
  function tab(name){
    for(const b of document.querySelectorAll('[data-config-tab]'))b.setAttribute('aria-selected',String(b.dataset.configTab===name));
    for(const n of ['roles','conditions','snapshot'])byId('config-'+n).hidden=n!==name;
  }
  function dirty(){revision++;saved=null;key=null;byId('use-saved-config').checked=false;byId('use-saved-config').disabled=true;display('task-config-label','尚未保存');display('config-draft-id','未保存');display('config-message','草稿已修改，请重新校验并保存。');display('effective-preview','草稿已修改，原预览失效。');syncTaskForm();}
  function selectProfiles(role,accountSelect,modelSelect,preferred){
    const rows=catalog.profiles.filter(p=>p.account===accountSelect.value);
    modelSelect.replaceChildren(...rows.map(p=>{const o=element('option',p.model);o.value=p.profile_ref;return o;}));
    if(rows.some(p=>p.profile_ref===preferred))modelSelect.value=preferred;
  }
  function draft(){
    const bindings={};for(const role of Object.keys(roles))bindings[role]=byId('binding-'+role).value;
    const role_overrides={};for(const role of Object.keys(roles)){const task_instruction=byId('instruction-'+role).value;const generation={};for(const k of ['max_output_tokens','temperature','top_p']){const n=byId('parameter-'+role+'-'+k);if(!n.disabled&&n.value!=='')generation[k]=Number(n.value);}if(task_instruction||Object.keys(generation).length)role_overrides[role]={task_instruction,generation};}
    return {bindings,role_overrides,strategy:byId('config-strategy').value,parameters:{max_rounds:Number(byId('config-rounds').value),max_calls:Number(byId('config-calls').value),total_seconds:Number(byId('config-seconds').value)}};
  }
  function roleEditor(role,name){
    const block=element('fieldset',null,'role-editor');block.append(element('legend',name));
    const label=element('label','独立任务指令（最多4000字符）','field'),area=element('textarea');area.id='instruction-'+role;area.maxLength=4000;area.rows=3;area.oninput=dirty;label.append(area);block.append(label);
    const fields=element('div',null,'three-col');
    for(const [key,title]of Object.entries({max_output_tokens:'可见输出上限',temperature:'temperature',top_p:'top_p'})){
      const label=element('label',title,'field'),input=element('input');input.type='number';input.id='parameter-'+role+'-'+key;input.oninput=dirty;
      const note=element('small');note.id='capability-'+role+'-'+key;label.append(input,note);fields.append(label);
    }block.append(fields);byId('role-editors').append(block);
  }
  function updateCapabilities(role){
    const p=catalog.profiles.find(p=>p.profile_ref===byId('binding-'+role).value);
    for(const key of ['max_output_tokens','temperature','top_p']){
      const cap=p?.capabilities?.[key],input=byId('parameter-'+role+'-'+key);input.value='';input.disabled=!cap?.supported;
      input.step=key==='max_output_tokens'?'1':'0.01';input.min=cap?.minimum??'';input.max=cap?.maximum??'';
      input.placeholder=cap?.default==null?'沿用服务商默认':String(cap.default);
      display('capability-'+role+'-'+key,cap?.supported?`${cap.minimum}–${cap.maximum}；${cap.reason}`:cap?.reason||'能力未登记');
    }
  }
  async function save(validateOnly){
    if(loading||!catalog)return;loading=true;const version=revision;
    byId('validate-config').disabled=byId('save-config').disabled=true;
    try{
      const data=await(await api(validateOnly?'/configurations/validate':'/configurations',{method:'POST',body:JSON.stringify(draft())})).json();
      if(version!==revision){display('config-message','草稿已变更，本次响应不应用；请重新保存。');return;}
      display('config-message',data.note);display('effective-preview',data);
      if(!validateOnly){saved=data;display('config-draft-id',data.config_id);byId('use-saved-config').disabled=false;byId('use-saved-config').checked=true;display('task-config-label',data.config_id.slice(0,16)+'…');display('config-message',data.note+'。已选用于下次新建任务；创建不会自动启动。');syncTaskForm();}
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
      readAccountNames(models);
      for(const a of budget.accounts||[]){const row=element('tr');for(const v of [accountLabel(a.account_id),a.authorized_cny,a.estimated_spent_cny,a.reserved_cny,a.estimated_remaining_cny,a.warning_threshold_cny])row.append(element('td',v==null?'未知':String(v)));if(a.low_balance)row.append(element('td','低余额提醒','warn'));byId('account-rows').append(row);}
      display('budget-note',(budget.paid_calls_blocked?'存在未结算费用，阻止新付费请求。 ':'')+(budget.note||'仅本项目估算，不是实时平台余额。')+(budget.personal?'':' 金额单位：人民币元；累计额度不按天重置。'));
      const remote=health.execution_mode==='remote-docker';
      const values=[['Docker命令',remote?'由远程执行服务提供，本机不需要安装':health.docker_available?'可找到；尚不代表隔离通过':'未找到'],['隔离探针','本次环境概览未执行探针；历史结果见所选任务记录，每次启动仍须重新验证'],['模型调用配置',health.model_configured?'默认配置可解析，未请求验证':'默认配置未就绪'],['执行机',remote?'远程隔离服务：'+health.execution_endpoint:'当前服务所在本机；不代表B机'],['前置阻塞',(health.blockers||[]).join('；')||'未报告；仍须每次运行检查']];
      for(const [k,v]of values)byId('isolation-values').append(element('dt',k),element('dd',v));
      renderModelReadiness(models.profiles);
      display('conditions-note','已读取本机状态；模型、费用预留和隔离仍以任务启动检查为准。');
    }catch(e){display('conditions-note','状态读取失败：'+e.message+'；旧状态不作当前依据。');display('health','未核对');display('budget','未核对');}
    finally{button.disabled=false;}
  }
  function renderModelReadiness(profiles){
    const host=byId('model-readiness');host.replaceChildren();
    const groups=[...new Set(profiles.map(p=>p.provider))];
    groups.sort((a,b)=>a==='siliconflow'?-1:b==='siliconflow'?1:a.localeCompare(b));
    for(const provider of groups){
      const group=element(provider==='siliconflow'?'section':'details',null,'readiness-group');
      group.append(element(provider==='siliconflow'?'h3':'summary',provider==='siliconflow'?'硅基流动':provider==='aliyun_maas'?'阿里云百炼':provider));
      const content=element('div',null,'readiness-layout'),scroll=element('div',null,'table-scroll'),table=element('table',null,'run-table readiness-table'),head=element('thead'),tr=element('tr'),body=element('tbody'),detail=element('aside',null,'readiness-detail');
      const rows=profiles.filter(p=>p.provider===provider),aids=[...new Set(rows.map(p=>p.account))];
      tr.append(element('th','模型'));for(const aid of aids)tr.append(element('th',accountLabel(aid)));head.append(tr);table.append(head,body);scroll.append(table);content.append(scroll,detail);group.append(content);host.append(group);
      const buttons=[];
      function select(p,b){
        for(const button of buttons){button.classList.toggle('selected',button===b);button.setAttribute('aria-pressed',String(button===b));}
        detail.replaceChildren(element('h3','检查详情'),element('strong',`${accountLabel(p.account)} · ${p.model.split('/').at(-1)}`));
        const dl=element('dl',null,'config-values');
        for(const check of p.checks||[]){const v=element('dd',check.status,check.ok?'check-ok':'check-pending');dl.append(element('dt',check.name),v);}detail.append(dl);
        if(!(p.checks||[]).length)detail.append(element('p','检查详情未记录；请重启新版本后端后刷新。'));
        const blockers=(p.checks||[]).filter(c=>!c.ok).map(c=>c.name+'：'+c.status);
        detail.append(element('p',blockers.length?'当前阻塞：'+blockers.join('；'):p.configured?'本机配置可解析；未发请求验证。':'四项检查已配置，其他调用条件（如请求时限、地址或额度）仍需核对。','readiness-reason'),element('small','模型 ID：'+p.model),element('p','仅本机检查，不代表平台模型可用或真实调用通过。','footnote'));
      }
      for(const model of [...new Set(rows.map(p=>p.model))]){
        const row=element('tr'),name=element('td',model.split('/').at(-1));name.title=model;row.append(name);
        for(const aid of aids){const cell=element('td'),p=rows.find(p=>p.model===model&&p.account===aid);
          if(p){const b=element('button',p.configured?'配置可解析 ›':'待核对 ›','readiness-status');b.type='button';b.setAttribute('aria-label',`${accountLabel(aid)} ${model}检查详情`);b.onclick=()=>select(p,b);buttons.push(b);cell.append(b);if(buttons.length===1)select(p,b);}
          else cell.textContent='未登记';row.append(cell);
        }body.append(row);
      }
    }
  }
  async function snapshot(run){
    const id=run.task.config_id||'';
    if(snapshotId===id)return;snapshotId=id;
    display('saved-snapshot',id?'读取保存配置中…':'历史未关联保存配置；不能按当前默认值补填。');
    if(!id)return;
    try{const data=await(await api('/configurations/'+encodeURIComponent(id))).json();if(snapshotId===id)display('saved-snapshot',{config_id:data.config_id,created_at:data.created_at,bindings:data.bindings,parameters:data.parameters,strategy:data.strategy,role_overrides:data.role_overrides||'历史未记录',effective_roles:data.effective_roles||'历史未记录',effective_roles_sha256:data.effective_roles_sha256||'历史未记录',role_overrides_sha256:data.role_overrides_sha256||'历史未记录',profiles_sha256:data.profiles_sha256});}
    catch(e){if(snapshotId===id){snapshotId=null;display('saved-snapshot','未能读取保存配置：'+e.message);}}
  }
  function apply(task){
    if(!byId('use-saved-config').checked)return;
    if(!saved)throw new Error('配置已修改或尚未保存，请重新保存。');
    Object.assign(task,saved.parameters,{config_id:saved.config_id,strategy:task.mode==='repair'?saved.strategy:'dynamic'});
  }
  async function init(){
    for(const b of document.querySelectorAll('[data-config-tab]'))b.onclick=()=>{tab(b.dataset.configTab);if(b.dataset.configTab==='conditions')conditions();};
    byId('validate-config').onclick=()=>save(true);byId('save-config').onclick=()=>save(false);byId('refresh-conditions').onclick=conditions;
    for(const id of ['config-rounds','config-calls','config-seconds','config-strategy'])byId(id).oninput=dirty;
    byId('use-saved-config').onchange=()=>{key=null;syncTaskForm();};
    byId('mode').onchange=syncTaskForm;
    byId('task-config-open').onclick=()=>{byId('new-task-dialog').close();showView('config');tab('roles');};
    byId('show-isolation-history').onclick=()=>{if(!currentRun){display('conditions-note','请先在运行记录中选择任务，再查看该任务的隔离探针记录。');return;}showView('workspace');byId('actual-view').click();byId('metric-environment').scrollIntoView({block:'center'});};
    const originalOpen=byId('new-task').onclick;byId('new-task').onclick=()=>{originalOpen();syncTaskForm();};
    try{
      catalog=await(await api('/configuration/catalog')).json();readAccountNames(catalog);byId('binding-rows').replaceChildren();
      for(const [role,[name,description]]of Object.entries(roles)){
        const row=element('tr'),labelCell=element('td'),aCell=element('td'),mCell=element('td'),a=element('select'),m=element('select');m.id='binding-'+role;m.setAttribute('aria-label',name+'模型');a.setAttribute('aria-label',name+'账号');
        labelCell.append(element('strong',name),element('small',description));
        const unique=[...new Set(catalog.profiles.map(p=>p.account))];
        for(const account of unique){const p=catalog.profiles.find(p=>p.account===account),o=element('option',accountName(p));o.value=account;a.append(o);}
        const preferred=catalog.profiles.find(p=>p.profile_ref===catalog.bindings[role]);if(preferred)a.value=preferred.account;
        selectProfiles(role,a,m,catalog.bindings[role]);a.onchange=()=>{selectProfiles(role,a,m);updateCapabilities(role);dirty();};m.onchange=()=>{updateCapabilities(role);dirty();};
        aCell.append(a);mCell.append(m);row.append(labelCell,aCell,mCell);byId('binding-rows').append(row);roleEditor(role,name);updateCapabilities(role);
      }
      for(const [id,k]of [['config-rounds','max_rounds'],['config-calls','max_calls'],['config-seconds','total_seconds']])byId(id).max=catalog.limits[k];
      const seen=new Set();for(const p of catalog.profiles){if(seen.has(p.provider+p.model))continue;seen.add(p.provider+p.model);const row=element('div',p.model,'candidate-model');row.append(element('small',p.provider+' · 受控档案；能力以实际检查为准'));byId('model-candidates').append(row);}
      display('config-message','已读取当前服务的角色绑定；修改仅用于新配置，不改变全局配置或历史任务。');
    }catch(e){display('config-message','配置读取失败：'+e.message);byId('validate-config').disabled=byId('save-config').disabled=true;}
  }
  function syncTaskForm(){
    const using=byId('use-saved-config').checked&&saved;
    const strategy=byId('task-strategy');strategy.disabled=Boolean(using)||byId('mode').value!=='repair';if(using)strategy.value=saved.strategy;
    const source=using?'使用已保存配置 '+saved.config_id.slice(0,16)+'…':'使用默认模型配置；本页未保存草稿不会应用，可先保存以固定配置';
    display('task-mode-hint',byId('mode').value==='repair'?source+'。启动时仍须核验模型授权、费用及隔离条件，创建任务不代表可以执行。':'基线检查不调用模型；公开题目仍需隔离执行。');
    for(const [id,k]of [['rounds','max_rounds'],['calls','max_calls'],['seconds','total_seconds']]){byId(id).disabled=Boolean(using);if(using)byId(id).value=saved.parameters[k];}
  }
  return {init,tab,snapshot,apply};
})();
