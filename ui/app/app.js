'use strict';
const eventCache = new Map();
const byId = id => document.getElementById(id);
const display = (id, value) => { byId(id).textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); if(id==='error')byId(id).hidden=!value; };
const labels = {created:'已创建',running:'运行中',stopped:'已停止',blocked:'条件未就绪',failed:'失败',success:'独立验收通过',paused:'已暂停',interrupted:'已中断',not_run:'未运行',passed:'通过',inconclusive:'证据不足'};
const label = value => labels[value] || value || '未知';
let token = '', selected = localStorage.getItem('codefix.selected') || '', key = null, busy = false, refreshing = false;
let projects = [], allRuns=[], currentRun=null, currentEvents=[], recordsKey='', historyKey='', scale=1;
const poller=CodeFixPolling.createPoller({refresh:refreshNow,isRunning:()=>allRuns.some(r=>r.status==='running'),isHidden:()=>document.hidden});
function refresh(){return poller.request();}
document.addEventListener('visibilitychange',()=>poller.visibilityChanged());
window.addEventListener('pagehide',()=>poller.stop());
window.addEventListener('pageshow',event=>{if(event.persisted)poller.start();});
const element=(tag,text,cls)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;};
const shortId=id=>id?.startsWith('run-')?'run-'+id.slice(4,12):id||'未记录';
const taskName=run=>projects.find(p=>p.project===run.task.project)?.title||run.task.project||'未记录题目';
const modeName=run=>run.task.mode==='baseline'?'基线检查':run.task.mode==='repair'?'缺陷修复':'历史任务';
function showEvidence(name){
  for(const button of document.querySelectorAll('[data-evidence]'))button.setAttribute('aria-selected',String(button.dataset.evidence===name));
  for(const id of ['artifact','testing','diff','calls','raw'])byId('evidence-'+id).hidden=id!==name;
}
function showView(view,updateHash=true){
  if(!['records','workspace','config','comparisons','evidence'].includes(view))view='records';
  const actual=view==='evidence'?'workspace':view;
  for(const id of ['records','workspace','config','comparisons'])byId('view-'+id).hidden=id!==actual;
  for(const button of document.querySelectorAll('[data-view]')){
    if(button.dataset.view===view)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
  }
  if(updateHash&&location.hash!=='#'+view)history.replaceState(null,'','#'+view);
  const title=view==='records'?'运行记录':view==='config'?'模型与配置':view==='comparisons'?'实验对照':currentRun?taskName(currentRun)+' · '+modeName(currentRun):'编排工作台';
  display('page-title',title);display('breadcrumb','工作空间 / '+({records:'运行记录',workspace:'编排工作台',config:'模型与配置',comparisons:'实验对照',evidence:'证据浏览'}[view]));
  display('page-subtitle',view==='records'?'从一次运行出发，查看Agent流转与决策依据。':view==='comparisons'?'按显式清单比较已有证据；创建、刷新和导出不会启动任务。':view==='config'?'设置新任务模型与参数，核对费用条件，查看只读运行记录。':currentRun?`${shortId(currentRun.run_id)} · ${currentRun.task.strategy || '策略未单独记录'} · ${label(currentRun.status)}`:'请先从运行记录选择任务。');
  if(view==='comparisons'&&window.CodeFixComparisons)CodeFixComparisons.load();
  if(view==='evidence'&&updateHash){showEvidence('artifact');document.querySelector('.evidence-dock').scrollIntoView({block:'start',behavior:'smooth'});}
}
function selectRun(id){selected=id;showView('workspace');refresh();}
function renderRecords(){
  const search=byId('search-runs').value.trim().toLowerCase(),status=byId('filter-status').value;
  const rows=allRuns.filter(run=>(!status||run.status===status)&&(!search||`${taskName(run)} ${run.run_id} ${run.task.project}`.toLowerCase().includes(search)));
  const signature=JSON.stringify([rows,search,status,projects,eventCache.size,currentRun?.run_id,currentEvents.length]);
  if(signature===recordsKey)return;recordsKey=signature;byId('run-rows').replaceChildren();display('record-count',rows.length);
  for(const run of rows){
    const row=element('tr'),nameCell=element('td'),name=element('button',`${taskName(run)} · ${modeName(run)}`,'run-name');
    name.type='button';name.onclick=()=>selectRun(run.run_id);nameCell.append(name,element('span',shortId(run.run_id),'run-id'));nameCell.title=run.run_id;
    const statusCell=element('td');statusCell.append(element('span',label(run.status)+(run.archived_at?' · 已归档':''),'badge '+(run.status==='success'?'good':run.status==='failed'?'bad':['blocked','stopped'].includes(run.status)?'warn':run.status==='running'?'blue':'')));
    const source=CodeFixTrace.provenance(run,eventCache.get(run.run_id)||[]);
    const sourceCell=element('td');sourceCell.append(element('span',source.includes('离线')?'离线演示':source.includes('已记录真实')?'真实调用':source.includes('基线')?'基线检查':'待核对事件','badge'));
    const date=run.updated_at?new Date(run.updated_at):null;
    const timeCell=element('td',date&&!Number.isNaN(date.getTime())?date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'未记录');
    const actionCell=element('td'),open=element('button','查看链路 →','btn compact');open.type='button';open.onclick=()=>selectRun(run.run_id);actionCell.append(open);
    const archive=element('button',run.archived_at?'恢复':'归档','btn compact');archive.type='button';archive.disabled=run.status==='running';
    archive.setAttribute('aria-label',(run.archived_at?'恢复':'归档')+' '+shortId(run.run_id));
    archive.onclick=()=>action(async()=>{await api(`/tasks/${run.run_id}/${run.archived_at?'restore':'archive'}`,{method:'POST'});});actionCell.append(archive);
    row.append(nameCell,statusCell,sourceCell,timeCell,actionCell);byId('run-rows').append(row);
  }
  byId('records-empty').hidden=rows.length!==0;
  byId('records-empty').textContent=allRuns.length?'没有符合筛选条件的运行记录。':'暂无运行记录。点击右上角“新建任务”开始。';
}
function renderRun(run,events){
  currentRun=run;currentEvents=events;
  const result=run.result||{},source=CodeFixTrace.provenance(run,events);
  display('metric-status',label(run.status));display('metric-rounds',`${result.rounds_used ?? '未记录'} / ${run.task.max_rounds ?? '未记录'}`);
  display('metric-calls',result.model_calls==null?'未记录':`${result.model_calls} 次${source.includes('离线')?' · 模拟':''}`);
  display('metric-cost',CodeFixTrace.costText(run,events).replace(/^费用(?:估算)?[：:]/,''));
  const isolation=events.filter(e=>e.event==='isolation_checked').at(-1);
  display('metric-environment',source.includes('离线')?'模拟事件':isolation?.isolation_ready===true?'隔离探针通过':isolation?'隔离未就绪':run.task.mode==='baseline'?'查看测试证据':'未记录');
  display('run-notice',`${source} · 最终验收：${label(result.final_result||'not_run')}。暂停/停止按服务端控制边界生效。`);
  display('config-json',run.task);
  if(window.CodeFixConfig)CodeFixConfig.snapshot(run);
  byId('config-values').replaceChildren();
  for(const [name,value] of [['题目',taskName(run)],['运行模式',modeName(run)],['策略',run.task.strategy||'历史参数未单独记录'],['最大修复轮数',run.task.max_rounds??'未记录'],['模型调用上限',run.task.max_calls??'未记录'],['总时限',run.task.total_seconds==null?'未记录':run.task.total_seconds+' 秒'],['运行编号',run.run_id]])byId('config-values').append(element('dt',name),element('dd',String(value)));
  const calls=events.filter(e=>e.event==='agent_call');
  const info=calls.map(e=>({角色:e.role||e.node_id,模型:e.model||e.model_name||'事件未记录',服务商:e.provider||'事件未记录',来源:e.mode||'未记录',真实请求:e.model_api_called===true,状态:e.status||'未记录',调用编号:e.call_id||'未记录'}));
  display('call-records',info.length?info:'尚无模型调用事件；不能据配置推断已经请求。');
  display('config-models',info.length?info:'暂无调用记录，不能推断有效模型绑定。');
  showView(location.hash.slice(1)||'records',false);
}
const renderRecordedTrace = CodeFixTrace.mount(document, byId('trace-flow'), byId('trace-detail'),
  async (runId, path) => (await api(`/tasks/${runId}/artifact?path=${encodeURIComponent(path)}`)).text(),{
    artifactContainer:byId('artifact-viewer'),onArtifact:path=>{display('artifact-path',path||'选择节点中的证据文件');if(path)showEvidence('artifact');}
  });
const renderArchitecture = CodeFixArchitecture.mount(document, byId('architecture-flow'), CodeFixTrace, (id,run,events) => {
  renderRecordedTrace(run,events);
  const b=[...byId('trace-flow').querySelectorAll('.trace-node')].find(n=>n.dataset.stepId===id);if(b)b.click();
});
function renderTrace(run,events){renderArchitecture(run,events);}
async function api(path, options = {}) {
  const response = await fetch('/api/v3' + path, { ...options, headers: {'Content-Type': 'application/json', 'X-Codefix-Token': token, ...(options.headers || {})} });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 ${response.status}`);
  }
  return response;
}
async function action(work) {
  if (busy) return;
  busy = true; byId('create').disabled = true; byId('start').disabled = true;
  display('error', '');
  try { await work(); } catch (error) { display('error', error.message);display('form-error',error.message); }
  finally { busy = false; byId('create').disabled = false; await refresh(); }
}
async function refreshNow() {
  if (refreshing) return;
  refreshing = true;
  try {
    const includeArchived=byId('include-archived').checked;
    const [runs, budget] = await Promise.all([api('/tasks'+(includeArchived?'?include_archived=true':'')).then(x => x.json()), api('/budget').then(x => x.json())]);
    if(includeArchived!==byId('include-archived').checked)return true;
    allRuns=runs;renderRecords();
    display('budget', budgetText(budget));
    const history = byId('history');
    const nextHistoryKey=JSON.stringify(runs.map(run=>[run.run_id,run.status,taskName(run)]));
    if(nextHistoryKey!==historyKey){historyKey=nextHistoryKey;history.replaceChildren();
    for (const run of runs) {
      const option = document.createElement('option'); option.value = run.run_id;
      option.textContent = `${taskName(run)} · ${label(run.status)} · ${shortId(run.run_id)}`;
      history.append(option);
    }}
    if (!selected || !runs.some(run => run.run_id === selected)) selected = runs[0]?.run_id || '';
    history.value = selected;
    if (!selected) {
      currentRun=null;currentEvents=[];
      renderTrace({run_id:'',status:'created',task:{}},[]);
      for(const id of ['start','pause','stop','report','bundle','archive-run'])byId(id).disabled=true;
      for(const id of ['status','summary','events','development','final','decisions','patch','call-records'])display(id,'尚未选择任务');
      for(const id of ['metric-status','metric-rounds','metric-calls','metric-cost','metric-environment'])display(id,'未选择');
      display('run-notice','请选择一条运行记录；已归档任务可在运行记录中勾选“包含已归档”查看或恢复。');
      localStorage.removeItem('codefix.selected');
      showView(location.hash.slice(1)||'records',false);return true;
    }
    localStorage.setItem('codefix.selected', selected);
    const snapshot = selected;
    const cached = eventCache.get(snapshot) || [];
    const cursor = cached.at(-1)?.sequence || 0;
    const [run, page] = await Promise.all([
      api(`/tasks/${snapshot}`).then(x => x.json()), api(`/tasks/${snapshot}/events?after_sequence=${cursor}&limit=200`).then(x => x.json())]);
    if (snapshot !== selected) return;
    const events = CodeFixTrace.mergeEvents(cached, page);
    eventCache.set(snapshot, events);
    const result = run.result || {};
    renderTrace(run, events);
    renderRun(run,events);renderRecords();
    const canvas=byId('trace-flow').querySelector('.trace-canvas');if(canvas)canvas.style.zoom=scale;const architecture=byId('architecture-flow').querySelector('.architecture-canvas');if(architecture){architecture.style.zoom=scale;}
    display('status', `${run.run_id}\n状态：${run.status} · 题目：${run.task.project} · 模式：${run.task.mode}`);
    display('summary', [
      `执行来源：${CodeFixTrace.provenance(run, events)}`,
      `修复轮次：${result.rounds_used ?? '未记录'} / ${run.task.max_rounds}`,
      `模型调用：${result.model_calls ?? '未记录'} 次`,
      `开发测试：${label(result.dev_result)}`,
      `独立验收：${label(result.final_result || 'not_run')}`,
      `说明：${result.reason || (run.status === 'created' ? '任务已保存，等待启动' : '历史记录未提供说明')}`
    ].join('\n'));
    const names = {started:'开始运行',finished:'运行结束',agent_call:'Agent调用',test_completed:'测试完成',
      candidate_applied:'候选已应用',decision:'调度决策',isolation_checked:'隔离检查',control_requested:'收到控制请求',archived:'已归档',restored:'已恢复'};
    display('events', events.length ? events.map(event => [event.created_at, names[event.event] || event.event,
      event.role || '', event.status ? label(event.status) : '', event.failure_class || '', event.reason || ''].filter(Boolean).join(' · ')).join('\n\n') : '尚无执行事件');
    const tests = events.filter(x => x.event === 'test_completed');
    const latestTest = tests.filter(x => x.phase === 'development').at(-1);
    display('development', result.stdout_excerpt || (latestTest ? `${label(latestTest.verification_status)}\n${latestTest.failure_class}\n${JSON.stringify(latestTest.counts || {})}` : '未运行'));
    display('final', label(result.final_result || 'not_run'));
    const decisions = events.filter(x => x.event === 'decision');
    display('decisions', decisions.length ? decisions.map(x => `轮次${x.round ?? '未记录'}：${x.effective_action || x.action || '未记录'}\n${x.reason || '原因未记录'}\n${x.model_advice ? '模型建议：'+x.model_advice.action+' · '+x.model_advice.reason : ''}`).join('\n\n') : '尚无调度决策');
    const patch = events.filter(x => x.event === 'candidate_applied').at(-1);
    display('patch', '尚无已应用候选');
    if (patch) {
      const response = await api(`/tasks/${snapshot}/artifact?path=${encodeURIComponent(`patch-${patch.round}/patch.diff`)}`);
      const text = await response.text(); if (snapshot === selected) display('patch', text);
    }
    byId('start').disabled = busy || run.status !== 'created' || Boolean(run.archived_at);
    byId('archive-run').disabled=busy||run.status==='running';byId('archive-run').textContent=run.archived_at?'恢复任务':'归档任务';
    byId('pause').disabled = byId('stop').disabled = busy || run.status !== 'running' || run.task.mode !== 'repair';
    byId('report').disabled = false;
    byId('bundle').disabled = ['created', 'running'].includes(run.status);
    return true;
  } catch (error) { display('error', error.message); display('budget', '额度状态暂时无法刷新，请重新核对；旧余额不作当前余额。');return false; }
  finally { refreshing = false; }
}
byId('task-form').onsubmit = event => {event.preventDefault();if(!byId('task-form').reportValidity())return;action(async () => {
  key ||= crypto.randomUUID();
  const task = {project:byId('project').value, mode:byId('mode').value, strategy:byId('mode').value==='repair'?byId('task-strategy').value:'dynamic', description:byId('description').value,
    max_rounds:Number(byId('rounds').value), max_calls:Number(byId('calls').value), total_seconds:Number(byId('seconds').value)};
  if(window.CodeFixConfig)CodeFixConfig.apply(task);
  const run = await (await api('/tasks', {method:'POST', headers:{'Idempotency-Key':key}, body:JSON.stringify(task)})).json();
  selected = run.run_id; key = null;byId('new-task-dialog').close();showView('workspace');
});};
for (const id of ['description', 'project', 'mode', 'rounds', 'calls', 'seconds']) byId(id).oninput = () => { key = null; };
byId('project').onchange = () => {
  key = null;
  const project = projects.find(item => item.project === byId('project').value);
  if (!project) return;
  byId('description').value = project.description;
  display('project-hint', `${project.source_paths.join('、')} · 预登记原生测试${project.expected_test_count}项。` +
    (project.admission_blocker || (project.requires_isolation ? '公开题目须隔离执行；尚待实际测试收集和基线验证。' : '本地冻结基线可直接检查。')));
};
byId('start').onclick = () => action(async () => { if (selected) await api(`/tasks/${selected}/start`, {method:'POST'}); });
for (const operation of ['pause', 'stop']) byId(operation).onclick = () => action(async () => {
  await api(`/tasks/${selected}/control`, {method:'POST', body:JSON.stringify({action:operation})});
});
byId('history').onchange = () => { selected = byId('history').value; refresh(); };
byId('include-archived').onchange=()=>refresh();
byId('archive-run').onclick=()=>action(async()=>{if(currentRun)await api(`/tasks/${currentRun.run_id}/${currentRun.archived_at?'restore':'archive'}`,{method:'POST'});});
byId('refresh').onclick = refresh;
for(const button of document.querySelectorAll('[data-view]'))button.onclick=()=>showView(button.dataset.view);
window.addEventListener('hashchange',()=>showView(location.hash.slice(1)||'records',false));
byId('show-config').onclick=()=>{showView('config');if(window.CodeFixConfig)CodeFixConfig.tab('snapshot');};
byId('search-runs').oninput=renderRecords;byId('filter-status').onchange=renderRecords;
byId('new-task').onclick=()=>{display('form-error','');byId('new-task-dialog').showModal();byId('project').focus();};
byId('close-task').onclick=byId('cancel-task').onclick=()=>byId('new-task-dialog').close();
for(const button of document.querySelectorAll('[data-evidence]'))button.onclick=()=>showEvidence(button.dataset.evidence);
byId('copy-evidence').onclick=async()=>{try{await navigator.clipboard.writeText(byId('artifact-viewer').textContent);display('artifact-path','已复制 · '+(byId('artifact-path').textContent.replace(/^已复制 · /,'')));}catch{display('error','浏览器未允许复制，可选中文本手动复制。');}};
for(const [id,change] of [['zoom-in',.1],['zoom-out',-.1]])byId(id).onclick=()=>{
  scale=Math.max(.5,Math.min(1.5,Math.round((scale+change)*10)/10));const canvas=byId('trace-flow').querySelector('.trace-canvas');if(canvas)canvas.style.zoom=scale;const architecture=byId('architecture-flow').querySelector('.architecture-canvas');if(architecture){architecture.style.zoom=scale;}
};
byId('locate-last').onclick=()=>{byId('actual-view').click();byId('architecture-flow').querySelector('[data-action=follow]')?.click();const current=byId('architecture-flow').querySelector('.arch-active');if(current){current.scrollIntoView({block:'nearest',inline:'nearest'});current.focus({preventScroll:true});}};
for(const [id,guide] of [['actual-view',false],['template-view',true]])byId(id).onclick=()=>{
  byId('flow-guide').hidden=!guide;byId('trace-flow').hidden=guide;byId('architecture-flow').hidden=guide;byId('recorded-trace').hidden=guide;
  for(const [button,active] of [['actual-view',!guide],['template-view',guide]]){byId(button).classList.toggle('active',active);byId(button).setAttribute('aria-pressed',String(active));}
};
showView(location.hash.slice(1)||'records',false);
async function download(kind) {
  await action(async () => {
    const response = await api(`/tasks/${selected}/${kind}`);
    const blob = await response.blob(); const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = `${selected}.${kind === 'bundle' ? 'zip' : 'json'}`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
byId('report').onclick = () => download('report'); byId('bundle').onclick = () => download('bundle');
(async () => {
  try {
    token = (await (await api('/session')).json()).token;
    projects = await (await api('/projects')).json();
    const choices = projects.map(project => {
      const option = document.createElement('option'); option.value = project.project;
      option.textContent = project.title + (project.admission_blocker ? '（测试策略待确认）' : '');
      option.disabled = Boolean(project.admission_blocker); return option;
    });
    byId('project').replaceChildren(...choices); byId('project').onchange();
    display('health', await (await api('/health')).json());
    if(window.CodeFixConfig)await CodeFixConfig.init();
    display('connection', '已连接任务后端 · 运行来源见Trace标识');
    await poller.start();
  } catch (error) { display('connection', '后端连接失败'); display('error', error.message); }
})();
