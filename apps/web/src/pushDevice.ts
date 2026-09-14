import {api,ApiError} from './api';
export type PushConfig={enabled:boolean;public_key:string};
export type DeviceMeta={owner:string;id:string;key:string;label:string};
export function deviceMeta():DeviceMeta|null {try{return JSON.parse(localStorage.getItem('cf-push-device')||'null');}catch{return null;}}
function storeDevice(value:DeviceMeta|null){try{if(value)localStorage.setItem('cf-push-device',JSON.stringify(value));else localStorage.removeItem('cf-push-device');}catch{/* optional device preference */}}
export function supportIssue(){
 if(!window.isSecureContext)return 'insecure' as const;
 const ios=/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
 const standalone=matchMedia('(display-mode: standalone)').matches||(navigator as Navigator&{standalone?:boolean}).standalone;
 if(ios&&!standalone)return 'ios' as const;
 if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window))return 'unsupported' as const;
 if(Notification.permission==='denied')return 'denied' as const;
 return null;
}
export async function registration(){
 if(!('serviceWorker' in navigator))throw new ApiError('push_unavailable');
 const reg=await navigator.serviceWorker.register('/sw.js',{scope:'/'});
 return await Promise.race([navigator.serviceWorker.ready,new Promise<ServiceWorkerRegistration>((_,reject)=>setTimeout(()=>reject(new Error('worker_timeout')),10000))]).then(()=>reg);
}
function applicationKey(value:string){
 const base=value.replace(/-/g,'+').replace(/_/g,'/');
 return Uint8Array.from(atob(base+'='.repeat((4-base.length%4)%4)),c=>c.charCodeAt(0));
}
async function saveSubscription(sub:PushSubscription,owner:string,key:string,label:string){
 const value=sub.toJSON();
 const {id}=await api<{id:string}>('/api/notifications/subscriptions','POST',{endpoint:value.endpoint,keys:value.keys,label});
 await setWorkerOwner(owner);
 storeDevice({owner,id,key,label});
 return id;
}
export async function enablePush(config:PushConfig,owner:string,label:string){
 // Called only from a click. Request permission before yielding to other work.
 const permission=Notification.permission==='granted'?'granted':await Notification.requestPermission();
 if(permission!=='granted')throw new ApiError('push_permission');
 const reg=await registration();
 let sub=await reg.pushManager.getSubscription();
 if(sub&&deviceMeta()?.key!==config.public_key){await sub.unsubscribe();sub=null;}
 sub=sub||await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:applicationKey(config.public_key)});
 try{return await saveSubscription(sub,owner,config.public_key,label);}
 catch(e){await sub.unsubscribe();storeDevice(null);throw e;}
}
export async function reconnectPush(config:PushConfig,owner:string){
 const meta=deviceMeta();
 if(!meta||meta.owner!==owner||meta.key!==config.public_key||Notification.permission!=='granted')return false;
 const reg=await registration(),sub=await reg.pushManager.getSubscription();
 if(!sub){storeDevice(null);return false;}
 await saveSubscription(sub,owner,config.public_key,meta.label);
 return true;
}
export async function disablePush(owner:string){
 const meta=deviceMeta();
 if(meta?.owner===owner){
   await api('/api/notifications/subscriptions/'+meta.id,'DELETE').catch(e=>{if((e as ApiError).code!=='not_found')throw e;});
 }
 await clearDevicePush();
}
export async function clearDevicePush(){
 storeDevice(null);
 if('serviceWorker' in navigator){
   await setWorkerOwner(null);
   const reg=await navigator.serviceWorker.getRegistration('/');
   if(reg){await (await reg.pushManager.getSubscription())?.unsubscribe();for(const n of await reg.getNotifications())n.close();}
 }
}

async function setWorkerOwner(owner:string|null){
 const reg=await navigator.serviceWorker.getRegistration('/');
 const worker=reg?.active;if(!worker)return;
 await new Promise<void>(resolve=>{const channel=new MessageChannel();const timer=setTimeout(resolve,2000);
   channel.port1.onmessage=()=>{clearTimeout(timer);channel.port1.close();resolve();};
   worker.postMessage({type:'push-owner',owner},[channel.port2]);});
}
