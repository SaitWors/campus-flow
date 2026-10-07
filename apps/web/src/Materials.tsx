import {useEffect,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,CheckCircle2,Download,FileText,Image,Paperclip,RefreshCw,Search,Settings2,Trash2,Upload,X} from 'lucide-react';
import {api,apiFile,apiUpload,ApiError} from './api';
import {format} from './date';
import {errorText} from './i18n';
import {canManageMaterials,canPreviewMaterial,formatFileSize,materialAccept,materialFileError,materialListPath,materialTitle,materialUploadPath} from './study';
import type {Material,MaterialCategory,MaterialPage,MaterialStorage} from './types';
import {Badge,Button,Confirm,Dialog,Empty,Field,IconButton,Loading,Notice,useApp,useT} from './ui';
import {useResource} from './useStudy';
import './materials.css';

const categories:MaterialCategory[]=['lecture','notes','other'];
const categoryLabel=(category:MaterialCategory)=>category==='lecture'?'materialLecture':category==='notes'?'materialNotes':'materialOther';

export default function SubjectMaterials({subjectKey,revision}:{subjectKey:string;revision:number}){
 const {user,lang}=useApp();const t=useT();const manage=canManageMaterials(user);
 const [category,setCategory]=useState<MaterialCategory|'all'>('all');
 const [search,setSearch]=useState('');const [query,setQuery]=useState('');const [offset,setOffset]=useState(0);
 const [uploadStorage,setUploadStorage]=useState<MaterialStorage|null>(null);
 const [editing,setEditing]=useState<Material|null>(null);const [deleting,setDeleting]=useState<Material|null>(null);const [preview,setPreview]=useState<Material|null>(null);
 const resource=useResource<MaterialPage>(materialListPath(subjectKey,category,query,offset),revision);
 useEffect(()=>{const timer=setTimeout(()=>{setQuery(search);setOffset(0);},300);return()=>clearTimeout(timer);},[search]);
 const data=resource.data;
 useEffect(()=>{if(data&&offset&&offset>=data.total)setOffset(data.total?Math.floor((data.total-1)/20)*20:0);},[data,offset]);
 const changed=()=>resource.reload();
 const uploaded=()=>{setCategory('all');setSearch('');setQuery('');setOffset(0);changed();};
 return <section className="subject-materials" aria-labelledby="subject-materials-heading">
  <div className="section-title material-heading"><div><h2 id="subject-materials-heading"><Paperclip size={20}/>{t('subjectMaterials')}{data&&<Badge>{data.total}</Badge>}</h2><p className="field-hint">{t(manage?'materialHint':'materialReadHint')}</p></div>
   {manage&&<Button onClick={()=>data&&setUploadStorage(data.storage)} disabled={!data}><Upload size={17}/>{t('uploadMaterials')}</Button>}
  </div>
  <div className="academic-toolbar material-toolbar"><div className="search-control"><Search size={17}/><input type="search" maxLength={160} aria-label={t('materialSearch')} placeholder={t('materialSearch')} value={search} onChange={event=>setSearch(event.target.value)}/></div>
   <select aria-label={t('materialCategoryFilter')} value={category} onChange={event=>{setCategory(event.target.value as MaterialCategory|'all');setOffset(0);}}><option value="all">{t('materialAll')}</option>{categories.map(value=><option key={value} value={value}>{t(categoryLabel(value))}</option>)}</select>
   <IconButton label={t('materialReload')} onClick={changed}><RefreshCw size={18}/></IconButton>
  </div>
  {manage&&data&&<p className="material-storage">{t('materialStorage')}: {formatFileSize(data.storage.used_bytes,lang)} {t('materialOf')} {formatFileSize(data.storage.quota_bytes,lang)}<span> · {t('materialMaxSize')}: {formatFileSize(data.storage.max_file_bytes,lang)}</span></p>}
  {resource.error&&<Notice error>{resource.error}<button onClick={changed}>{t('retry')}</button></Notice>}
  {resource.loading?<Loading/>:!data?null:!data.items.length?<Empty title={t(query.trim()||category!=='all'?'materialsNoResults':'materialsEmpty')} description={t('materialsEmptyHint')}>{(query.trim()||category!=='all')&&<Button variant="secondary" onClick={()=>{setSearch('');setQuery('');setCategory('all');setOffset(0);}}>{t('resetFilters')}</Button>}</Empty>:<div className="material-list">{data.items.map(material=><MaterialCard key={material.id} material={material} manage={manage} onPreview={()=>setPreview(material)} onEdit={()=>setEditing(material)} onDelete={()=>setDeleting(material)}/>)}</div>}
  {data&&data.total>20&&<nav className="material-pagination" aria-label={t('subjectMaterials')}><Button variant="secondary" disabled={!offset||resource.loading} onClick={()=>setOffset(value=>Math.max(0,value-20))}><ArrowLeft size={16}/>{t('prev')}</Button><span>{t('materialPage')} {Math.floor(offset/20)+1} {t('materialOf')} {Math.ceil(data.total/20)}</span><Button variant="secondary" disabled={offset+20>=data.total||resource.loading} onClick={()=>setOffset(value=>value+20)}>{t('next')}<ArrowRight size={16}/></Button></nav>}
  {manage&&uploadStorage&&<UploadMaterials subjectKey={subjectKey} storage={uploadStorage} defaultCategory={category==='all'?'other':category} onClose={()=>setUploadStorage(null)} onChanged={uploaded}/>}
  {manage&&editing&&<EditMaterial material={editing} onClose={()=>setEditing(null)} onSaved={()=>{setEditing(null);changed();}}/>}
  {manage&&deleting&&<Confirm title={t('deleteMaterial')} description={deleting.title+' — '+t('deleteMaterialHint')} onClose={()=>setDeleting(null)} onConfirm={async()=>{await api(`/api/schedule/materials/${encodeURIComponent(deleting.id)}?revision=${deleting.revision}`,'DELETE');changed();}}/>}
  {preview&&<PreviewMaterial material={preview} onClose={()=>setPreview(null)}/>}
 </section>;
}

function MaterialCard({material,manage,onPreview,onEdit,onDelete}:{material:Material;manage:boolean;onPreview:()=>void;onEdit:()=>void;onDelete:()=>void}){
 const {lang,settings}=useApp();const t=useT();const [downloading,setDownloading]=useState(false);const [error,setError]=useState('');
 const transfer=useRef<AbortController|null>(null);const objectUrls=useRef(new Set<string>());
 useEffect(()=>()=>{transfer.current?.abort();for(const url of objectUrls.current)URL.revokeObjectURL(url);objectUrls.current.clear();},[]);
 async function download(){
  if(transfer.current)return;
  const controller=new AbortController();transfer.current=controller;setDownloading(true);setError('');
  try{
   const blob=await apiFile(`/api/schedule/materials/${encodeURIComponent(material.id)}/file`,{signal:controller.signal});
   if(controller.signal.aborted)return;
   const url=URL.createObjectURL(blob);objectUrls.current.add(url);
   const anchor=document.createElement('a');anchor.href=url;anchor.download=material.original_filename;document.body.appendChild(anchor);anchor.click();anchor.remove();
   setTimeout(()=>{URL.revokeObjectURL(url);objectUrls.current.delete(url);},1000);
  }catch(problem){if(!controller.signal.aborted)setError(errorText(lang,problem));}
  finally{if(transfer.current===controller){transfer.current=null;setDownloading(false);}}
 }
 const raster=canPreviewMaterial(material.mime_type);
 return <article className="material-card"><div className="material-symbol">{raster?<Image size={23}/>:<FileText size={23}/>}</div><div className="material-info"><div className="material-title"><h3>{material.title}</h3><Badge>{t(categoryLabel(material.category))}</Badge></div>{material.description&&<p className="material-description">{material.description}</p>}
  <p className="material-filename">{material.original_filename}<span> · {formatFileSize(material.size_bytes,lang)}</span></p>
  <p className="material-meta"><span>{material.uploader_name||t('materialUnknownAuthor')}</span><span> · </span><time dateTime={material.created_at}>{format(new Date(material.created_at),lang,{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',timeZone:settings.timezone})}</time></p>
  {error&&<Notice error>{error}</Notice>}</div><div className="material-actions">
   {raster&&<IconButton label={t('materialPreview')+' · '+material.title} onClick={onPreview}><Image size={18}/></IconButton>}
   <Button variant="secondary" onClick={()=>void download()} busy={downloading} aria-label={t('materialDownload')+' · '+material.title}><Download size={16}/>{t('materialDownload')}</Button>
   {downloading&&<IconButton label={t('materialCancelDownload')+' · '+material.title} onClick={()=>transfer.current?.abort()}><X size={18}/></IconButton>}
   {manage&&<><IconButton label={t('editMaterial')+' · '+material.title} onClick={onEdit}><Settings2 size={17}/></IconButton><IconButton label={t('deleteMaterial')+' · '+material.title} onClick={onDelete}><Trash2 size={17}/></IconButton></>}
  </div></article>;
}

type UploadItem={id:number;file:File;title:string;description:string;category:MaterialCategory;status:'pending'|'uploading'|'done'|'error'|'unknown';progress:number;error?:string};
function UploadMaterials({subjectKey,storage,defaultCategory,onClose,onChanged}:{subjectKey:string;storage:MaterialStorage;defaultCategory:MaterialCategory;onClose:()=>void;onChanged:()=>void}){
 const {lang,notify}=useApp();const t=useT();const [items,setItems]=useState<UploadItem[]>([]);const [busy,setBusy]=useState(false);const [stopped,setStopped]=useState(false);
 const transfer=useRef<AbortController|null>(null);const alive=useRef(true);const nextId=useRef(0);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;transfer.current?.abort();};},[]);
 const update=(id:number,patch:Partial<UploadItem>)=>{if(alive.current)setItems(previous=>previous.map(item=>item.id===id?{...item,...patch}:item));};
 const remaining=items.filter(item=>['pending','error'].includes(item.status));
 const invalid=remaining.some(item=>!!materialFileError(item.file,storage.max_file_bytes));
 async function upload(){
  if(transfer.current||!remaining.length||invalid)return;
  const controller=new AbortController();transfer.current=controller;setBusy(true);setStopped(false);let added=0,finished=true;
  for(const item of remaining){
   if(controller.signal.aborted){finished=false;break;}
   update(item.id,{status:'uploading',progress:0,error:undefined});
   try{
    await apiUpload<Material>(materialUploadPath(subjectKey,{filename:item.file.name,title:item.title,description:item.description.trim(),category:item.category}),item.file,{signal:controller.signal,onProgress:progress=>update(item.id,{progress})});
    added++;update(item.id,{status:'done',progress:100});
   }catch(problem){
    const code=(problem as ApiError).code||'network';const uncertain=['network','request_timeout','cancelled','service_unavailable','storage_unavailable'].includes(code);
    update(item.id,{status:uncertain?'unknown':'error',error:code==='validation'?'material_metadata_invalid':code});
    if(controller.signal.aborted&&alive.current)setStopped(true);finished=false;break;
   }
  }
  transfer.current=null;
  if(!alive.current)return;
  setBusy(false);onChanged();
  if(added)notify(t('materialsAdded')+' '+added);
  if(finished)onClose();
 }
 const close=()=>busy?transfer.current?.abort():onClose();
 return <Dialog title={t('uploadMaterials')} onClose={close} wide><form onSubmit={event=>{event.preventDefault();void upload();}}>
  <fieldset className="form-fieldset" disabled={busy}><Field label={t('materialFiles')} hint={t('materialFilesHint')}><input type="file" multiple accept={materialAccept} onChange={event=>{const files=Array.from(event.target.files||[]);setItems(files.map(file=>({id:nextId.current++,file,title:materialTitle(file.name),description:'',category:defaultCategory,status:'pending',progress:0})));setStopped(false);event.target.value='';}}/></Field></fieldset>
  <p className="field-hint material-upload-limit">{t('materialTypesHint')} {t('materialMaxSize')}: {formatFileSize(storage.max_file_bytes,lang)}.</p>
  <div className="material-upload-list">{items.map(item=>{
   const validation=materialFileError(item.file,storage.max_file_bytes);const locked=busy||['done','unknown'].includes(item.status);
   return <div className={'material-upload-item '+item.status} key={item.id}><div className="material-upload-file"><FileText size={18}/><strong>{item.file.name}</strong><span>{formatFileSize(item.file.size,lang)}</span>{!busy&&<IconButton label={t('materialRemoveFile')+' · '+item.file.name} onClick={()=>setItems(previous=>previous.filter(file=>file.id!==item.id))}><X size={17}/></IconButton>}</div>
    <fieldset className="form-fieldset" disabled={locked}><Field label={t('materialTitle')+' · '+item.file.name}><input required maxLength={160} value={item.title} onChange={event=>update(item.id,{title:event.target.value})}/></Field><div className="material-upload-details"><Field label={t('materialDescription')+' · '+item.file.name}><textarea maxLength={4000} rows={2} value={item.description} onChange={event=>update(item.id,{description:event.target.value})}/></Field><Field label={t('materialCategory')+' · '+item.file.name}><select value={item.category} onChange={event=>update(item.id,{category:event.target.value as MaterialCategory})}>{categories.map(value=><option key={value} value={value}>{t(categoryLabel(value))}</option>)}</select></Field></div></fieldset>
    {validation&&<Notice error>{errorText(lang,new ApiError(validation))}</Notice>}
    {item.error&&<Notice error>{item.status==='unknown'?t('materialUploadUncertain'):errorText(lang,new ApiError(item.error))}{item.status==='unknown'&&<span className="material-uncertain">{t('materialCheckList')}</span>}</Notice>}
    <div className="material-upload-status" aria-live="polite">{item.status==='done'?<><CheckCircle2 size={16}/>{t('materialUploaded')}</>:item.status==='uploading'?<><span>{t(item.progress===100?'materialPublishing':'materialUploading')}{item.progress<100?' · '+item.progress+'%':''}</span><progress max={100} value={item.progress}/></>:item.status==='pending'?t('materialQueued'):null}</div>
   </div>;
  })}</div>
  {stopped&&<Notice>{t('materialUploadStopped')}</Notice>}
  <div className="form-actions"><Button variant="secondary" onClick={close}>{t(busy?'materialStopUpload':'close')}</Button><Button type="submit" busy={busy} disabled={!remaining.length||invalid}>{t(items.some(item=>item.status==='done')?'materialUploadRemaining':'materialUpload')}</Button></div>
 </form></Dialog>;
}

function EditMaterial({material,onClose,onSaved}:{material:Material;onClose:()=>void;onSaved:()=>void}){
 const {lang,notify}=useApp();const t=useT();const [title,setTitle]=useState(material.title);const [description,setDescription]=useState(material.description);const [category,setCategory]=useState(material.category);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 return <Dialog title={t('editMaterial')} onClose={()=>!busy&&onClose()}><form onSubmit={async event=>{event.preventDefault();setBusy(true);setError('');try{await api(`/api/schedule/materials/${encodeURIComponent(material.id)}`,'PATCH',{revision:material.revision,title:title.trim(),description:description.trim(),category});notify(t('materialSaved'));onSaved();}catch(problem){setError(errorText(lang,(problem as ApiError).code==='validation'?new ApiError('material_metadata_invalid'):problem));}finally{setBusy(false);}}}>
  <p className="material-edit-filename">{material.original_filename}</p><fieldset className="form-fieldset" disabled={busy}><Field label={t('materialTitle')}><input required maxLength={160} autoFocus value={title} onChange={event=>setTitle(event.target.value)}/></Field><Field label={t('materialDescription')}><textarea maxLength={4000} rows={5} value={description} onChange={event=>setDescription(event.target.value)}/></Field><Field label={t('materialCategory')}><select value={category} onChange={event=>setCategory(event.target.value as MaterialCategory)}>{categories.map(value=><option key={value} value={value}>{t(categoryLabel(value))}</option>)}</select></Field></fieldset>
  {error&&<Notice error>{error}</Notice>}<div className="form-actions"><Button variant="secondary" onClick={onClose} disabled={busy}>{t('cancel')}</Button><Button type="submit" busy={busy} disabled={!title.trim()}>{t('save')}</Button></div>
 </form></Dialog>;
}

function PreviewMaterial({material,onClose}:{material:Material;onClose:()=>void}){
 const {lang}=useApp();const t=useT();const [url,setUrl]=useState('');const [error,setError]=useState('');const [loading,setLoading]=useState(true);const [attempt,setAttempt]=useState(0);
 useEffect(()=>{
  const controller=new AbortController();let objectUrl='';setUrl('');setError('');setLoading(true);
  void apiFile(`/api/schedule/materials/${encodeURIComponent(material.id)}/file?inline=true`,{signal:controller.signal}).then(blob=>{
   if(controller.signal.aborted)return;
   if(!canPreviewMaterial(blob.type))throw new ApiError('material_content_mismatch');
   objectUrl=URL.createObjectURL(blob);setUrl(objectUrl);
  }).catch(problem=>{if(!controller.signal.aborted)setError(errorText(lang,problem));}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
  return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
 },[material.id,lang,attempt]);
 return <Dialog title={material.title} onClose={onClose} wide><div className="material-preview">{loading?<Loading/>:error?<Notice error>{error}<button onClick={()=>setAttempt(value=>value+1)}>{t('retry')}</button></Notice>:url&&<img src={url} alt={material.title} onError={()=>setError(errorText(lang,new ApiError('material_preview_failed')))}/>}</div><p className="field-hint material-preview-caption">{material.original_filename} · {formatFileSize(material.size_bytes,lang)}</p></Dialog>;
}
