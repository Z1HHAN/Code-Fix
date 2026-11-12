'use strict';
(function(root){
  const nodeWidth=156,nodeHeight=88;
  const travelMs=2200,staggerMs=700,handoffMs=3800;
  const completionMs=1500,settleMs=300;
  // All routes below use orthogonal SVG commands. Count their actual travel,
  // including bends, rather than the distance between the node centres.
  function packetCount(d){
    const tokens=d.match(/[MLHVmlhv]|-?\d+(?:\.\d+)?/g);let x=0,y=0,length=0;
    for(let i=0;i<tokens.length;){
      const command=tokens[i++],relative=command===command.toLowerCase();let nx=x,ny=y;
      if(command.toUpperCase()==='M'||command.toUpperCase()==='L'){nx=Number(tokens[i++])+(relative?x:0);ny=Number(tokens[i++])+(relative?y:0);}
      else if(command.toUpperCase()==='H')nx=Number(tokens[i++])+(relative?x:0);
      else ny=Number(tokens[i++])+(relative?y:0);
      if(command.toUpperCase()!=='M')length+=Math.abs(nx-x)+Math.abs(ny-y);
      x=nx;y=ny;
    }
    return length<260?1:length<600?2:3;
  }
  const slots={entry:[230,35,'用户入口'],service:[620,35,'任务服务'],isolation:[230,195,'隔离前置检查'],program:[550,195,'独立程序调度'],guard:[900,195,'预算与权限'],locator:[550,345,'定位员·按需参与'],baseline:[180,470,'隔离基线测试'],repairer:[348,470,'修复与补丁校验'],pytest:[516,470,'隔离开发测试'],detector:[684,470,'检测员'],scheduler:[852,470,'模型调度员'],final:[1020,470,'最终复验'],trace:[230,640,'Trace事件'],logs:[470,640,'测试日志 / JUnit'],diff:[710,640,'候选diff'],cost:[950,640,'费用账本']};
  function slot(node){const f=node.facts;return ['budget_check','permission_check','configuration_check'].includes(f.node_id)?'guard':f.node_id==='pytest'?(f.phase==='final_check'?'final':f.round===0?'baseline':'pytest'):f.node_id==='apply_candidate'?'repairer':f.node_id==='program_dispatch'?'program':f.node_id==='isolation_preflight'?'isolation':slots[f.node_id]?f.node_id:null;}
  function projection(run,events,trace){
    const graph=trace.project(run,events),mapped=new Map(),byStep=new Map();
    for(const node of graph.nodes){const key=slot(node);if(key){mapped.set(key,node);byStep.set(node.id,key);}}
    const edges=graph.edges.filter(e=>byStep.has(e.from)&&byStep.has(e.to)).map(e=>({...e,source:byStep.get(e.from),target:byStep.get(e.to)}));
    return {graph,mapped,edges,started:events.some(e=>e.event==='started'),terminal:!['created','running'].includes(run.status)};
  }
  function evidenceTargets(node,events,trace){
    const targets=new Set(),paths=trace.artifacts(node,events);
    if(node.completed)targets.add('trace');
    if(paths.some(x=>/stdout|stderr|junit|structured/.test(x)))targets.add('logs');
    if(node.facts.node_id==='apply_candidate'&&paths.length)targets.add('diff');
    if(node.decision?.counters?.cost!=null)targets.add('cost');
    return targets;
  }
  function mount(document,host,trace,onSelect){
    const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
    const svg=tag=>document.createElementNS('http://www.w3.org/2000/svg',tag);
    let run=null,events=[],runId=null,seen=new Set(),selected=null,following=true,replaying=false,index=0,timer=null,transfers=new Map(),motionTimer=null,signature=null;
    let pausedAt=null,remaining=0,due=0,replayEnded=false,pendingEvents=0;
    let lastPaths=[],settlingPaths=[];
    const motionPreference=(document.defaultView||root).matchMedia?.('(prefers-reduced-motion: reduce)');
    const identity=e=>Number.isInteger(e.sequence)?'seq:'+e.sequence:JSON.stringify(e);
    const controls=el('div',null,'arch-controls'),state=el('span'),follow=el('button','返回当前步骤'),back=el('button','上一步'),next=el('button','下一步'),play=el('button','历史回放'),pause=el('button','暂停回放');
    const fromStep=el('button','从此步骤回放'),notice=el('span',null,'arch-new-events');notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
    follow.setAttribute('data-action','follow');
    for(const b of [follow,back,next,play,pause,fromStep]){b.type='button';controls.append(b);}controls.prepend(state);controls.append(notice);
    const viewport=el('div',null,'arch-viewport'),canvas=el('div',null,'architecture-canvas'),steps=el('div',null,'arch-steps'),hint=el('p',null,'arch-hint');viewport.append(canvas);host.append(controls,viewport,steps,hint);
    function stop(){clearTimeout(timer);timer=null;replaying=false;}
    function historyAt(i){stop();clearTimeout(motionTimer);settlingPaths=[];pausedAt=null;replayEnded=false;following=false;index=Math.max(0,Math.min(events.length-1,i));transfers.clear();draw();}
    follow.onclick=()=>{stop();clearTimeout(motionTimer);settlingPaths=[];pausedAt=null;replayEnded=false;pendingEvents=0;following=true;selected=null;transfers.clear();draw();};back.onclick=()=>historyAt((following?events.length-1:index)-1);next.onclick=()=>historyAt((following?events.length-1:index)+1);
    pause.onclick=()=>{if(!replaying)return;remaining=Math.max(0,due-Date.now());pausedAt=Date.now();stop();draw();};
    play.onclick=()=>{
      if(!run||!events.length||replaying)return;
      clearTimeout(motionTimer);
      if(pausedAt!==null){const shift=Date.now()-pausedAt;for(const [key,end]of transfers)transfers.set(key,end+shift);pausedAt=null;replaying=true;draw();schedule(remaining);return;}
      startReplay(0);
    };
    function startReplay(at){stop();clearTimeout(motionTimer);settlingPaths=[];pausedAt=null;pendingEvents=0;following=false;selected=null;replaying=true;replayEnded=false;index=at;transfers.clear();tick();}
    const selectedStart=()=>events.findIndex(e=>e.step_id===selected&&e.event==='step_started');
    fromStep.onclick=()=>{const at=selectedStart();if(at>=0&&!replaying)startReplay(at);};
    function updatePending(){notice.hidden=following||!pendingEvents;notice.textContent=notice.hidden?'':`新增 ${pendingEvents} 个事件，返回当前步骤查看`;}
    motionPreference?.addEventListener('change',()=>{if(run)draw();});
    function schedule(delay){due=Date.now()+delay;timer=setTimeout(()=>{if(index>=events.length-1){stop();transfers.clear();replayEnded=true;draw();}else{index++;tick();}},delay);}
    function tick(){
      if(!replaying)return;
      const visible=events.slice(0,index+1),event=visible.at(-1),p=projection(run,visible,trace),end=Date.now()+handoffMs;
      if(event.event==='started')transfers.set('entry',end);
      if(event.event==='step_started')for(const edge of p.edges)if(edge.to===event.step_id)transfers.set(edge.from+'>'+edge.to,end);
      if(event.event==='step_completed'&&p.graph.nodes.some(n=>n.id===event.step_id&&slot(n)))transfers.set('evidence:'+event.step_id,end);
      if(event.event==='finished')transfers.clear();
      draw();
      // Give recorded completion/evidence a readable beat; ordinary metadata and
      // entry still advance promptly while their packets retain elapsed travel.
      let delay=event.event==='step_started'&&p.edges.some(e=>e.to===event.step_id)?handoffMs:650;
      if(event.event==='step_completed'&&p.graph.nodes.some(n=>n.id===event.step_id&&slot(n)))delay=completionMs;
      if(index>=events.length-1)delay=Math.max(delay,...[...transfers.values()].map(t=>t-Date.now()));
      schedule(delay);
    }
    function draw(){
      if(!run)return;
      const visible=following?events:events.slice(0,index+1),finished=visible.some(e=>e.event==='finished');
      const viewRun=following||finished?run:{...run,status:visible.some(e=>e.event==='started')?'running':'created',result:null};
      const p=projection(viewRun,visible,trace),drawnPaths=[];canvas.replaceChildren();steps.replaceChildren();
      // Source describes the complete record, not the prefix at the playhead.
      // A real request does not by itself prove isolation or final acceptance.
      const replaySource={real_requests:'真实历史回放',simulation:'离线／模拟回放',baseline:'基线历史回放',unknown:'历史回放（来源未确认）'}[trace.provenanceKind(run,events)];
      state.textContent=following?(run.status==='running'?'实时事件视图':`${replaySource} · 记录查看 · 不执行任务`):replaying?`${replaySource} · ${index+1}/${events.length} · 仅播放已记录事件`:pausedAt!==null?`${replaySource} · 回放已暂停 · ${index+1}/${events.length}`:replayEnded?`${replaySource} · 回放结束 · 已播放全部已记录事件`:`${replaySource} · 历史查看 · 不执行任务`;
      play.textContent=pausedAt!==null?'继续回放':replayEnded?'重新回放':'历史回放';
      fromStep.disabled=replaying||selectedStart()<0;updatePending();
      state.setAttribute('role','status');follow.disabled=following;back.disabled=!events.length||(!following&&index===0);next.disabled=!events.length||following||index>=events.length-1;play.disabled=!events.length||replaying;pause.disabled=!replaying;
      hint.textContent=p.graph.provenance+'。默认原始字号，超出时可在图内横向滚动。分层为架构映射；执行连线仅据父步骤记录。灰色表示未执行或未记录，查看与回放不触发模型请求。';
      const links=svg('svg');links.setAttribute('class','arch-links');links.setAttribute('viewBox','0 0 1200 770');
      const defs=svg('defs'),marker=svg('marker');marker.setAttribute('id','arch-arrow');marker.setAttribute('viewBox','0 0 10 10');marker.setAttribute('refX','9');marker.setAttribute('refY','5');marker.setAttribute('markerWidth','5');marker.setAttribute('markerHeight','5');marker.setAttribute('orient','auto');const arrow=svg('path');arrow.setAttribute('d','M0 0L10 5L0 10Z');arrow.setAttribute('fill','#6b8cca');marker.append(arrow);defs.append(marker);links.append(defs);
      for(const [y,h,title]of [[15,130,'用户入口'],[175,130,'独立调度层'],[335,245,'Agent与工具'],[625,125,'证据与持久化']]){const band=el('div',null,'arch-band');band.style.cssText=`top:${y}px;height:${h}px`;band.append(el('strong',title));canvas.append(band);}
      function line(d,cls,attrs={}){
        const n=svg('path');n.setAttribute('d',d);n.setAttribute('class',cls);n.setAttribute('marker-end','url(#arch-arrow)');for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);links.append(n);
        if(cls==='arch-transmission')drawnPaths.push({d,attrs});
        if(cls==='arch-transmission'&&!motionPreference?.matches){
          // Move the arrow itself along the same directed path, including return routes.
          // Preserve elapsed time if another new event rebuilds this SVG during a handoff.
          const key=attrs['data-from']?attrs['data-from']+'>'+attrs['data-to']:attrs['data-evidence-step']?'evidence:'+attrs['data-evidence-step']:'entry';
          const elapsed=Math.max(0,handoffMs-((transfers.get(key)||Date.now()+handoffMs)-Date.now()));
          for(let segment=0;segment<packetCount(d);segment++){
          const begin=((segment*staggerMs-elapsed)/1000)+'s',packet=svg('g');packet.setAttribute('data-segment',String(segment));packet.setAttribute('class','arch-packet');packet.setAttribute('opacity','0');packet.setAttribute('aria-hidden','true');
          for(const [k,v]of Object.entries(attrs))packet.setAttribute(k,v);
          const shape=svg('path');shape.setAttribute('d','M-30 -3.5H-12V-9L0 0L-12 9V3.5H-30Z');shape.setAttribute('class','arch-packet-arrow');packet.append(shape);
          const motion=svg('animateMotion');motion.setAttribute('path',d);motion.setAttribute('rotate','auto');motion.setAttribute('dur',(travelMs/1000)+'s');motion.setAttribute('begin',begin);motion.setAttribute('repeatCount','1');motion.setAttribute('fill','freeze');motion.setAttribute('calcMode','paced');packet.append(motion);
          const visibility=svg('animate');visibility.setAttribute('attributeName','opacity');visibility.setAttribute('values','0;1;1;0');visibility.setAttribute('keyTimes','0;0.06;0.9;1');visibility.setAttribute('dur',(travelMs/1000)+'s');visibility.setAttribute('begin',begin);visibility.setAttribute('fill','freeze');packet.append(visibility);links.append(packet);
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
      const now=pausedAt??Date.now();for(const [k,t]of transfers)if(t<=now)transfers.delete(k);
      for(const edge of p.edges){
        const a=slots[edge.source],b=slots[edge.target];let d;
        if(edge.source===edge.target)d=`M${a[0]+nodeWidth} ${a[1]+24}h8v-35h-90v8`;
        else if(a[1]===b[1])d=`M${a[0]+nodeWidth/2} ${a[1]}V${a[1]-22}H${b[0]+nodeWidth/2}V${b[1]-3}`;
        else if(b[1]<a[1])d=`M${a[0]+nodeWidth/2} ${a[1]}V${a[1]-12}H${Math.max(a[0],b[0])+nodeWidth+18}V${b[1]+nodeHeight+12}H${b[0]+nodeWidth/2}V${b[1]+nodeHeight+3}`;
        else {const mid=a[1]+112;d=`M${a[0]+nodeWidth/2} ${a[1]+nodeHeight}V${mid}H${b[0]+nodeWidth/2}V${b[1]-3}`;}
        line(d,'arch-visited',{'data-from':edge.from,'data-to':edge.to});
        const key=edge.from+'>'+edge.to;
        if(animate&&transfers.has(key))line(d,'arch-transmission',{'data-from':edge.from,'data-to':edge.to});
      }
      // Evidence pulses require a newly recorded completion with an evidence path.
      const evidenceSlots=new Set();
      for(const node of p.graph.nodes)for(const target of evidenceTargets(node,visible,trace))evidenceSlots.add(target);
      if(animate)for(const node of p.graph.nodes){
        const key=slot(node);if(!key||!node.completed)continue;
        if(!transfers.has('evidence:'+node.id))continue;
        const a=slots[key];for(const target of evidenceTargets(node,visible,trace)){
          const b=slots[target];line(`M${a[0]+nodeWidth/2} ${a[1]+nodeHeight}V605H${b[0]+nodeWidth/2}V${b[1]-3}`,'arch-transmission',{'data-evidence-step':node.id,'data-evidence-target':target});
        }
      }
      // A terminal task has no moving packets or active nodes. Only the existing
      // connection highlights fade out; stop/interruption never enters this path.
      if(p.terminal&&!motionPreference?.matches)for(const {d,attrs}of settlingPaths)line(d,'arch-settling',attrs);
      lastPaths=drawnPaths;
      canvas.append(links);
      for(const [key,[x,y,title]]of Object.entries(slots)){
        const node=p.mapped.get(key),entryPulse=['entry','service'].includes(key)&&transfers.has('entry');
        const active=(node?.status==='running'||entryPulse)&&(live||replaying)&&!p.terminal;
        const failed=node&&(node.test?.verification_status==='failed'||['failed','blocked','timeout','inconclusive','interrupted','incomplete'].includes(node.status));
        const recorded=node||evidenceSlots.has(key)||(['entry','service'].includes(key)&&p.started);
        const button=el('button',null,'arch-node'+(active?' arch-active':failed?' arch-failed':recorded?' arch-done':''));button.type='button';button.style.cssText=`left:${x}px;top:${y}px`;button.dataset.module=key;button.setAttribute('aria-pressed',String(node?.id===selected));
        const status=node?(active?'执行中':node.statusLabel):key==='guard'?'每步强制校验（规则说明）':recorded?'已有事件记录':key==='locator'?'按需参与 · 未调用':'未执行 / 未记录';
        button.title=title+' · '+status;
        button.append(el('strong',title),el('span',status),el('small',node?trace.roundLabel(node):''));
        button.disabled=!node;
        button.onclick=()=>{selected=node.id;historyAt(events.findIndex(e=>identity(e)===identity(node.events.at(-1))));};canvas.append(button);
      }
      for(const node of p.graph.nodes){const b=el('button',`${node.title}${trace.roundLabel(node)?' · '+trace.roundLabel(node):''} · ${node.statusLabel}`,'arch-step');b.type='button';b.setAttribute('aria-pressed',String(selected===node.id));b.onclick=()=>{selected=node.id;historyAt(events.findIndex(e=>identity(e)===identity(node.events.at(-1))));};steps.append(b);}
      if(!p.graph.nodes.length)steps.append(el('span',p.started?'已收到启动事件，等待步骤记录。':'尚未启动或暂无步骤证据。'));
      if(p.terminal)canvas.append(el('div','运行已结束 · '+(viewRun.result?.reason||viewRun.status),'arch-terminal'));
      const chosen=following||replaying?p.graph.nodes.at(-1):p.graph.nodes.find(n=>n.id===selected)||p.graph.nodes.at(-1);
      onSelect(chosen?.id,viewRun,visible);
    }
    return function render(value,page){
      const changed=runId!==value.run_id;
      const signatureNext=JSON.stringify([value.run_id,value.status,value.result,page]);if(signature===signatureNext)return;signature=signatureNext;
      if(changed){stop();pausedAt=null;replayEnded=false;pendingEvents=0;clearTimeout(motionTimer);lastPaths=[];settlingPaths=[];runId=value.run_id;seen=new Set(page.map(identity));transfers.clear();selected=null;following=true;}
      const fresh=changed?[]:page.filter(e=>!seen.has(identity(e)));
      const settle=following&&!changed&&run?.status==='running'&&['success','failed'].includes(value.status)&&page.some(e=>e.event==='finished')&&transfers.size>0&&!motionPreference?.matches;
      if(settle)settlingPaths=lastPaths;
      run=value;events=page;for(const e of page)seen.add(identity(e));
      if(!following&&fresh.length){pendingEvents+=fresh.length;updatePending();}
      const p=projection(value,page,trace);
      if(following&&run.status==='running'){
        if(fresh.some(e=>e.event==='started'||(e.event==='step_started'&&!e.parent_step_id)))transfers.set('entry',Date.now()+handoffMs);
        for(const edge of p.edges){if(fresh.some(e=>e.event==='step_started'&&e.step_id===edge.to))transfers.set(edge.from+'>'+edge.to,Date.now()+handoffMs);}
        for(const e of fresh)if(e.event==='step_completed'&&e.step_id)transfers.set('evidence:'+e.step_id,Date.now()+handoffMs);
      }
      if(following&&run.status!=='running'){
        transfers.clear();
        if(!settlingPaths.length)clearTimeout(motionTimer);
        if(!['success','failed'].includes(run.status)){clearTimeout(motionTimer);settlingPaths=[];}
      }
      if(following||changed)draw();
      if(settle){clearTimeout(motionTimer);motionTimer=setTimeout(()=>{settlingPaths=[];if(following)draw();},settleMs);}
      if(following&&transfers.size){clearTimeout(motionTimer);motionTimer=setTimeout(()=>{transfers.clear();if(following)draw();},handoffMs+50);}
    };
  }
  const api={projection,slot,mount,packetCount};if(typeof module!=='undefined')module.exports=api;root.CodeFixArchitecture=api;
})(typeof globalThis!=='undefined'?globalThis:this);
