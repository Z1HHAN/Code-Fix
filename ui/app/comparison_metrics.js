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
  const api={arms,names,totals};
  if(typeof module!=='undefined')module.exports=api;
  root.CodeFixComparisonMetrics=api;
})(typeof globalThis!=='undefined'?globalThis:this);
