'use strict';
const byId = id => document.getElementById(id);
const display = (id, value) => { byId(id).textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); };
const labels = {created:'已创建',running:'运行中',stopped:'已停止',blocked:'条件未就绪',failed:'失败',success:'独立验收通过',paused:'已暂停',interrupted:'已中断',not_run:'未运行',passed:'通过',inconclusive:'证据不足'};
const label = value => labels[value] || value || '未知';
let token = '', selected = localStorage.getItem('codefix.selected') || '', key = null, busy = false, refreshing = false;
let projects = [];
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
  try { await work(); } catch (error) { display('error', error.message); }
  finally { busy = false; byId('create').disabled = false; await refresh(); }
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const runs = await (await api('/tasks')).json();
    const history = byId('history'); history.replaceChildren();
    for (const run of runs) {
      const option = document.createElement('option'); option.value = run.run_id;
      option.textContent = `${run.status} · ${run.task.project} · ${run.task.mode} · ${run.run_id}`;
      history.append(option);
    }
    if (!selected || !runs.some(run => run.run_id === selected)) selected = runs[0]?.run_id || '';
    history.value = selected;
    if (!selected) return;
    localStorage.setItem('codefix.selected', selected);
    const snapshot = selected;
    const [run, events] = await Promise.all([
      api(`/tasks/${snapshot}`).then(x => x.json()), api(`/tasks/${snapshot}/events`).then(x => x.json())]);
    if (snapshot !== selected) return;
    const result = run.result || {};
    display('status', `${run.run_id}\n状态：${run.status} · 题目：${run.task.project} · 模式：${run.task.mode}`);
    display('summary', [
      `运行模式：${run.task.mode === 'baseline' ? '原始基线检查' : '真实模型修复'}`,
      `修复轮次：${result.rounds_used || 0} / ${run.task.max_rounds}`,
      `模型调用：${result.model_calls || 0} 次`,
      `开发测试：${label(result.dev_result)}`,
      `独立验收：${label(result.final_result || 'not_run')}`,
      `说明：${result.reason || '任务已保存，等待启动'}`
    ].join('\n'));
    const names = {started:'开始运行',finished:'运行结束',agent_call:'Agent调用',test_completed:'测试完成',
      candidate_applied:'候选已应用',decision:'调度决策',isolation_checked:'隔离检查',control_requested:'收到控制请求'};
    display('events', events.length ? events.map(event => [event.created_at, names[event.event] || event.event,
      event.role || '', event.status ? label(event.status) : '', event.failure_class || '', event.reason || ''].filter(Boolean).join(' · ')).join('\n\n') : '尚无执行事件');
    const tests = events.filter(x => x.event === 'test_completed');
    const latestTest = tests.filter(x => x.phase === 'development').at(-1);
    display('development', result.stdout_excerpt || (latestTest ? `${label(latestTest.verification_status)}\n${latestTest.failure_class}\n${JSON.stringify(latestTest.counts || {})}` : '未运行'));
    display('final', label(result.final_result || 'not_run'));
    const decisions = events.filter(x => x.event === 'decision');
    display('decisions', decisions.length ? decisions.map(x => `第${x.round}轮：${x.action}\n${x.reason}\n${x.model_advice ? '模型建议：'+x.model_advice.action+' · '+x.model_advice.reason : ''}`).join('\n\n') : '尚无调度决策');
    const patch = events.filter(x => x.event === 'candidate_applied').at(-1);
    display('patch', '尚无已应用候选');
    if (patch) {
      const response = await api(`/tasks/${snapshot}/artifact?path=${encodeURIComponent(`patch-${patch.round}/patch.diff`)}`);
      const text = await response.text(); if (snapshot === selected) display('patch', text);
    }
    byId('start').disabled = busy || run.status !== 'created';
    byId('pause').disabled = byId('stop').disabled = busy || run.status !== 'running' || run.task.mode !== 'repair';
    byId('report').disabled = false;
    byId('bundle').disabled = ['created', 'running'].includes(run.status);
  } catch (error) { display('error', error.message); }
  finally { refreshing = false; }
}
byId('create').onclick = () => action(async () => {
  key ||= crypto.randomUUID();
  const task = {project:byId('project').value, mode:byId('mode').value, description:byId('description').value,
    max_rounds:Number(byId('rounds').value), max_calls:Number(byId('calls').value), total_seconds:Number(byId('seconds').value)};
  const run = await (await api('/tasks', {method:'POST', headers:{'Idempotency-Key':key}, body:JSON.stringify(task)})).json();
  selected = run.run_id; key = null;
});
for (const id of ['description', 'project', 'mode', 'rounds', 'calls', 'seconds']) byId(id).oninput = () => { key = null; };
byId('project').onchange = () => {
  key = null;
  const project = projects.find(item => item.project === byId('project').value);
  if (!project) return;
  byId('description').value = project.description;
  display('project-hint', `${project.source_paths.join('、')} · 原生测试${project.expected_test_count}项。` +
    (project.requires_isolation ? '公开题目须隔离执行；条件未就绪会阻止运行。' : '本地冻结基线可直接检查。'));
};
byId('start').onclick = () => action(async () => { if (selected) await api(`/tasks/${selected}/start`, {method:'POST'}); });
for (const operation of ['pause', 'stop']) byId(operation).onclick = () => action(async () => {
  await api(`/tasks/${selected}/control`, {method:'POST', body:JSON.stringify({action:operation})});
});
byId('history').onchange = () => { selected = byId('history').value; refresh(); };
byId('refresh').onclick = refresh;
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
      option.textContent = project.title; return option;
    });
    byId('project').replaceChildren(...choices); byId('project').onchange();
    display('health', await (await api('/health')).json());
    display('connection', '已连接真实任务后端');
    await refresh(); setInterval(refresh, 1200);
  } catch (error) { display('connection', '后端连接失败'); display('error', error.message); }
})();
