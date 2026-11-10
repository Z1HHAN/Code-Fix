'use strict';
window.CodeFixComparisons=(()=>{
  const names={fixed:'固定顺序',dynamic:'动态编排'},scopes={formal:'正式计划（真实证据待核验）',controlled:'受控分支（不计正式成功率）',offline:'离线夹具（不是真实验收）'};
  let current=null,loading=false;
  const value=n=>n==null?'未记录 / 未知':String(n),percent=n=>n==null?'无分母':(n*100).toFixed(1)+'%';
  function table(headers,rows){const t=element('table',null,'run-table'),head=element('thead'),tr=element('tr'),body=element('tbody');for(const h of headers)tr.append(element('th',h));head.append(tr);t.append(head,body);for(const row of rows){const tr=element('tr');for(const item of row){const td=element('td');if(item instanceof Node)td.append(item);else td.textContent=value(item);tr.append(td);}body.append(tr);}return t;}
  function drawChart(data){
    const canvas=byId('comparison-chart'),c=canvas.getContext('2d');c.fillStyle='#ffffff';c.fillRect(0,0,canvas.width,canvas.height);c.fillStyle='#20334f';c.font='bold 23px sans-serif';c.fillText(data.title,30,38,1130);c.font='16px sans-serif';c.fillText(scopes[data.scope]+' · 计划 '+(data.summary.fixed.planned*2)+' 组 / 已有运行 '+(data.summary.fixed.ran+data.summary.dynamic.ran)+' / 已执行 '+(data.summary.fixed.executed+data.summary.dynamic.executed)+'；未知费用不按零处理',30,68);
    const summaries=data.summary;
    const metrics=[['全计划成功率',s=>s.success_rate_all,n=>percent(n)],['已执行成功率',s=>s.success_rate_executed,n=>percent(n)],['候选轮次（合计）',s=>s.candidate_rounds,value],['真实HTTP请求（合计）',s=>s.requests,value],['人民币费用（合计）',s=>Number.isFinite(Number(s.cost.cny.total))&&s.cost.cny.total!=null?Number(s.cost.cny.total):null,value],['已结束任务耗时（秒，合计）',(_,strategy)=>{const rows=data.pairs.map(p=>p[strategy]).filter(r=>r.run_id);return rows.length&&rows.every(r=>r.elapsed_seconds!=null)?rows.reduce((sum,r)=>sum+r.elapsed_seconds,0):null;},n=>n==null?'未知':n.toFixed(2)]];
    metrics.forEach(([title,read,format],index)=>{const x=30+(index%2)*585,y=110+Math.floor(index/2)*175;c.fillStyle='#20334f';c.font='bold 17px sans-serif';c.fillText(title,x,y);const vals=Object.keys(names).map(s=>read(summaries[s],s)),maximum=Math.max(1,...vals.filter(n=>n!=null));Object.keys(names).forEach((strategy,i)=>{c.font='16px sans-serif';c.fillStyle='#42536c';c.fillText(names[strategy],x,y+35+i*50);c.fillStyle=i?'#4398ac':'#5277c2';if(vals[i]!=null)c.fillRect(x+90,y+19+i*50,300*vals[i]/maximum,24);c.fillStyle='#20334f';c.fillText(format(vals[i]),x+402,y+37+i*50);});});
    c.fillStyle='#42536c';c.font='14px monospace';c.fillText(data.comparison_id,30,665);c.font='15px sans-serif';c.fillText('不含自动启动或付费重跑；美元金额与未知项见逐题表 / JSON。B/C复核：待独立签收。',30,695);
  }
  function show(data){
    current=data;byId('comparison-results').hidden=false;display('comparison-title',data.title);display('comparison-basis',scopes[data.scope]+' · '+data.created_at+' · 只读证据快照，查看与导出不会启动任务。');
    byId('comparison-summary').replaceChildren(table(['策略','计划','已有运行','已执行','成功','执行未成功','阻塞','未运行','全计划成功率','已执行成功率'],Object.keys(names).map(s=>{const r=data.summary[s];return [names[s],r.planned,r.ran,r.executed,r.success,r.failed,r.blocked,r.not_run,percent(r.success_rate_all),percent(r.success_rate_executed)];})));
    const rows=[];for(const p of data.pairs)for(const strategy of Object.keys(names)){const r=p[strategy],links=element('div');if(r.run_id){const open=element('button','查看步骤记录','btn compact');open.type='button';open.onclick=()=>selectRun(r.run_id);links.append(open);for(const [url,title]of [[r.report_url,'报告'],[r.bundle_url,'ZIP'],[r.events_url,'原始事件']]){const a=element('a',title);a.href=url;a.target='_blank';a.rel='noopener';links.append(a);}}rows.push([p.project,names[strategy],r.run_id||'未运行',label(r.status),r.candidate_rounds,r.requests,r.cost.amount_cny,r.cost.amount_usd,r.cost.status,r.elapsed_seconds,r.reason||'',links]);}
    byId('comparison-runs').replaceChildren(table(['题目','策略','运行','状态','候选轮次','HTTP请求','CNY','USD','费用状态','秒','停止/移交原因','证据'],rows));display('comparison-json',data);drawChart(data);
    const actions=byId('comparison-downloads');actions.replaceChildren();for(const format of ['json','csv']){const a=element('a','导出 '+format.toUpperCase(),'btn');a.href='/api/v3/comparisons/'+data.comparison_id+'/export?format='+format;a.download=data.comparison_id+'.'+format;actions.append(a);}const png=element('button','导出 PNG 图表','btn');png.type='button';png.onclick=()=>{const a=document.createElement('a');a.href=byId('comparison-chart').toDataURL('image/png');a.download=data.comparison_id+'.png';a.click();};actions.append(png);
  }
  async function load(){
    if(loading)return;loading=true;try{const rows=await(await api('/comparisons')).json(),select=byId('comparison-record');select.replaceChildren(element('option','选择对照记录'));for(const r of rows){const o=element('option',r.title+' · '+scopes[r.scope]);o.value=r.comparison_id;select.append(o);}if(current)select.value=current.comparison_id;display('comparison-message',rows.length?'已读取对照记录；不会启动任务。':'尚无对照记录。请先完成运行或明确登记未运行项。');}catch(e){display('comparison-message',e.message);}finally{loading=false;}
  }
  async function init(){
    byId('comparison-refresh').onclick=load;byId('comparison-record').onchange=async e=>{if(!e.target.value)return;try{show(await(await api('/comparisons/'+e.target.value)).json());}catch(error){display('comparison-message',error.message);}};
    byId('comparison-create').onclick=async()=>{const b=byId('comparison-create');b.disabled=true;try{const plan=JSON.parse(byId('comparison-plan').value),data=await(await api('/comparisons',{method:'POST',body:JSON.stringify(plan)})).json();show(data);await load();display('comparison-message','已保存证据快照；未启动任何任务。');}catch(e){display('comparison-message','未保存：'+e.message);}finally{b.disabled=false;}};
    const create=byId('comparison-create');create.disabled=true;
    try{
      const registered=await(await api('/projects')).json(),projects=registered.map(p=>p.project).filter(p=>p.startsWith('qb-')).sort();
      if(projects.length!==40)throw new Error('登记题目不是预期的40道QuixBugs-Python，请核对正式范围。');
      if(!byId('comparison-plan').value.trim())byId('comparison-plan').value=JSON.stringify({title:'QuixBugs-Python 全量40题配对',scope:'formal',pairs:projects.map(project=>({project,fixed:null,dynamic:null,not_run_reason:{fixed:'待本批资格核对与同版本配对',dynamic:'待本批资格核对与同版本配对'}}))},null,2);
    }catch(e){display('comparison-message','默认清单未生成：'+e.message);}
    finally{create.disabled=false;}
    if(location.hash==='#comparisons')load();
  }
  return {init,load};
})();
CodeFixComparisons.init();
