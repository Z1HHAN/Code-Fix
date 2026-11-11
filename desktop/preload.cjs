'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('desktopSettings',{
  load:()=>ipcRenderer.invoke('personal:load'),
  save:value=>ipcRenderer.invoke('personal:save',value)
});
