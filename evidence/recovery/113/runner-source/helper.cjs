const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {app,safeStorage}=require('electron');
const mode=process.argv.includes('--resume')?'resume':process.argv.includes('--execute')?'execute':'preflight';
const log=fs.createWriteStream(path.join(__dirname,mode==='resume'?'resume.log':mode==='execute'?'execution.log':'preflight-helper.log'),{flags:'a'});
app.setName('codefix-desktop');app.disableHardwareAcceleration();
app.whenReady().then(()=>{
  try{
    const settings=JSON.parse(safeStorage.decryptString(fs.readFileSync(path.join(app.getPath('appData'),'codefix-desktop/personal-settings.encrypted'))));
    const child=spawn('C:/Users/lenovo/Documents/ChatGPT/Code Fix/runs/v3-venv/Scripts/python.exe',
      ['-B','-X','utf8',path.join(__dirname,'run.py'),mode],
      {cwd:path.resolve(__dirname,'../..'),windowsHide:true,stdio:['pipe','pipe','pipe']});
    child.stdout.on('data',v=>{log.write(v);});
    child.stderr.on('data',()=>{});
    child.stdin.on('error',()=>{});
    child.stdin.end(JSON.stringify({settings,run_root:path.join(app.getPath('appData'),'codefix-desktop/runs')}));
    child.once('error',()=>app.exit(1));child.once('close',code=>{log.end(JSON.stringify({helper_exit:code})+'\n',()=>app.exit(code??1));});
    app.once('before-quit',()=>{if(child.exitCode===null)child.kill();});
  }catch(error){console.log(JSON.stringify({blocked:'saved_configuration_unavailable',error_type:error.name}));app.exit(1);}
});
