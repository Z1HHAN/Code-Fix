'use strict';
// Read-only projection. Display identities for legacy rows are never real step IDs.
(function (root) {
  const names = {repairer:'修复员', detector:'检测员', scheduler:'模型调度员', locator:'定位员',
    pytest:'pytest执行器', program_dispatch:'程序调度', isolation_preflight:'隔离检查',
    handoff_created:'人工移交包', decision:'程序调度（历史事件）',
    configuration_check:'配置与协议检查', budget_check:'调用预算检查', permission_check:'补丁权限检查'};
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
  function provenanceKind(run, events) {
    const result = run.result || {};
    const calls = events.filter(e => e.event === 'agent_call');
    const modes = [result.mode, ...calls.map(e => e.mode)].filter(Boolean);
    const simulated = modes.some(m => /mock|stub|test|offline|simulat/i.test(m));
    const real = !simulated && calls.some(e => e.mode === 'live' && e.model_api_called === true);
    const baseline = run.task?.mode === 'baseline' && calls.length === 0;
    return simulated ? 'simulation' : real ? 'real_requests' : baseline ? 'baseline' : 'unknown';
  }
  function provenance(run, events) {
    return {simulation:'离线／模拟：不代表真实修复成功',real_requests:'已记录真实模型请求；验收范围见工具证据',
      baseline:'基线检查：不含模型修复',unknown:'来源未确认／尚无真实模型请求证据'}[provenanceKind(run,events)];
  }
  function project(run, events) {
    const nodes = [], byId = new Map();
    for (const event of events) {
      if (!['step_started','step_completed','agent_call','test_completed','decision','handoff_created','guard_check'].includes(event.event)) continue;
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
      if (event.event === 'guard_check') {node.started=true;node.completed=true;}
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
      if(node.test){
        const failure=node.test.failure_class,baseline=node.round===0&&node.phase!=='final_check';
        node.executionStatus=node.completed?'执行完成':'执行中';
        node.meaning=baseline&&['assertion_failed','code_runtime_error'].includes(failure)?'缺陷已复现，可以开始修复':baseline&&failure==='passed'?'起点未复现缺陷':failure==='passed'?(node.phase==='final_check'?'最终复验通过':'候选通过，待最终复验'):node.test.verification_status==='inconclusive'?'验证证据不足':['environment_error','timeout'].includes(failure)?'执行环境异常':'候选仍未修好';
        node.statusLabel=node.executionStatus+' · '+node.meaning;
        if(node.completed&&!baseline&&failure!=='passed')node.status='failed';
      }
      node.duration = f.duration_ms ?? null;
    }
    const edges = nodes.filter(n => n.parent && byId.has(n.parent)).map(n => ({from:n.parent,to:n.id,
      action:byId.get(n.parent).decision?.effective_action || byId.get(n.parent).decision?.action || null}));
    const gaps = nodes.filter(n => n.legacy || !n.started || (n.parent && !byId.has(n.parent)) || n.status === 'incomplete').length;
    return {nodes,edges,gaps,provenance:provenance(run,events)};
  }
  function roundLabel(node) {
    if(node.round===0)return node.facts.node_id==='pytest'&&node.phase!=='final_check'?'基线':'';
    return node.round!=null?'第'+node.round+'轮':'';
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
    const currency=cost.currency||(cost.amount_cny!=null?'CNY':'USD');
    const amount = currency === 'CNY' ? cost.amount_cny : cost.amount_usd;
    if (amount == null) return '费用：未记录／未知';
    return cost.status==='reconciled'?`费用：${currency} ${amount}（凭据已人工核对）`:`费用估算：${currency} ${amount}（不是平台账单）`;
  }
  // Positions arrange recorded steps for reading; only graph.edges define execution links.
  function layout(graph) {
    const columns=Math.min(3,Math.max(1,graph.nodes.length));
    const positions=new Map(graph.nodes.map((node,i)=>[node.id,{x:18+(i%columns)*252,y:24+Math.floor(i/columns)*160}]));
    return {positions,width:36+columns*252-47,height:Math.max(265,Math.ceil(graph.nodes.length/columns)*160+80)};
  }
  function mount(document, container, detail, readArtifact, options={}) {
    let selected = null, generation = 0, detailKey = null, renderedKey = null;
    const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent=text; if(cls)e.className=cls; return e; };
    return function render(run, events) {
      const renderKey=JSON.stringify([run.run_id,run.status,run.result,events]);
      if(renderKey===renderedKey)return;
      renderedKey=renderKey;
      const focused=document.activeElement?.dataset?.stepId;
      const graph=project(run,events); container.replaceChildren();
      container.append(el('p',graph.provenance,'trace-provenance'));
      container.append(el('p',graph.gaps ? `${graph.gaps}处历史、未闭合或未加载完整的记录：缺失身份／父步骤时不补造连线。` :
        '连线依据已记录的父步骤；布局顺序不代表额外执行关系。','trace-hint'));
      const viewport=el('div',null,'trace-viewport'),canvas=el('div',null,'trace-canvas');
      const placement=layout(graph);canvas.setAttribute('style',`width:${placement.width}px;height:${placement.height}px`);
      const svg=(tag)=>document.createElementNS?document.createElementNS('http://www.w3.org/2000/svg',tag):el(tag);
      const links=svg('svg');links.setAttribute('class','trace-links');links.setAttribute('aria-label','已记录的父子分支');
      const defs=svg('defs'),marker=svg('marker');marker.setAttribute('id','trace-arrow');marker.setAttribute('viewBox','0 0 10 10');
      marker.setAttribute('refX','9');marker.setAttribute('refY','5');marker.setAttribute('markerWidth','5');marker.setAttribute('markerHeight','5');marker.setAttribute('orient','auto');
      const arrow=svg('path');arrow.setAttribute('d','M0 0 L10 5 L0 10Z');arrow.setAttribute('fill','#7195eb');marker.append(arrow);defs.append(marker);links.append(defs);
      for(const edge of graph.edges){
        const a=placement.positions.get(edge.from),b=placement.positions.get(edge.to);
        const line=svg('path');let path,labelX,labelY;
        if(a.y===b.y&&b.x>a.x){path=`M${a.x+205} ${a.y+50} H${b.x-3}`;labelX=a.x+207;labelY=a.y+38;}
        else{const middle=a.y+126;path=`M${a.x+102} ${a.y+100} V${middle} H${b.x+102} V${b.y-3}`;labelX=Math.min(a.x,b.x)+112;labelY=middle-7;}
        line.setAttribute('d',path);line.setAttribute('class','trace-edge');line.setAttribute('marker-end','url(#trace-arrow)');
        line.setAttribute('data-from',edge.from);line.setAttribute('data-to',edge.to);links.append(line);
        const parent=graph.nodes.find(n=>n.id===edge.from);
        const text=edge.action?(actions[edge.action]||edge.action):(parent.test?.failure_class==='assertion_failed'?'断言失败':parent.test?.verification_status==='passed'?'测试通过':'');
        if(text){const label=svg('text');label.textContent=text;label.setAttribute('x',String(labelX));label.setAttribute('y',String(labelY));label.setAttribute('class','trace-edge-label');links.append(label);}
      }
      canvas.append(links);const list=el('ol',null,'trace-nodes');canvas.append(list);viewport.append(canvas);container.append(viewport);
      const terminal=!['running','created'].includes(run.status);
      if(terminal&&graph.nodes.length)canvas.append(el('div',`运行已结束 · ${run.result?.reason || run.status}（非新增步骤）`,'trace-terminal'));
      container.append(el('div',`已记录 ${graph.nodes.length} 步 · ${costText(run,events)}`,'trace-legend'));
      if (!graph.nodes.length) { canvas.append(el('p','尚无步骤证据；创建任务或静态模板不代表已经执行。','trace-empty'));detail.replaceChildren();detailKey=null;generation++;options.artifactContainer?.replaceChildren();return; }
      if (!graph.nodes.some(n => n.id === selected)) selected=graph.nodes[0].id;
      function show(node) {
        const key=JSON.stringify([run.run_id,node.id,node.status,node.events,artifacts(node,events)]);
        if(key===detailKey)return;
        detailKey=key;
        const ticket=++generation; detail.replaceChildren();
        detail.append(el('h3',`${node.title}${roundLabel(node)?' · '+roundLabel(node):''}`,'inspector-title'));
        detail.append(el('p',node.statusLabel,'step-caption'));
        const d=node.decision;
        const data={身份:node.legacy?'历史事件：没有真实step_id':node.id,父步骤:node.parent || '未记录／首步骤',
          轮次:node.round===0?(roundLabel(node)||'不适用'):node.round ?? '未记录',阶段:node.phase,状态:node.statusLabel,耗时毫秒:node.duration ?? '未记录',
          代码版本:node.facts.code_version ?? '未记录',测试版本:node.facts.test_suite_version ?? '未记录',证据ID:node.evidenceIds};
        const tabs=el('div',null,'inspector-tabs');tabs.setAttribute('role','tablist');
        const panes=['决策详情','关联证据','技术信息'].map(()=>el('div',null,'trace-detail-pane'));
        for(let i=0;i<panes.length;i++){
          const tab=el('button',['决策详情','关联证据','技术信息'][i]);tab.type='button';tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(i===0));
          panes[i].hidden=i!==0;panes[i].setAttribute('role','tabpanel');
          tab.onclick=()=>{panes.forEach((p,j)=>p.hidden=j!==i);for(let j=0;j<tabs.children.length;j++)tabs.children[j].setAttribute('aria-selected',String(j===i));};tabs.append(tab);
        }
        detail.append(tabs,...panes);
        panes[2].append(el('pre',JSON.stringify(data,null,2),'trace-technical'));
        const fields=el('dl',null,'decision-fields');
        const field=(name,value)=>fields.append(el('dt',name),el('dd',value));
        if (d) {
          panes[0].append(el('div',`最终动作：${actions[d.effective_action || d.action] || d.effective_action || d.action || '未记录'}`,'decision-action'));
          field('模型建议',d.model_advice?(actions[d.model_advice.action]||d.model_advice.action||'未记录'):'未调用／未记录');
          field('程序裁决',d.reason||'未记录');field('覆盖原因',d.override_reason||'未记录，不推断模型与程序一致');
          field('命中规则',d.rule_id||'未记录');
          field('证据引用',node.evidenceIds.join('、')||'未记录');
        }else{
          panes[0].append(el('div',node.test?node.statusLabel:'执行状态：'+node.statusLabel,'decision-action'));
          field('步骤职责',node.title);field('测试结论',node.test?.verification_status||'本步骤无测试结论');
          field('失败类型',node.test?.failure_class||'未记录');field('调用来源',node.call?.mode||'本步骤无模型调用记录');
        }
        panes[0].append(fields);
        const viewer=el('pre','选择下方证据文件查看原文。','trace-artifact');
        if(options.artifactContainer){options.artifactContainer.replaceChildren(viewer);options.onArtifact?.(null);}
        const paths=artifacts(node,events);
        const files=el('div');panes[1].append(el('p',paths.length?'以下文件来自事件中的路径与证据引用。':'此步骤未记录可读取的文件路径。'),files);
        const makeFile=(path)=>{
          const button=el('button',path,'trace-file');button.type='button';
          button.onclick=async () => {
            viewer.textContent='读取中…';options.onArtifact?.(path);
            try { const value=await readArtifact(run.run_id,path);if(ticket===generation)viewer.textContent=value; }
            catch(error){if(ticket===generation)viewer.textContent=`证据读取失败：${error.message}；未替换为模拟内容。`;}
          };return button;
        };
        for (const path of paths)files.append(makeFile(path));
        panes[0].append(el('h4',paths.length?'依据证据 · 只读':'尚无关联证据文件','evidence-title'));
        for(const path of paths.slice(0,4))panes[0].append(makeFile(path));
        if(!options.artifactContainer)detail.append(viewer);
        const raw=el('details');raw.append(el('summary','原始事件（页面字段可能已裁剪，完整原文见证据包）'));
        raw.append(el('pre',JSON.stringify(node.events,null,2)));panes[2].append(raw);
      }
      const buttons=[];
      for (const node of graph.nodes) {
        const li=el('li'),point=placement.positions.get(node.id);li.setAttribute('style',`left:${point.x}px;top:${point.y}px`);list.append(li);
        const button=el('button',null,'trace-node '+(node.status==='running'?'trace-active':node.test?.verification_status==='failed'?'trace-test-failed':node.status==='failed'?'trace-failed':''));button.type='button';button.dataset.stepId=node.id;
        const top=el('span',null,'node-top');top.append(el('span',`${node.title}${roundLabel(node)?' · '+roundLabel(node):''}`,'node-title'),el('span',String(buttons.length+1).padStart(2,'0'),'node-index'));
        button.append(top,el('span',(node.status==='failed'?'! ':node.status==='ok'?'✓ ':'')+node.statusLabel,'node-state'),
          el('span',node.decision?(actions[node.decision.effective_action||node.decision.action]||'裁决已记录'):`${node.legacy?'历史格式':node.phase}${node.duration!=null?' · '+node.duration+'ms':''}`,'node-result'));
        button.setAttribute('aria-label',`${node.title}，${node.statusLabel}`);
        button.setAttribute('aria-pressed',String(node.id===selected));
        button.onclick=()=>{selected=node.id;for(const b of buttons)b.setAttribute('aria-pressed',String(b===button));show(node);};
        buttons.push(button);li.append(button);
      }
      show(graph.nodes.find(n=>n.id===selected));
      if(focused)buttons.find(b=>b.dataset.stepId===focused)?.focus();
    };
  }
  const api={project,roundLabel,mergeEvents,provenance,provenanceKind,artifacts,safePath,costText,layout,mount};
  if(typeof module!=='undefined')module.exports=api;
  root.CodeFixTrace=api;
})(typeof globalThis!=='undefined'?globalThis:this);
