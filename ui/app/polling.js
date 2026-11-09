'use strict';
(function(root){
  function createPoller({refresh,isRunning,isHidden,schedule=setTimeout,cancel=clearTimeout}){
    let enabled=false,timer=null,inflight=null,pending=false,failures=0;
    const clear=()=>{if(timer!==null)cancel(timer);timer=null;};
    function arm(delay){clear();timer=schedule(()=>{timer=null;tick(false);},delay);}
    async function tick(force){
      clear();
      if(!enabled||(!force&&isHidden()))return;
      // User actions during an outstanding request need one fresh follow-up.
      if(inflight){pending=true;return inflight;}
      inflight=(async()=>{
        try{const ok=await refresh();failures=ok===false?Math.min(failures+1,6):0;}
        catch{failures=Math.min(failures+1,6);}
        finally{
          inflight=null;
          if(enabled&&!isHidden()){
            const delay=pending?0:Math.min(60000,(isRunning()?1200:15000)*2**failures);
            pending=false;arm(delay);
          }
        }
      })();
      return inflight;
    }
    return {
      start(){enabled=true;return tick(false);},
      request(){return tick(true);},
      visibilityChanged(){clear();if(isHidden()){pending=false;return;}return tick(true);},
      stop(){enabled=false;pending=false;clear();}
    };
  }
  if(typeof module==='object'&&module.exports)module.exports={createPoller};
  else root.CodeFixPolling={createPoller};
})(globalThis);
