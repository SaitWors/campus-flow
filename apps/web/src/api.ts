let csrf='';
export function setCsrf(value:string){csrf=value;}
export class ApiError extends Error {constructor(public code:string, public detail?:{date?:string;title?:string}){super(code);}}
export async function api<T>(path:string,method='GET',body?:unknown,headers:Record<string,string>={}):Promise<T>{
 let response:Response;
 try {response=await fetch(path,{method,credentials:'same-origin',headers:{...(body!==undefined?{'Content-Type':'application/json'}:{}),...(method!=='GET'?{'X-CSRF-Token':csrf}:{}),...headers},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(12000)});}catch{throw new ApiError('network');}
 if(!response.ok){let d;try{d=(await response.json()).detail;}catch{d='service_unavailable';}if(Array.isArray(d))throw new ApiError('validation');throw new ApiError(typeof d==='string'?d:d?.code||'service_unavailable',typeof d==='object'?d:undefined);}
 return response.json();
}
export function actionKey(){return globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;}
