'use strict';
(function(root){
  const nodeWidth=156,nodeHeight=88;
  const slots={entry:[230,35,'用户入口'],service:[620,35,'任务服务'],isolation:[230,195,'隔离前置检查'],program:[550,195,'独立程序调度'],guard:[900,195,'预算与权限'],locator:[550,345,'定位员·按需参与'],baseline:[180,470,'隔离基线测试'],repairer:[348,470,'修复与补丁校验'],pytest:[516,470,'隔离开发测试'],detector:[684,470,'检测员'],scheduler:[852,470,'模型调度员'],final:[1020,470,'最终复验'],trace:[230,640,'Trace事件'],logs:[470,640,'测试日志 / JUnit'],diff:[710,640,'候选diff'],cost:[950,640,'费用账本']};
  function slot(node){const f=node.facts;return f.node_id==='pytest'?(f.phase==='final_check'?'final':f.round===0?'baseline':'pytest'):f.node_id==='apply_candidate'?'repairer':f.node_id==='program_dispatch'?'program':f.node_id==='isolation_preflight'?'isolation':slots[f.node_id]?f.node_id:null;}
  function projection(run,events,trace){
    const graph=trace.project(run,events),mapped=new Map(),byStep=new Map();
    for(const node of graph.nodes){const key=slot(node);if(key){mapped.set(key,node);byStep.set(node.id,key);}}
    const edges=graph.edges.filter(e=>byStep.has(e.from)&&byStep.has(e.to)).map(e=>({...e,source:byStep.get(e.from),target:byStep.get(e.to)}));
    return {graph,mapped,edges,started:events.some(e=>e.event==='started'),terminal:!['created','running'].includes(run.status)};
  }
  function mount(document,host,trace,onSelect){
    const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
    const svg=tag=>document.createElementNS('http://www.w3.org/2000/svg',tag);
    let run=null,events=[],runId=null,seen=new Set(),selected=null,following=true,replaying=false,index=0,timer=null,transfers=new Map(),motionTimer=null,signature=null;
    const identity=e=>Number.isInteger(e.sequence)?'seq:'+e.sequence:JSON.stringify(e);
    const controls=el('div',null,'arch-controls'),state=el('span'),follow=el('button','返回当前步骤'),back=el('button','上一步'),next=el('button','下一步'),play=el('button','历史回放'),pause=el('button','暂停回放');
    for(const b of [follow,back,next,play,pause]){b.type='button';controls.append(b);}controls.prepend(state);
    const viewport=el('div',null,'arch-viewport'),canvas=el('div',null,'architecture-canvas'),steps=el('div',null,'arch-steps'),hint=el('p',null,'arch-hint');viewport.append(canvas);host.append(controls,viewport,steps,hint);
    function stop(){clearTimeout(timer);timer=null;replaying=false;}
    function historyAt(i){stop();following=false;index=Math.max(0,Math.min(events.length-1,i));transfers.clear();draw();}
    follow.onclick=()=>{stop();following=true;selected=null;transfers.clear();draw();};back.onclick=()=>historyAt((following?events.length-1:index)-1);next.onclick=()=>historyAt((following?events.length-1:index)+1);
    pause.onclick=()=>{stop();draw();};
    play.onclick=()=>{if(!run||!events.length)return;stop();following=false;replaying=true;index=0;transfers.clear();tick();};
    function tick(){if(!replaying)return;draw(true);if(index>=events.length-1){stop();draw();return;}timer=setTimeout(()=>{index++;tick();},900);}
    function draw(replayTick=false){
      if(!run)return;
      const visible=following?events:events.slice(0,index+1),finished=visible.some(e=>e.event==='finished');
      const viewRun=following||finished?run:{...run,status:visible.some(e=>e.event==='started')?'running':'created',result:null};
      const p=projection(viewRun,visible,trace);canvas.replaceChildren();steps.replaceChildren();
      state.textContent=following?'实时事件视图':replaying?'历史回放 · 仅播放已记录事件':'历史查看 · 不执行任务';
      state.setAttribute('role','status');follow.disabled=following;back.disabled=!events.length||(!following&&index===0);next.disabled=!events.length||following||index>=events.length-1;play.disabled=!events.length||replaying;pause.disabled=!replaying;
      hint.textContent=p.graph.provenance+'。默认原始字号，超出时可在图内横向滚动。分层为架构映射；执行连线仅据父步骤记录。灰色表示未执行或未记录，查看与回放不触发模型请求。';
      const links=svg('svg');links.setAttribute('class','arch-links');links.setAttribute('viewBox','0 0 1200 770');
      const defs=svg('defs'),marker=svg('marker');marker.setAttribute('id','arch-arrow');marker.setAttribute('viewBox','0 0 10 10');marker.setAttribute('refX','9');marker.setAttribute('refY','5');marker.setAttribute('markerWidth','5');marker.setAttribute('markerHeight','5');marker.setAttribute('orient','auto');const arrow=svg('path');arrow.setAttribute('d','M0 0L10 5L0 10Z');arrow.setAttribute('fill','#6b8cca');marker.append(arrow);defs.append(marker);links.append(defs);
      for(const [y,h,title]of [[15,130,'用户入口'],[175,130,'独立调度层'],[335,245,'Agent与工具'],[625,125,'证据与持久化']]){const band=el('div',null,'arch-band');band.style.cssText=`top:${y}px;height:${h}px`;band.append(el('strong',title));canvas.append(band);}
      function line(d,cls,attrs={}){
        const n=svg('path');n.setAttribute('d',d);n.setAttribute('class',cls);n.setAttribute('marker-end','url(#arch-arrow)');for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);links.append(n);
        if(cls==='arch-transmission'){
          // Move the arrow itself along the same directed path, including return routes.
          // Preserve elapsed time if another new event rebuilds this SVG during a handoff.
          const key=attrs['data-from']?attrs['data-from']+'>'+attrs['data-to']:attrs['data-evidence-step']?'evidence:'+attrs['data-evidence-step']:'entry';
          const elapsed=replayTick?0:Math.max(0,1300-((transfers.get(key)||Date.now()+1300)-Date.now()));
          for(let segment=0;segment<3;segment++){
          const begin=((segment*160-elapsed)/1000)+'s',packet=svg('g');packet.setAttribute('data-segment',String(segment));packet.setAttribute('class','arch-packet');packet.setAttribute('opacity','0');packet.setAttribute('aria-hidden','true');
          for(const [k,v]of Object.entries(attrs))packet.setAttribute(k,v);
          const shape=svg('path');shape.setAttribute('d','M-16 -2H-7V-5L0 0L-7 5V2H-16Z');shape.setAttribute('class','arch-packet-arrow');packet.append(shape);
          const motion=svg('animateMotion');motion.setAttribute('path',d);motion.setAttribute('rotate','auto');motion.setAttribute('dur','0.54s');motion.setAttribute('begin',begin);motion.setAttribute('repeatCount','1');motion.setAttribute('fill','freeze');motion.setAttribute('calcMode','paced');packet.append(motion);
          const visibility=svg('animate');visibility.setAttribute('attributeName','opacity');visibility.setAttribute('values','0;1;1;0');visibility.setAttribute('keyTimes','0;0.06;0.9;1');visibility.setAttribute('dur','0.54s');visibility.setAttribute('begin',begin);visibility.setAttribute('fill','freeze');packet.append(visibility);links.append(packet);
          }
        }
        return n;
      }
      // Structural downward backbone is deliberately distinct from step-to-step execution edges.
      for(const [a,b]of [[145,175],[305,335],[580,625]])line(`M90 ${a}V${b-3}`,'arch-structural');
      line('M386 79H617','arch-structural');
      const live=following&&run.status==='running';
      const animate=(live||replaying)&&!p.terminal;
      if(p.started){line('M698 123V158H308V192','arch-context');if(animate&&transfers.has('entry'))line('M698 123V158H308V192','arch-transmission');}
      const now=Date.now();for(const [k,t]of transfers)if(t<=now)transfers.delete(k);
      for(const edge of p.edges){
        const a=slots[edge.source],b=slots[edge.target];let d;
        if(edge.source===edge.target)d=`M${a[0]+nodeWidth} ${a[1]+24}h8v-35h-90v8`;
        else if(a[1]===b[1]&&b[0]>a[0])d=`M${a[0]+nodeWidth} ${a[1]+nodeHeight/2}H${b[0]-3}`;
        else if(b[1]<a[1])d=`M${a[0]+nodeWidth/2} ${a[1]+nodeHeight}V600H1188V${b[1]+nodeHeight+20}H${b[0]+nodeWidth/2}V${b[1]+nodeHeight+3}`;
        else {const mid=a[1]+112;d=`M${a[0]+nodeWidth/2} ${a[1]+nodeHeight}V${mid}H${b[0]+nodeWidth/2}V${b[1]-3}`;}
        line(d,'arch-visited',{'data-from':edge.from,'data-to':edge.to});
        const target=p.graph.nodes.find(n=>n.id===edge.to),key=edge.from+'>'+edge.to;
        const replayNew=replayTick&&target?.events.some(e=>e.event==='step_started'&&identity(e)===identity(visible.at(-1)));
        if(animate&&(transfers.has(key)||replayNew))line(d,'arch-transmission',{'data-from':edge.from,'data-to':edge.to});
      }
      // Evidence pulses require a newly recorded completion with an evidence path.
      const evidenceSlots=new Set();
      for(const node of p.graph.nodes){if(node.completed)evidenceSlots.add('trace');const paths=trace.artifacts(node,visible);if(paths.some(x=>/stdout|stderr|junit|structured/.test(x)))evidenceSlots.add('logs');if(node.facts.node_id==='apply_candidate'&&paths.length)evidenceSlots.add('diff');if(node.decision?.counters?.cost)evidenceSlots.add('cost');}
      if(animate)for(const node of p.graph.nodes){
        const key=slot(node);if(!key||!node.completed)continue;
        const replayComplete=replayTick&&visible.at(-1)?.event==='step_completed'&&visible.at(-1)?.step_id===node.id;
        if(!transfers.has('evidence:'+node.id)&&!replayComplete)continue;
        const a=slots[key];line(`M${a[0]+nodeWidth/2} ${a[1]+nodeHeight}V605H308V637`,'arch-transmission',{'data-evidence-step':node.id});
      }
      canvas.append(links);
      for(const [key,[x,y,title]]of Object.entries(slots)){
        const node=p.mapped.get(key),entryPulse=['entry','service'].includes(key)&&transfers.has('entry');
        const active=(node?.status==='running'||entryPulse)&&(live||replaying)&&!p.terminal;
        const failed=node&&(node.test?.verification_status==='failed'||['failed','blocked','timeout','inconclusive','interrupted','incomplete'].includes(node.status));
        const recorded=node||evidenceSlots.has(key)||(['entry','service'].includes(key)&&p.started);
        const button=el('button',null,'arch-node'+(active?' arch-active':failed?' arch-failed':recorded?' arch-done':''));button.type='button';button.style.cssText=`left:${x}px;top:${y}px`;button.dataset.module=key;button.setAttribute('aria-pressed',String(node?.id===selected));
        const status=node?(active?'执行中':node.statusLabel):key==='guard'?'每步强制校验（规则说明）':recorded?'已有事件记录':key==='locator'?'按需参与 · 未调用':'未执行 / 未记录';
        button.title=title+' · '+status;
        button.append(el('strong',title),el('span',status),el('small',node?.round!=null?'第'+node.round+'轮':''));
        button.disabled=!node;
        button.onclick=()=>{selected=node.id;following=false;index=events.findIndex(e=>identity(e)===identity(node.events.at(-1)));stop();transfers.clear();draw();};canvas.append(button);
      }
      for(const node of p.graph.nodes){const b=el('button',`${node.title}${node.round!=null?' · '+node.round+'轮':''} · ${node.statusLabel}`,'arch-step');b.type='button';b.setAttribute('aria-pressed',String(selected===node.id));b.onclick=()=>{selected=node.id;historyAt(events.findIndex(e=>identity(e)===identity(node.events.at(-1))));};steps.append(b);}
      if(!p.graph.nodes.length)steps.append(el('span',p.started?'已收到启动事件，等待步骤记录。':'尚未启动或暂无步骤证据。'));
      if(p.terminal)canvas.append(el('div','运行已结束 · '+(viewRun.result?.reason||viewRun.status),'arch-terminal'));
      const chosen=following||replaying?p.graph.nodes.at(-1):p.graph.nodes.find(n=>n.id===selected)||p.graph.nodes.at(-1);
      onSelect(chosen?.id,viewRun,visible);
    }
    return function render(value,page){
      const changed=runId!==value.run_id;
      const signatureNext=JSON.stringify([value.run_id,value.status,value.result,page]);if(signature===signatureNext)return;signature=signatureNext;
      if(changed){stop();clearTimeout(motionTimer);runId=value.run_id;seen=new Set(page.map(identity));transfers.clear();selected=null;following=true;}
      const fresh=changed?[]:page.filter(e=>!seen.has(identity(e)));
      run=value;events=page;for(const e of page)seen.add(identity(e));
      const p=projection(value,page,trace);
      if(following&&run.status==='running'){
        if(fresh.some(e=>e.event==='started'||(e.event==='step_started'&&!e.parent_step_id)))transfers.set('entry',Date.now()+1300);
        for(const edge of p.edges){if(fresh.some(e=>e.event==='step_started'&&e.step_id===edge.to))transfers.set(edge.from+'>'+edge.to,Date.now()+1300);}
        for(const e of fresh)if(e.event==='step_completed'&&e.step_id)transfers.set('evidence:'+e.step_id,Date.now()+1300);
      }
      if(run.status!=='running')transfers.clear();
      if(following||changed)draw();
      if(transfers.size){clearTimeout(motionTimer);motionTimer=setTimeout(()=>{transfers.clear();if(following)draw();},1350);}
    };
  }
  const api={projection,slot,mount};if(typeof module!=='undefined')module.exports=api;root.CodeFixArchitecture=api;
})(typeof globalThis!=='undefined'?globalThis:this);
