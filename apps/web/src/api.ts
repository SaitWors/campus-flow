let csrf='';
export function setCsrf(value:string){csrf=value;}
export class ApiError extends Error {constructor(public code:string, public detail?:{date?:string;title?:string}){super(code);}}
function responseError(payload:unknown){
 const d=(payload as {detail?:unknown})?.detail;
 if(Array.isArray(d))return new ApiError('validation');
 if(typeof d==='string')return new ApiError(d);
 if(d&&typeof d==='object'){const detail=d as {code?:string;date?:string;title?:string};return new ApiError(detail.code||'service_unavailable',detail);}
 return new ApiError('service_unavailable');
}
export async function api<T>(path:string,method='GET',body?:unknown,headers:Record<string,string>={}):Promise<T>{
 let response:Response;
 try {response=await fetch(path,{method,credentials:'same-origin',cache:'no-store',headers:{...(body!==undefined?{'Content-Type':'application/json'}:{}),...(method!=='GET'?{'X-CSRF-Token':csrf}:{}),...headers},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(12000)});}catch{throw new ApiError('network');}
 if(!response.ok){let payload;try{payload=await response.json();}catch{payload=null;}throw responseError(payload);}
 return response.json();
}
type TransferOptions={signal?:AbortSignal;timeoutMs?:number};
function privatePath(path:string){if(!path.startsWith('/')||path.startsWith('//')||/[\\\x00-\x20\x7f]/.test(path))throw new ApiError('invalid_request');}
export async function apiUpload<T>(path:string,file:Blob,options:TransferOptions&{onProgress?:(percent:number)=>void}={}):Promise<T>{
 privatePath(path);
 if(!csrf)throw new ApiError('csrf_invalid');
 if(options.signal?.aborted)throw new ApiError('cancelled');
 return new Promise<T>((resolve,reject)=>{
  const request=new XMLHttpRequest();
  const cancel=()=>request.abort();
  const cleanup=()=>options.signal?.removeEventListener('abort',cancel);
  const fail=(error:ApiError)=>{cleanup();reject(error);};
  request.open('POST',path);
  request.timeout=options.timeoutMs??300000;
  request.setRequestHeader('Content-Type','application/octet-stream');
  request.setRequestHeader('X-CSRF-Token',csrf);
  request.upload.onprogress=event=>{const total=event.lengthComputable?event.total:file.size;if(total)options.onProgress?.(Math.min(100,Math.round(event.loaded/total*100)));};
  request.onload=()=>{
   cleanup();let payload:unknown;try{payload=JSON.parse(request.responseText);}catch{reject(new ApiError('service_unavailable'));return;}
   if(request.status>=200&&request.status<300)resolve(payload as T);else reject(responseError(payload));
  };
  request.onerror=()=>fail(new ApiError('network'));
  request.ontimeout=()=>fail(new ApiError('request_timeout'));
  request.onabort=()=>fail(new ApiError('cancelled'));
  options.signal?.addEventListener('abort',cancel,{once:true});
  try{request.send(file);}catch{fail(new ApiError('network'));}
 });
}
export async function apiFile(path:string,options:TransferOptions={}):Promise<Blob>{
 privatePath(path);
 if(options.signal?.aborted)throw new ApiError('cancelled');
 const controller=new AbortController();const cancel=()=>controller.abort();
 options.signal?.addEventListener('abort',cancel,{once:true});
 const timer=setTimeout(cancel,options.timeoutMs??300000);
 try{
  const response=await fetch(path,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
  if(!response.ok){let payload;try{payload=await response.json();}catch{payload=null;}throw responseError(payload);}
  return await response.blob();
 }catch(error){if(error instanceof ApiError)throw error;throw new ApiError(options.signal?.aborted?'cancelled':controller.signal.aborted?'request_timeout':'network');}
 finally{clearTimeout(timer);options.signal?.removeEventListener('abort',cancel);}
}
export function actionKey(){return globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;}
