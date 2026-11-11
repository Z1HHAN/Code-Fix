'use strict';
const {app,BrowserWindow,Menu,ipcMain,safeStorage,dialog}=require('electron');
const fs=require('node:fs/promises'),path=require('node:path');
const {spawn}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const {validate,publicSettings}=require('./settings.cjs');
let window,child,backendURL,backendLoaded=false,quitting=false,current=null;
const smoke=process.argv.includes('--smoke-test');
function finishSmoke(code){quitting=true;if(child)child.kill();app.exit(code);}
if(smoke){
  app.setPath('userData',path.join(app.getPath('temp'),'codefix-desktop-smoke-'+process.pid));
  app.disableHardwareAcceleration();
}
const setupURL=pathToFileURL(path.join(__dirname,'setup.html')).href;
// Smoke profiles are disposable and must not hand off to an existing desktop
// instance. Normal users retain the single-instance behavior.
if(!smoke&&!app.requestSingleInstanceLock()){app.quit();}else{
app.on('second-instance',()=>{if(window){window.show();window.focus();}});
app.whenReady().then(async()=>{
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
  async function launch(settings){
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
      CODE_FIX_PERSONAL_MODE:'1',CODE_FIX_PERSONAL_BASE_URL:settings.base_url,CODE_FIX_PERSONAL_KEY:settings.api_key,
      CODE_FIX_PERSONAL_APPROVED:'1',CODE_FIX_LIVE_APPROVED:'1',CODE_FIX_EXECUTION_APPROVED:'1',
      CODE_FIX_EXECUTION_URL:settings.execution_url,CODE_FIX_EXECUTION_TOKEN:settings.execution_token,
      CODE_FIX_EXECUTION_SSH_TUNNEL:settings.ssh_tunnel?'1':'0',CODE_FIX_PERSONAL_TIMEOUT:'60',
      CODE_FIX_PERSONAL_BUDGET:settings.monetary_budget?'money':'usage',
      CODE_FIX_PERSONAL_DAILY_USD:settings.daily_usd||'',CODE_FIX_PERSONAL_INPUT_PRICE:settings.input_price||'',
      CODE_FIX_PERSONAL_OUTPUT_PRICE:settings.output_price||'',CODE_FIX_RUN_ROOT:path.join(app.getPath('userData'),'runs'),
      CODE_FIX_DESKTOP_SETTINGS:JSON.stringify({...publicSettings(settings),price_version:settings.price_version})});
    child=spawn(path.join(resources,'python','python.exe'),['-I','-B','-X','utf8',path.join(runtime,'desktop-launch.py')],
      {cwd:runtime,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    const launched=child;
    const port=await new Promise((resolve,reject)=>{
      let text='';const timer=setTimeout(()=>{launched.kill();reject(Error('本机服务启动超时'));},20000);
      launched.once('error',()=>{clearTimeout(timer);reject(Error('内置Python无法启动'));});
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
    if(!ready){launched.kill();throw Error('本机服务未就绪');}
    await window.loadURL(backendURL);
    backendLoaded=true;
    if(smoke)console.log('Desktop smoke: workbench loaded');
  }
  ipcMain.handle('personal:load',async(event)=>{trusted(event);try{current=await load();return {ok:true,settings:publicSettings(current)};}catch(e){return {ok:false,error:e.message};}});
  ipcMain.handle('personal:save',async(event,value)=>{
    trusted(event);
    try{
      if(await running())throw Error('任务正在运行，请先停止并等待结束后再修改配置');
      if(!safeStorage.isEncryptionAvailable())throw Error('当前系统不可用本机加密存储');
      const v=validate(value,current||{});
      await fs.mkdir(path.dirname(settingsPath),{recursive:true});
      const tmp=settingsPath+'.tmp';await fs.writeFile(tmp,safeStorage.encryptString(JSON.stringify(v)));await fs.rename(tmp,settingsPath);
      if(child){const old=child;child=null;await new Promise(resolve=>{old.once('exit',resolve);old.kill();setTimeout(resolve,3000);});}
      backendURL=null;backendLoaded=false;current=v;await launch(v);return {ok:true};
    }catch(e){if(smoke)console.error('Desktop smoke save failed: '+e.message);return {ok:false,error:e.message};}
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
  try{current=await load();if(current)await launch(current);else await window.loadURL(setupURL);}
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
        finishSmoke(0);return;
      }
      // Exercise the real preload/IPC, Windows encryption and bundled backend with
      // synthetic credentials. Saving must never contact a model or execution server.
      const fixture={base_url:'https://desktop-smoke.invalid/v1',model:'offline-smoke',api_key:'offline-smoke-key',
        execution_url:'http://127.0.0.1:17880',execution_token:'s'.repeat(48),ssh_tunnel:true,
        monetary_budget:false,max_output_tokens:2048,token_parameter:'max_tokens',approved:true};
      await window.webContents.executeJavaScript(`void window.desktopSettings.save(${JSON.stringify(fixture)}).then(r=>{if(!r.ok)document.title='SMOKE_SAVE_FAILED:'+r.error}).catch(()=>{}); true`);
      console.log('Desktop smoke: save dispatched');
      for(let i=0;i<300&&!backendLoaded;i++)await new Promise(r=>setTimeout(r,100));
      if(!backendLoaded)throw Error('Desktop saved-config launch failed: '+window.getTitle());
      const encrypted=await fs.readFile(settingsPath);
      if(encrypted.includes(Buffer.from(fixture.api_key))||encrypted.includes(Buffer.from(fixture.execution_token)))throw Error('Desktop secrets stored as plaintext');
      const restored=await load();
      if(restored.api_key!==fixture.api_key||restored.model!==fixture.model)throw Error('Desktop encrypted config reload failed');
      const tasks=await (await fetch(backendURL+'/api/v3/tasks')).json();
      if(tasks.length)throw Error('Desktop save unexpectedly created tasks');
      console.log('Desktop packaged smoke passed: first launch, real IPC save, encrypted reload, bundled backend and zero tasks. No model or cloud requests.');
      finishSmoke(0);
    }catch(error){console.error(error.message);finishSmoke(1);}
  }
});
app.on('window-all-closed',()=>app.quit());
}
