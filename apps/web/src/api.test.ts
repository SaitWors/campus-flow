import {afterEach,describe,expect,it,vi} from 'vitest';
import * as transport from './api';

// The browser owns XHR; this double captures the real transport boundary only.
class UploadRequest {
 static requests:UploadRequest[]=[];
 method='';url='';headers:Record<string,string>={};body:unknown;timeout=0;withCredentials=false;
 status=0;responseText='';upload={onprogress:null as null|((event:{loaded:number;total:number;lengthComputable:boolean})=>void)};
 onload:(()=>void)|null=null;onerror:(()=>void)|null=null;ontimeout:(()=>void)|null=null;onabort:(()=>void)|null=null;
 constructor(){UploadRequest.requests.push(this);}
 open(method:string,url:string){this.method=method;this.url=url;}
 setRequestHeader(name:string,value:string){this.headers[name]=value;}
 send(body:unknown){this.body=body;}
 abort(){this.onabort?.();}
 finish(status:number,data:unknown){this.status=status;this.responseText=JSON.stringify(data);this.onload?.();}
}
afterEach(()=>{transport.setCsrf('');UploadRequest.requests=[];vi.unstubAllGlobals();});

describe('authenticated binary upload',()=>{
 it('sends raw file bytes with the current CSRF token, progress and a long timeout',async()=>{
  vi.stubGlobal('XMLHttpRequest',UploadRequest);transport.setCsrf('fresh-token');
  const file=new Blob([new Uint8Array(70000).fill(19)]);const progress:number[]=[];
  const pending=transport.apiUpload<{id:string}>('/api/schedule/subjects/math/materials?filename=lecture.pdf',file,{onProgress:value=>progress.push(value)});
  const request=UploadRequest.requests[0];
  expect(request.method).toBe('POST');expect(request.headers).toMatchObject({'Content-Type':'application/octet-stream','X-CSRF-Token':'fresh-token'});
  expect(request.body).toBe(file);expect(await (request.body as Blob).arrayBuffer()).toEqual(await file.arrayBuffer());
  expect(request.timeout).toBeGreaterThanOrEqual(300000);
  request.upload.onprogress?.({loaded:35000,total:70000,lengthComputable:true});
  request.finish(201,{id:'uploaded'});
  expect(await pending).toEqual({id:'uploaded'});expect(progress).toEqual([50]);
 });
 it('preserves quota errors returned by the upload endpoint',async()=>{
  vi.stubGlobal('XMLHttpRequest',UploadRequest);transport.setCsrf('token');
  const pending=transport.apiUpload('/api/schedule/subjects/math/materials',new Blob(['bytes']));
  UploadRequest.requests[0].finish(409,{detail:'material_storage_full'});
  await expect(pending).rejects.toMatchObject({code:'material_storage_full'});
 });
 it('cancels an in-flight upload and distinguishes it from a connection failure',async()=>{
  vi.stubGlobal('XMLHttpRequest',UploadRequest);transport.setCsrf('token');const cancel=new AbortController();
  const pending=transport.apiUpload('/api/schedule/subjects/math/materials',new Blob(['bytes']),{signal:cancel.signal});
  cancel.abort();await expect(pending).rejects.toMatchObject({code:'cancelled'});
 });
 it('rejects missing CSRF and off-site destinations before any file leaves the browser',async()=>{
  vi.stubGlobal('XMLHttpRequest',UploadRequest);
  await expect(transport.apiUpload('/api/schedule/subjects/math/materials',new Blob(['bytes']))).rejects.toMatchObject({code:'csrf_invalid'});
  transport.setCsrf('private-token');
  for(const path of ['https://elsewhere.test/upload','//elsewhere.test/upload','/\\elsewhere.test/upload']){
   await expect(transport.apiUpload(path,new Blob(['bytes']))).rejects.toMatchObject({code:'invalid_request'});
  }
  expect(UploadRequest.requests).toHaveLength(0);
 });
 it('reports a timed-out upload separately so the user can check the list before retrying',async()=>{
  vi.stubGlobal('XMLHttpRequest',UploadRequest);transport.setCsrf('token');
  const pending=transport.apiUpload('/api/schedule/subjects/math/materials',new Blob(['bytes']));
  UploadRequest.requests[0].ontimeout?.();await expect(pending).rejects.toMatchObject({code:'request_timeout'});
 });
 it('blocks control characters that a browser would strip into an off-site URL',async()=>{
  vi.stubGlobal('XMLHttpRequest',UploadRequest);transport.setCsrf('private-token');
  for(const path of ['/\t/elsewhere.test/upload','/\n/elsewhere.test/upload','/\r/elsewhere.test/upload']){
   const pending=transport.apiUpload(path,new Blob(['private-file']));
   UploadRequest.requests.at(-1)?.finish(201,{id:'leaked'});
   await expect(pending).rejects.toMatchObject({code:'invalid_request'});
  }
  expect(UploadRequest.requests).toHaveLength(0);
 });
});

describe('private file reads',()=>{
 it('also disables browser caching for material metadata requests',async()=>{
  let options:RequestInit|undefined;
  vi.stubGlobal('fetch',async(_path:string,init:RequestInit)=>{options=init;return new Response('{"items":[],"total":0}');});
  expect(await transport.api('/api/schedule/subjects/math/materials')).toEqual({items:[],total:0});
  expect(options?.cache).toBe('no-store');
 });
 it('fetches an authorized binary response with browser caching disabled',async()=>{
  const bytes=new Uint8Array([137,80,78,71,13,10,26,10]);let options:RequestInit|undefined;
  vi.stubGlobal('fetch',async(_path:string,init:RequestInit)=>{options=init;return new Response(bytes,{headers:{'Content-Type':'image/png'}});});
  const blob=await transport.apiFile('/api/schedule/materials/file-id/file?inline=true');
  expect(options).toMatchObject({credentials:'same-origin',cache:'no-store'});
  expect(blob.type).toBe('image/png');expect(new Uint8Array(await blob.arrayBuffer())).toEqual(bytes);
 });
 it('keeps direct file access denials visible to the interface',async()=>{
  vi.stubGlobal('fetch',async()=>new Response(JSON.stringify({detail:'account_inactive'}),{status:403}));
  await expect(transport.apiFile('/api/schedule/materials/file-id/file')).rejects.toMatchObject({code:'account_inactive'});
 });
});
