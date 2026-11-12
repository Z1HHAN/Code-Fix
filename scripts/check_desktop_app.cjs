'use strict';
// Run the actual packaged executable, requiring its success marker as well as
// exit code zero. All app smoke settings are synthetic; no external requests.
const {spawn}=require('node:child_process');
const path=require('node:path');
const fs=require('node:fs/promises');
async function run(executable,screenshot){
  const env={...process.env};delete env.CODE_FIX_SMOKE_SCREENSHOT;
  if(screenshot)env.CODE_FIX_SMOKE_SCREENSHOT=screenshot;
  await new Promise((resolve,reject)=>{
    const app=spawn(executable,['--smoke-test'],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';
    const timer=setTimeout(()=>{app.kill();app.stdout.destroy();app.stderr.destroy();reject(Error('Packaged desktop check timed out'));},180000);
    app.stdout.on('data',chunk=>{output=(output+chunk).slice(-65536);process.stdout.write(chunk);});
    app.stderr.on('data',chunk=>process.stderr.write(chunk));
    app.once('error',error=>{clearTimeout(timer);reject(error);});
    app.once('exit',code=>{
      clearTimeout(timer);
      app.stdout.destroy();app.stderr.destroy();
      const marker='encrypted reload, bundled backend and zero tasks';
      if(code===0&&output.includes(marker)&&(!screenshot||output.includes('Desktop first-launch capture passed')))resolve();
      else reject(Error('Packaged desktop check failed: exit='+code+', marker='+output.includes(marker)));
    });
  });
}
(async()=>{
  if(process.argv.length!==4)throw Error('Usage: node check_desktop_app.cjs <exe> <first-launch.png>');
  const executable=path.resolve(process.argv[2]),screenshot=path.resolve(process.argv[3]);
  await fs.mkdir(path.dirname(screenshot),{recursive:true});
  await run(executable,screenshot);
  if(!(await fs.stat(screenshot)).size)throw Error('First-launch screenshot is empty');
  console.log('Packaged desktop checks passed: screenshot and actual save/start. No model or cloud requests.');
})().catch(error=>{console.error(error.message);process.exitCode=1;});
