'use strict';
(function(root){
  function arms(data){return data.kind==='agents'?['single','dynamic']:['fixed','dynamic'];}
  function names(data){return data.kind==='agents'?{single:'单Agent · 修复员',dynamic:'多Agent · 证据协作'}:{fixed:'固定顺序',dynamic:'动态编排'};}
  function totals(data,arm){
    const rows=data.pairs.map(p=>p[arm]).filter(r=>r.run_id);
    return {elapsed:rows.length&&rows.every(r=>r.elapsed_seconds!=null)?rows.reduce((a,r)=>a+r.elapsed_seconds,0):null,
      cost:data.summary[arm].cost.cny.total,
      unpaired:data.pairs.filter(p=>!p.paired).length};
  }
  function preferred(rows,kind,current){
    const visible=rows.filter(r=>(r.kind||'strategies')===kind);
    if(visible.some(r=>r.comparison_id===current))return current;
    visible.sort((a,b)=>{
      const rank=r=>(r.scope==='formal'?1000000:0)+(r.planned>0&&r.ran===r.planned?100000:0)+(r.ran||0);
      return rank(b)-rank(a)||String(b.created_at).localeCompare(String(a.created_at));
    });
    return visible[0]?.comparison_id||'';
  }
  function statistics(data,arm,extra){
    const sum=data.summary[arm],t=totals(data,arm),n=sum.ran??data.pairs.filter(p=>p[arm].run_id).length;
    const h=extra?.comparison_id===data.comparison_id?extra.groups?.[arm]:null;
    return {...t,averageCalls:n&&Number.isFinite(sum.requests)?sum.requests/n:null,averageSeconds:n&&t.elapsed!=null?t.elapsed/n:null,
      handoffRate:h?.handoff_rate??null,handoffs:h?.handoff_packages??null};
  }
  const api={arms,names,totals,preferred,statistics};
  if(typeof module!=='undefined')module.exports=api;
  root.CodeFixComparisonMetrics=api;
})(typeof globalThis!=='undefined'?globalThis:this);
