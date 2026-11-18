'use strict';
const {app,BrowserWindow,Menu,ipcMain,safeStorage,dialog}=require('electron');
const fs=require('node:fs/promises'),path=require('node:path');
const {spawn}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const {validate,publicSettings,checkSettings}=require('./settings.cjs');
let window,child,backendURL,backendLoaded=false,quitting=false,current=null;
const smoke=process.argv.includes('--smoke-test');
function modelAuthorizationEnvironment(){
  const env={};
  for(const key of ['CODE_FIX_PERSONAL_AUTHORIZATION_ID','CODE_FIX_PERSONAL_AUTHORIZATION_LIMIT']){
    if(process.env[key])env[key]=process.env[key];
  }
  return env;
}
function finishSmoke(code){quitting=true;if(child)child.kill();app.exit(code);}
if(smoke){
  console.log('Desktop smoke: disposable process starting');
  app.setPath('userData',path.join(app.getPath('temp'),'codefix-desktop-smoke-'+process.pid));
  app.disableHardwareAcceleration();
}
const setupURL=pathToFileURL(path.join(__dirname,'setup.html')).href;
// Smoke profiles are disposable and must not hand off to an existing desktop
// instance. Normal users retain the single-instance behavior.
if(!smoke&&!app.requestSingleInstanceLock()){app.quit();}else{
app.on('second-instance',()=>{if(window){window.show();window.focus();}});
app.whenReady().then(async()=>{
  if(smoke)console.log('Desktop smoke: Electron ready');
  window=new BrowserWindow({width:1440,height:960,minWidth:900,minHeight:650,show:!smoke,
    webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,
      backgroundThrottling:!smoke}});
  window.on('close',event=>{if(!quitting){event.preventDefault();app.quit();}});
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  window.webContents.on('will-navigate',(e,url)=>{if(url!==setupURL&&(!backendURL||new URL(url).origin!==backendURL))e.preventDefault();});
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {label:'应用',submenu:[{label:'模型与云端连接设置',click:()=>window.loadURL(setupURL)},
      {label:'返回工作台',click:()=>{if(backendURL)window.loadURL(backendURL);}},{role:'quit',label:'退出'}]},
    {label:'查看',submenu:[{role:'reload',label:'刷新'},{role:'resetZoom'},{role:'zoomIn'},{role:'zoomOut'}]}
  ]));
  const settingsPath=path.join(app.getPath('userData'),'personal-settings.encrypted');
  function trusted(event){if(event.senderFrame?.url!==setupURL)throw Error('仅本机设置页面可修改连接');}
  async function load(){
    try{return JSON.parse(safeStorage.decryptString(await fs.readFile(settingsPath)));}
    catch(e){if(e.code==='ENOENT')return null;throw Error('本机配置无法解密，请在当前Windows用户下重新配置');}
  }
  async function running(){
    if(!backendURL||!child||child.exitCode!==null)return false;
    try{const r=await fetch(backendURL+'/api/v3/tasks?include_archived=true',{signal:AbortSignal.timeout(3000)});
      if(!r.ok)throw Error();const tasks=await r.json();return tasks.some(t=>t.status==='running');}
    catch{throw Error('无法确认任务状态，暂不重启后端；请稍后重试');}
  }
  let checkBusy=false,lastExecution=null;
  async function check(mode,value){
    if(checkBusy)throw Error('检查正在进行，请稍候');
    if(await running())throw Error('任务正在运行，结束后再测试连接');
    const v=checkSettings(value,current||{},mode);
    checkBusy=true;
    try{
      // Packaged smoke uses only synthetic credentials and never external services.
      if(smoke&&mode!=='prices'){
        const fixture={ready:true,models:mode==='models'?['offline-smoke','offline-second']:[],
          isolation_verified:['all','execution'].includes(mode),checked_at:new Date().toISOString(),
          checks:[{id:mode,title:'离线检查夹具',status:'pass',detail:'仅验证桌面IPC和展示，不代表真实连接'}]};
        if(['all','execution'].includes(mode))lastExecution=fixture;return fixture;
      }
      const resources=app.isPackaged?process.resourcesPath:path.join(__dirname,'build');
      const env={};for(const key of ['SystemRoot','WINDIR','PATH','TEMP','TMP','USERPROFILE'])if(process.env[key])env[key]=process.env[key];
      if(mode==='test_model')Object.assign(env,modelAuthorizationEnvironment());
      env.CODE_FIX_RUN_ROOT=path.join(app.getPath('userData'),'runs');
      const result=await new Promise((resolve,reject)=>{
        const proc=spawn(path.join(resources,'python','python.exe'),['-I','-B','-X','utf8',path.join(resources,'backend','desktop-check.py')],
          {env,windowsHide:true,stdio:['pipe','pipe','pipe']});
        let buffer='',result=null;
        proc.stdout.setEncoding('utf8');
        const timer=setTimeout(()=>{proc.kill();reject(Error('连接检查超时，请重试；模型测试不会自动重发'));},90000);
        proc.on('error',()=>{clearTimeout(timer);reject(Error('内置连接检查程序无法启动'));});
        proc.stdin.on('error',()=>{});
        proc.stderr.on('data',()=>{}); // Never forward credentials or provider bodies to logs.
        proc.stdout.on('data',chunk=>{
          buffer+=chunk.toString();if(buffer.length>1048576){proc.kill();return;}
          let end;while((end=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,end);buffer=buffer.slice(end+1);
            try{const data=JSON.parse(line);if(data.check_result)result=data.check_result;
              if(data.check_progress&&window.webContents.getURL()===setupURL)window.webContents.send('personal:progress',data.check_progress);}catch{}
          }
        });
        proc.on('close',()=>{clearTimeout(timer);result?resolve(result):reject(Error('连接检查失败，请检查安装文件后重试'));});
        proc.stdin.end(JSON.stringify({...v,check_mode:mode}));
      });
      if(mode==='all'||mode==='execution')lastExecution=result;
      return result;
    }finally{checkBusy=false;}
  }
  ipcMain.handle('personal:check',async(event,mode,value)=>{
    trusted(event);try{return {ok:true,result:await check(mode,value)};}catch(e){return {ok:false,error:e.message};}
  });
  async function launch(settings){
    if(window.webContents.getURL()===setupURL)window.webContents.send('personal:progress',{
      id:'runtime',title:'本机应用运行条件',status:'checking',detail:'正在启动内置Python；首次冷启动可能较慢，最多等待90秒'});
    const resources=app.isPackaged?process.resourcesPath:path.join(__dirname,'build');
    const template=path.join(resources,'backend');
    const build=JSON.parse(await fs.readFile(path.join(template,'desktop-build.json'),'utf8'));
    const runtime=path.join(app.getPath('userData'),'runtime',build.source_sha256);
    // Fresh per-build tree: personal generated config never modifies installed files.
    await fs.mkdir(runtime,{recursive:true});
    await fs.cp(template,runtime,{recursive:true,force:true});
    if(smoke)console.log('Desktop smoke: backend assets copied');
    const env={};
    for(const key of ['SystemRoot','SYSTEMROOT','WINDIR','COMSPEC','SYSTEMDRIVE','PATH','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA'])if(process.env[key])env[key]=process.env[key];
    Object.assign(env,{PYTHONUTF8:'1',PYTHONNOUSERSITE:'1',PYTHONDONTWRITEBYTECODE:'1',
      ...modelAuthorizationEnvironment(),
      CODE_FIX_PERSONAL_MODE:'1',CODE_FIX_PERSONAL_BASE_URL:settings.base_url,CODE_FIX_PERSONAL_KEY:settings.api_key,
      CODE_FIX_PERSONAL_APPROVED:'1',CODE_FIX_LIVE_APPROVED:'1',CODE_FIX_EXECUTION_APPROVED:'1',
      CODE_FIX_EXECUTION_URL:settings.execution_url,CODE_FIX_EXECUTION_TOKEN:settings.execution_token,
      CODE_FIX_EXECUTION_SSH_TUNNEL:settings.ssh_tunnel?'1':'0',CODE_FIX_PERSONAL_TIMEOUT:'60',
      CODE_FIX_PERSONAL_BUDGET:settings.monetary_budget?'money':'usage',
      CODE_FIX_PERSONAL_DAILY_USD:settings.daily_usd||'',CODE_FIX_PERSONAL_INPUT_PRICE:settings.input_price||'',
      CODE_FIX_PERSONAL_DAILY_LIMIT:settings.daily_limit||'',
      CODE_FIX_PERSONAL_OUTPUT_PRICE:settings.output_price||'',CODE_FIX_RUN_ROOT:path.join(app.getPath('userData'),'runs'),
      CODE_FIX_CONNECTION_CHECK:JSON.stringify(lastExecution||{}),
      CODE_FIX_DESKTOP_SETTINGS:JSON.stringify({...publicSettings(settings),price_version:settings.price_version})});
    child=spawn(path.join(resources,'python','python.exe'),['-I','-B','-X','utf8',...(smoke?['-X','importtime']:[]),path.join(runtime,'desktop-launch.py')],
      {cwd:runtime,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    const launched=child;
    const port=await new Promise((resolve,reject)=>{
      let text='';const timer=setTimeout(()=>{launched.kill();reject(Error('本机服务启动超时（90秒），请重试并检查应用是否被安全软件拦截'));},90000);
      launched.once('error',()=>{clearTimeout(timer);reject(Error('内置Python环境无法启动，请修复或重新安装应用'));});
      launched.once('exit',(code,signal)=>{if(smoke)console.error('Desktop smoke child exit: '+code+' / '+signal);clearTimeout(timer);reject(Error('本机服务启动失败，请检查安装文件'));});
      launched.stdout.on('data',chunk=>{text+=chunk.toString();if(text.length>65536)text=text.slice(-65536);
        for(const line of text.split('\n'))try{const v=JSON.parse(line);if(Number.isInteger(v.desktop_port)&&v.desktop_port>0&&v.desktop_port<65536){clearTimeout(timer);resolve(v.desktop_port);}}catch{}
      });
      launched.stderr.on('data',chunk=>{if(smoke)console.error(chunk.toString());}); // Smoke uses synthetic credentials only; normal startup never logs child output.
    });
    backendURL='http://127.0.0.1:'+port;
    if(smoke)console.log('Desktop smoke: backend announced port');
    let ready=false;
    for(let i=0;i<80;i++){
      try{const r=await fetch(backendURL+'/api/v3/session',{signal:AbortSignal.timeout(500)});if(r.ok){ready=true;break;}}catch{}
      await new Promise(r=>setTimeout(r,100));
    }
    if(!ready){launched.kill();throw Error('本机服务启动后未响应，请重新检查并重试');}
    let health;
    try{const r=await fetch(backendURL+'/api/v3/health',{signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error();health=await r.json();}
    catch{launched.kill();backendURL=null;throw Error('本机服务已启动，但健康检查未响应，请保留配置后重试');}
    const row={id:'runtime',title:'本机应用运行条件',status:health.blockers?.length?'blocked':'pass',
      detail:health.blockers?.join('；')||'后端已启动，模型配置已解析，远程执行连接已载入',
      action:health.blockers?.length?'检查上述配置后点击保存并重新检查':''};
    if(window.webContents.getURL()===setupURL)window.webContents.send('personal:progress',row);
    if(health.blockers?.length){launched.kill();backendURL=null;throw Error('本机应用运行条件未通过，请查看上面的逐项原因');}
    await window.loadURL(backendURL);
    backendLoaded=true;
    if(smoke)console.log('Desktop smoke: workbench loaded');
  }
  ipcMain.handle('personal:load',async(event)=>{trusted(event);try{current=await load();return {ok:true,settings:publicSettings(current),checks:lastExecution};}catch(e){return {ok:false,error:e.message};}});
  ipcMain.handle('personal:save',async(event,value)=>{
    trusted(event);
    try{
      if(await running())throw Error('任务正在运行，请先停止并等待结束后再修改配置');
      if(!safeStorage.isEncryptionAvailable())throw Error('当前系统不可用本机加密存储');
      const v=validate(value,current||{});
      await fs.mkdir(path.dirname(settingsPath),{recursive:true});
      const tmp=settingsPath+'.tmp';await fs.writeFile(tmp,safeStorage.encryptString(JSON.stringify(v)));await fs.rename(tmp,settingsPath);
      if(child){const old=child;child=null;await new Promise(resolve=>{old.once('exit',resolve);old.kill();setTimeout(resolve,3000);});}
      backendURL=null;backendLoaded=false;current=v;
      const checked=await check('all',value);
      if(!checked.ready)return {ok:false,saved:true,result:checked,error:'配置已加密保存；以下条件未通过，请按说明处理后重新检查。没有启动修复或调用模型。'};
      await launch(v);return {ok:true};
    }catch(e){if(smoke)console.error('Desktop smoke save failed: '+e.message);return {ok:false,error:e.message,
      result:{checks:[{id:'launch',title:'保存或启动',status:'blocked',detail:e.message,action:'保留当前设置，修正原因后点击保存并重新检查；不会自动启动修复'}]}};}
  });
  app.on('before-quit',e=>{
    if(quitting)return;
    if(!child){quitting=true;return;}
    e.preventDefault();
    (async()=>{let active=true;try{active=await running();}catch{}
      if(active){const answer=await dialog.showMessageBox(window,{type:'question',buttons:['返回应用','退出'],defaultId:0,cancelId:0,
        message:'任务可能仍在运行。退出将中断本机编排，云端任务按原时限停止，不会自动重跑。'});if(answer.response!==1)return;}
      quitting=true;child.kill();app.quit();
    })();
  });
  try{current=await load();if(smoke)console.log('Desktop smoke: loading settings');await window.loadURL(setupURL);if(smoke)console.log('Desktop smoke: settings loaded');}
  catch(e){if(smoke){console.error(e.message);finishSmoke(1);return;}await window.loadURL(setupURL);dialog.showErrorBox('启动提示',e.message);}
  if(smoke){
    try{
      const state=await window.webContents.executeJavaScript('({title:document.title,passwords:document.querySelectorAll("input[type=password]").length,node:typeof require})');
      if(state.passwords!==2||state.node!=='undefined'||!safeStorage.isEncryptionAvailable())throw Error('Desktop first-launch state: '+JSON.stringify({...state,encryption:safeStorage.isEncryptionAvailable()}));
      if(process.env.CODE_FIX_SMOKE_SCREENSHOT){
        let capture;
        for(let attempt=0;attempt<10;attempt++){
          await new Promise(r=>setTimeout(r,200));
          try{capture=await window.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});if(!capture.isEmpty())break;}catch(error){if(attempt===9)throw error;}
        }
        if(!capture||capture.isEmpty())throw Error('Desktop first-launch capture has no frame');
        await fs.writeFile(process.env.CODE_FIX_SMOKE_SCREENSHOT,capture.toPNG());
        console.log('Desktop first-launch capture passed; no configuration or requests.');
        // Continue in this same disposable process. Reopening Electron directly
        // after exit can stall Windows process creation during Chromium cleanup.
      }
      // Exercise the real preload/IPC, Windows encryption and bundled backend with
      // synthetic credentials. Saving must never contact a model or execution server.
      const fixture={base_url:'https://desktop-smoke.invalid/v1',model:'offline-smoke',api_key:'offline-smoke-key',
        execution_url:'http://127.0.0.1:17880',execution_token:'s'.repeat(48),ssh_tunnel:true,
        monetary_budget:false,max_output_tokens:2048,token_parameter:'max_tokens',approved:true};
      const controls=await window.webContents.executeJavaScript(`({models:!!document.getElementById('read-models'),test:!!document.getElementById('test-model'),probe:!!document.getElementById('check-execution')})`);
      if(!controls.models||!controls.test||!controls.probe)throw Error('Desktop connection controls missing');
      const buttonFlow=await window.webContents.executeJavaScript(`(async()=>{
        const fixture=${JSON.stringify(fixture)};
        for(const [id,value] of Object.entries(fixture)){const e=document.getElementById(id);if(e){if(e.type==='checkbox')e.checked=value;else e.value=value;}}
        for(const id of ['read-models','test-model','check-execution']){
          document.getElementById(id).click();
          for(let i=0;i<200&&document.getElementById(id).disabled;i++)await new Promise(r=>setTimeout(r,25));
          if(document.getElementById(id).disabled||!document.getElementById('checks').textContent.includes('离线检查夹具'))throw Error('Button check failed: '+id);
          if(id==='read-models'&&document.getElementById('model-options').children.length!==2)throw Error('Model choices not rendered');
        }return true;
      })()`);
      if(!buttonFlow)throw Error('Desktop connection button flow failed');
      const priceFlow=await window.webContents.executeJavaScript(`(async()=>{
        document.getElementById('price-text').value=JSON.stringify({provider:'manual',model:'offline-smoke',region:'fixture',mode:'standard',currency:'CNY',unit:'per_million_tokens',source_url:'https://example.com/prices',verified_at:'2026-11-11',endpoint_host:'desktop-smoke.invalid',tiers:[{max_input_tokens:1000000,input:'2',output:'8'}]});
        document.getElementById('preview-prices').click();
        for(let i=0;i<1600&&document.getElementById('preview-prices').disabled;i++)await new Promise(r=>setTimeout(r,25));
        if(document.getElementById('confirm-prices').disabled)throw Error('Price preview failed');
        document.getElementById('confirm-prices').click();
        if(!document.getElementById('monetary_budget').checked||document.getElementById('budget_currency').value!=='CNY')throw Error('Price confirmation failed');
        return true;
      })()`);
      if(!priceFlow)throw Error('Desktop price UI failed');
      console.log('Desktop price preview and confirmation passed with synthetic prices.');
      const discovery=await window.webContents.executeJavaScript(`window.desktopSettings.check('models',${JSON.stringify(fixture)})`);
      if(!discovery.ok||discovery.result.models.length!==2)throw Error('Desktop model discovery IPC failed');
      const noConsent=await window.webContents.executeJavaScript(`window.desktopSettings.check('test_model',${JSON.stringify({...fixture,approved:false})})`);
      if(noConsent.ok)throw Error('Desktop model test accepted without consent');
      await window.webContents.executeJavaScript(`void window.desktopSettings.save(value()).then(r=>{if(!r.ok)document.title='SMOKE_SAVE_FAILED:'+r.error}).catch(()=>{}); true`);
      console.log('Desktop smoke: save dispatched');
      for(let i=0;i<1500&&!backendLoaded;i++)await new Promise(r=>setTimeout(r,100));
      if(!backendLoaded)throw Error('Desktop saved-config launch failed: '+window.getTitle());
      const encrypted=await fs.readFile(settingsPath);
      if(encrypted.includes(Buffer.from(fixture.api_key))||encrypted.includes(Buffer.from(fixture.execution_token)))throw Error('Desktop secrets stored as plaintext');
      const restored=await load();
      if(restored.api_key!==fixture.api_key||restored.model!==fixture.model||!restored.model_prices?.['offline-smoke']||restored.budget_currency!=='CNY')throw Error('Desktop encrypted config reload failed');
      const tasks=await (await fetch(backendURL+'/api/v3/tasks')).json();
      if(tasks.length)throw Error('Desktop save unexpectedly created tasks');
      console.log('Desktop packaged smoke passed: first launch, real IPC save, encrypted reload, bundled backend and zero tasks. No model or cloud requests.');
      finishSmoke(0);
    }catch(error){console.error(error.message);finishSmoke(1);}
  }
});
app.on('window-all-closed',()=>app.quit());
}
