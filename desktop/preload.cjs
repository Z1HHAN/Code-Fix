'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('desktopSettings',{
  load:()=>ipcRenderer.invoke('personal:load'),
  save:value=>ipcRenderer.invoke('personal:save',value),
  check:(mode,value)=>ipcRenderer.invoke('personal:check',mode,value),
  onProgress:callback=>{const listener=(_event,row)=>callback(row);ipcRenderer.on('personal:progress',listener);return ()=>ipcRenderer.removeListener('personal:progress',listener);}
});
