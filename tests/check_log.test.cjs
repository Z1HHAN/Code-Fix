'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawnSync}=require('node:child_process');

test('helper console and child output survive closed stdout and stderr',()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'codefix-check-log-'));
  const filename=path.join(dir,'check.log');
  try{
    const result=spawnSync(process.execPath,['-e',`
      const fs=require('node:fs');
      const log=require(${JSON.stringify(path.resolve(__dirname,'../scripts/check_log.cjs'))}).installFileLogging(${JSON.stringify(filename)});
      fs.closeSync(1);fs.closeSync(2);
      console.warn('Electron warning: %s','test');
      console.error('Expected check failure: %s','test');
      log.write(Buffer.from('child progress\\n'));
      log.restore();
    `],{encoding:'utf8',windowsHide:true});
    assert.equal(result.status,0,result.error?.message||result.stderr);
    assert.equal(fs.readFileSync(filename,'utf8'),'Electron warning: test\nExpected check failure: test\nchild progress\n');
  }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
