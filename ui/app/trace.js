'use strict';
// Read-only projection. Display identities for legacy rows are never real step IDs.
(function (root) {
  const names = {repairer:'修复员', detector:'检测员', scheduler:'模型调度员', locator:'定位员',
    pytest:'pytest执行器', program_dispatch:'程序调度', isolation_preflight:'隔离检查',
    handoff_created:'人工移交包', decision:'程序调度（历史事件）'};
  const states = {ok:'完成', running:'执行中', failed:'失败', blocked:'阻塞', interrupted:'中断',
    passed:'通过', inconclusive:'证据不足', timeout:'超时'};
  const actions = {repair:'重试修复', locate:'重新定位', final_check:'进入最终复验',
    stop:'停止', pause:'暂停／待人工处理', finish:'结束', retry:'重试'};
  const unique = values => [...new Set(values.filter(Boolean))];
  function mergeEvents(previous, page) {
    const indexed = new Map();
    for (const event of [...previous, ...page]) {
      const key = Number.isInteger(event.sequence) ? `seq:${event.sequence}` : `old:${JSON.stringify(event)}`;
      indexed.set(key, event);
    }
    return [...indexed.values()].sort((a,b) => (a.sequence ?? 0) - (b.sequence ?? 0));
  }
  function provenance(run, events) {
    const result = run.result || {};
    const calls = events.filter(e => e.event === 'agent_call');
    const modes = [result.mode, ...calls.map(e => e.mode)].filter(Boolean);
    const simulated = modes.some(m => /mock|stub|test|offline|simulat/i.test(m));
    const real = !simulated && calls.some(e => e.mode === 'live' && e.model_api_called === true);
    const baseline = run.task?.mode === 'baseline' && calls.length === 0;
    return simulated ? '离线／模拟：不代表真实修复成功' : real ? '已记录真实模型请求；验收范围见工具证据' :
      baseline ? '基线检查：不含模型修复' : '来源未确认／尚无真实模型请求证据';
  }
  function project(run, events) {
    const nodes = [], byId = new Map();
    for (const event of events) {
      if (!['step_started','step_completed','agent_call','test_completed','decision','handoff_created'].includes(event.event)) continue;
      const legacy = !event.step_id;
      const id = event.step_id || `legacy:${event.sequence ?? nodes.length}`;
      let node = byId.get(id);
      if (!node) {
        node = {id, legacy, events:[], facts:{}, evidenceIds:[], started:false, completed:false};
        nodes.push(node); byId.set(id,node);
      }
      node.events.push(event);
      node.facts = {...node.facts, ...event};
      node.evidenceIds = unique([...node.evidenceIds, ...(event.evidence_ids || []), event.evidence_id]);
      if (event.event === 'step_started') node.started = true;
      if (event.event === 'step_completed') node.completed = true;
      if (event.event === 'decision') node.decision = event;
      if (event.event === 'test_completed') node.test = event;
      if (event.event === 'agent_call') node.call = event;
    }
    const terminal = !['created','running'].includes(run.status);
    for (const node of nodes) {
      const f = node.facts;
      node.parent = f.parent_step_id || null;
      node.title = names[f.node_id || f.role || f.event] || f.node_id || f.role || f.event;
      node.round = f.round ?? null;
      node.phase = f.phase || '未记录阶段';
      node.status = node.completed ? (f.status || 'ok') : node.started ? (terminal ? 'incomplete' : 'running') :
        (node.test?.verification_status || node.call?.status || 'unknown');
      node.statusLabel = node.status === 'incomplete' ? '步骤未闭合／事件尚未加载完整' : states[node.status] || '状态未记录';
      if(node.test)node.statusLabel += ' · ' + ({passed:'验证通过',failed:'验证未通过',inconclusive:'验证证据不足',not_run:'验证未运行'}[node.test.verification_status] || '验证结论未记录');
      node.duration = f.duration_ms ?? null;
    }
    const edges = nodes.filter(n => n.parent && byId.has(n.parent)).map(n => ({from:n.parent,to:n.id,
      action:byId.get(n.parent).decision?.effective_action || byId.get(n.parent).decision?.action || null}));
    const gaps = nodes.filter(n => n.legacy || !n.started || (n.parent && !byId.has(n.parent)) || n.status === 'incomplete').length;
    return {nodes,edges,gaps,provenance:provenance(run,events)};
  }
  function safePath(path) {
    return typeof path === 'string' && path.length > 0 && !/[\\:\x00-\x1f]/.test(path) &&
      !path.startsWith('/') && path.split('/').every(p => p && p !== '.' && p !== '..');
  }
  function artifacts(node, events) {
    const paths = [];
    for (const e of node.events.concat(events.filter(e => node.evidenceIds.includes(e.evidence_id)))) {
      if (safePath(e.artifact_path)) paths.push(e.artifact_path);
      if (safePath(e.evidence_path)) for (const key of ['junit_path','structured_path','stdout_path','stderr_path']) {
        if (safePath(e[key])) paths.push(`${e.evidence_path}/${e[key]}`);
      }
      if (e.event === 'agent_call' && /^m-[a-f0-9]+$/.test(e.call_id || '')) {
        paths.push(`calls/${e.call_id}/input.json`, `calls/${e.call_id}/output.json`);
      }
    }
    return unique(paths).filter(safePath);
  }
  function costText(run, events) {
    const cost = run.result?.cost || [...events].reverse().find(e => e.counters?.cost)?.counters.cost;
    if (!cost) return '费用：未记录／未知';
    if (cost.status === 'not_called') return '费用：未调用模型';
    if (cost.status === 'not_applicable') return '费用：离线模拟，无真实账单';
    if (['unknown','pending'].includes(cost.status)) return '费用：未知／预留，尚未结算';
    const amount = cost.currency === 'CNY' ? cost.amount_cny : cost.amount_usd;
    if (amount == null) return '费用：未记录／未知';
    return `费用估算：${cost.currency || 'USD'} ${amount}（不是平台账单）`;
  }
  function mount(document, container, detail, readArtifact) {
    let selected = null, generation = 0, detailKey = null, renderedKey = null;
    const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent=text; if(cls)e.className=cls; return e; };
    return function render(run, events) {
      const renderKey=JSON.stringify([run.run_id,run.status,run.result,events]);
      if(renderKey===renderedKey)return;
      renderedKey=renderKey;
      const focused=document.activeElement?.dataset?.stepId;
      const graph=project(run,events); container.replaceChildren();
      container.append(el('p',graph.provenance,'trace-provenance'));
      container.append(el('p',`实际记录 ${graph.nodes.length} 步 · ${costText(run,events)}`,'hint'));
      container.append(el('p',graph.gaps ? `${graph.gaps}处历史、未闭合或未加载完整的记录：缺失身份／父步骤时不补造连线。` :
        '连线仅来自已记录的parent_step_id；显示顺序不代表额外执行关系。','hint'));
      const list=el('ol',null,'trace-nodes');container.append(list);
      if (!graph.nodes.length) { container.append(el('p','尚无步骤证据；创建任务或静态模板不代表已经执行。'));detail.replaceChildren();detailKey=null;generation++;return; }
      if (!graph.nodes.some(n => n.id === selected)) selected=graph.nodes[0].id;
      function show(node) {
        const key=JSON.stringify([run.run_id,node.id,node.status,node.events,artifacts(node,events)]);
        if(key===detailKey)return;
        detailKey=key;
        const ticket=++generation; detail.replaceChildren();
        detail.append(el('h3',node.title));
        const d=node.decision;
        const data={身份:node.legacy?'历史事件：没有真实step_id':node.id,父步骤:node.parent || '未记录／首步骤',
          轮次:node.round ?? '未记录',阶段:node.phase,状态:node.statusLabel,耗时毫秒:node.duration ?? '未记录',
          代码版本:node.facts.code_version ?? '未记录',测试版本:node.facts.test_suite_version ?? '未记录',证据ID:node.evidenceIds};
        detail.append(el('pre',JSON.stringify(data,null,2)));
        if (d) {
          detail.append(el('p',`程序实际动作：${actions[d.effective_action || d.action] || d.effective_action || d.action || '未记录'} · 规则 ${d.rule_id || '未记录'}`));
          detail.append(el('p',`裁决原因：${d.reason || '未记录'}`));
          detail.append(el('p',`模型建议：${d.model_advice ? JSON.stringify(d.model_advice) : '未调用／未记录'}`));
          detail.append(el('p',`覆盖原因：${d.override_reason || '未记录，不推断模型与程序一致'}`));
        }
        const viewer=el('pre','选择下方证据文件查看原文。','trace-artifact');
        const paths=artifacts(node,events);
        detail.append(el('p',paths.length ? '已记录的证据文件（只读）' : '此步骤未记录可读取的文件路径；可查看原始事件或下载报告。','hint'));
        for (const path of paths) {
          const button=el('button',path,'trace-file');button.type='button';
          button.onclick=async () => {
            viewer.textContent='读取中…';
            try { const value=await readArtifact(run.run_id,path);if(ticket===generation)viewer.textContent=value; }
            catch(error){if(ticket===generation)viewer.textContent=`证据读取失败：${error.message}；未替换为模拟内容。`;}
          };detail.append(button);
        }
        detail.append(viewer);
        const raw=el('details');raw.append(el('summary','原始事件（页面字段可能已裁剪，完整原文见证据包）'));
        raw.append(el('pre',JSON.stringify(node.events,null,2)));detail.append(raw);
      }
      const buttons=[];
      for (const node of graph.nodes) {
        const li=el('li');list.append(li);
        const edge=graph.edges.find(e=>e.to===node.id);
        if(edge)li.append(el('div',`↓ 来自 ${edge.from}${edge.action ? ' · '+(actions[edge.action] || edge.action) : ''}`,'trace-edge'));
        const button=el('button',`${node.title} · 第${node.round ?? '?'}轮 · ${node.phase}\n${node.statusLabel}${node.duration != null ? ' · '+node.duration+'ms' : ''}`,
          'trace-node '+(node.status==='running'?'trace-active':''));button.type='button';button.dataset.stepId=node.id;
        button.setAttribute('aria-pressed',String(node.id===selected));
        button.onclick=()=>{selected=node.id;for(const b of buttons)b.setAttribute('aria-pressed',String(b===button));show(node);};
        buttons.push(button);li.append(button);
      }
      show(graph.nodes.find(n=>n.id===selected));
      if(focused)buttons.find(b=>b.dataset.stepId===focused)?.focus();
    };
  }
  const api={project,mergeEvents,provenance,artifacts,safePath,costText,mount};
  if(typeof module!=='undefined')module.exports=api;
  root.CodeFixTrace=api;
})(typeof globalThis!=='undefined'?globalThis:this);
