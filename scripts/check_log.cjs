'use strict';
// Desktop checks can outlive the shell that launched them. Keep their logging
// independent of its stdout/stderr pipes, including Electron's console warnings.
const fs=require('node:fs');
const path=require('node:path');
const {format}=require('node:util');

function installFileLogging(filename){
  fs.mkdirSync(path.dirname(filename),{recursive:true});
  const previous=global.console;
  const write=chunk=>fs.appendFileSync(filename,chunk);
  const log=(...args)=>write(format(...args)+'\n');
  global.console={...previous,log,info:log,warn:log,error:log,debug:log};
  return {write,restore(){global.console=previous;}};
}
module.exports={installFileLogging};
