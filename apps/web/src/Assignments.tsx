import {useState} from 'react';
import {CheckCircle2,ClipboardList,Plus,RefreshCw,Search} from 'lucide-react';
import {ApiError,api} from './api';
import type {Assignment,Subject} from './types';
import {assignmentSubgroup,instantToLocalInput,localInputToInstant} from './study';
import {Button,Confirm,Dialog,Empty,Field,IconButton,Loading,Notice,useApp,useJob,useT} from './ui';
import {lessonTitle} from './Lessons';
import AssignmentCard,{progressStatuses} from './AssignmentCard';
import {SubgroupFilter,useResource,useStudyFilters} from './useStudy';

export default function Assignments({revision}:{revision:number}){
 const t=useT();const {user,settings,lang}=useApp();const manage=user.role!=='student';const {filters,update}=useStudyFilters();
 const subgroup=assignmentSubgroup(filters.subgroup,user);const [subjectKey,setSubjectKey]=useState('');const [status,setStatus]=useState('unfinished');const [search,setSearch]=useState('');const [form,setForm]=useState<Assignment|null|false>(false);const [archive,setArchive]=useState<Assignment|null>(null);
 const assignments=useResource<Assignment[]>(`/api/schedule/assignments?subgroup=${subgroup}${subjectKey?'&subject_key='+encodeURIComponent(subjectKey):''}`,revision,60000);
 const subjects=useResource<Subject[]>('/api/schedule/subjects',revision);
 const items=assignments.data||[];const done=items.filter(a=>a.progress.status==='done').length;
 const shown=items.filter(a=>(status==='all'||status==='unfinished'&&a.progress.status!=='done'||a.progress.status===status)&&(!search||(a.title+' '+a.subject_title+' '+a.description).toLocaleLowerCase().includes(search.toLocaleLowerCase()))).sort((a,b)=>((a.due_at?Date.parse(a.due_at):Infinity)-(b.due_at?Date.parse(b.due_at):Infinity))||a.title.localeCompare(b.title));
 const replace=(assignment:Assignment)=>assignments.setData(previous=>previous?.map(a=>a.id===assignment.id?assignment:a)||null);
 return <>
  <div className="page-heading"><div><span className="eyebrow">{settings.group} / CAMPUS FLOW</span><h1>{t('assignments')}</h1><p>{t('personalWork')}</p></div><div className="heading-actions">{manage&&<Button onClick={()=>setForm(null)} disabled={!subjects.data?.length}><Plus size={18}/>{t('addAssignment')}</Button>}</div></div>
  <div className="assignment-overview"><div className="assignment-overview-count"><ClipboardList size={20}/><strong>{items.length}</strong><span>{t('assignmentCount')}</span></div><div className="assignment-overview-count"><CheckCircle2 size={20}/><strong>{done}</strong><span>{t('completedCount')}</span></div><div className="assignment-progress-bar" aria-hidden="true"><span style={{width:(items.length?done/items.length*100:0)+'%'}}/></div></div>
  <p className="personal-status-note"><CheckCircle2 size={16}/>{t('personalStatuses')}</p>
  <div className="academic-toolbar"><div className="search-control"><Search size={17}/><input type="search" aria-label={t('searchAssignments')} placeholder={t('searchAssignments')} value={search} onChange={e=>setSearch(e.target.value)}/></div><SubgroupFilter value={subgroup} assignments onChange={value=>update({subgroup:value})}/><select aria-label={t('subjects')} value={subjectKey} onChange={e=>setSubjectKey(e.target.value)}><option value="">{t('allSubjects')}</option>{subjects.data?.map(s=><option key={s.key} value={s.key}>{lessonTitle(s,lang)}</option>)}</select><select aria-label={t('personalStatus')} value={status} onChange={e=>setStatus(e.target.value)}><option value="unfinished">{t('unfinished')}</option><option value="all">{t('allAssignments')}</option>{progressStatuses.map(s=><option key={s} value={s}>{t(s)}</option>)}</select><IconButton label={t('refresh')} onClick={()=>{assignments.reload();subjects.reload();}}><RefreshCw size={18}/></IconButton></div>
  {(assignments.error||subjects.error)&&<Notice error>{assignments.error||subjects.error}<button onClick={()=>{assignments.reload();subjects.reload();}}>{t('retry')}</button></Notice>}
  {assignments.loading?<Loading/>:!shown.length?<Empty title={t(items.length?'noAssignmentMatches':'noAssignments')} description={t('noAssignmentsHint')}>{items.length>0&&<Button variant="secondary" onClick={()=>{setStatus('all');setSubjectKey('');setSearch('');}}>{t('allAssignments')}</Button>}{manage&&<Button onClick={()=>setForm(null)} disabled={!subjects.data?.length}><Plus size={17}/>{t('addAssignment')}</Button>}</Empty>:<div className="assignment-grid">{shown.map(a=><AssignmentCard key={a.id} assignment={a} subject={subjects.data?.find(s=>s.key===a.subject_key)} onUpdate={replace} onReload={assignments.reload} onEdit={manage?()=>setForm(a):undefined} onArchive={manage?()=>setArchive(a):undefined}/>)}</div>}
  <p className="field-hint">{t('deadlineZone')}: {settings.timezone}. {t('savedFilters')}</p>
  {form!==false&&<AssignmentForm initial={form||undefined} subjects={subjects.data||[]} onClose={()=>setForm(false)} onSaved={()=>{assignments.reload();setForm(false);}} onReload={()=>{assignments.reload();setForm(false);}}/>}
  {archive&&<Confirm title={t('archiveAssignment')} description={t('archiveAssignmentHint')} onClose={()=>setArchive(null)} onConfirm={async()=>{await api(`/api/schedule/assignments/${archive.id}?revision=${archive.revision}`,'DELETE');assignments.reload();}}/>}
 </>;
}

export function AssignmentForm({initial,subjects,subjectKey='',onClose,onSaved,onReload}:{initial?:Assignment;subjects:Subject[];subjectKey?:string;onClose:()=>void;onSaved:()=>void;onReload:()=>void}){
 const t=useT();const {lang,settings,notify}=useApp();const job=useJob();
 const [data,setData]=useState({subject_key:initial?.subject_key||subjectKey||subjects[0]?.key||'',title:initial?.title||'',description:initial?.description||'',due:instantToLocalInput(initial?.due_at||null,settings.timezone),subgroup:initial?.subgroup||0,material_url:initial?.material_url||''});
 const set=(key:keyof typeof data,value:string|number)=>setData(d=>({...d,[key]:value}));
 async function save(){
  const due_at=data.due?localInputToInstant(data.due,settings.timezone):null;
  if(data.due&&!due_at)throw new ApiError('invalid_due_at');
  if(!data.subject_key)throw new ApiError('invalid_subject');
  const body={subject_key:data.subject_key,title:data.title.trim(),description:data.description.trim(),due_at,subgroup:data.subgroup,material_url:data.material_url.trim(),...(initial?{revision:initial.revision}:{})};
  await api('/api/schedule/assignments'+(initial?'/'+initial.id:''),initial?'PUT':'POST',body);
  notify(t('assignmentSaved'));onSaved();
 }
 return <Dialog title={t(initial?'editAssignment':'addAssignment')} onClose={()=>!job.busy&&onClose()} wide><form onSubmit={e=>{e.preventDefault();void job.run(save);}}><fieldset className="form-fieldset" disabled={job.busy}>
  <div className="form-grid"><Field label={t('subjects')}><select required value={data.subject_key} onChange={e=>set('subject_key',e.target.value)}>{subjects.map(s=><option key={s.key} value={s.key}>{lessonTitle(s,lang)}</option>)}</select></Field><Field label={t('assignmentTitle')}><input required maxLength={160} autoFocus value={data.title} onChange={e=>set('title',e.target.value)} placeholder={lang==='ru'?'Лабораторная № 1':'Laboratory assignment 1'}/></Field></div>
  <Field label={t('description')}><textarea maxLength={10000} rows={5} value={data.description} onChange={e=>set('description',e.target.value)}/></Field>
  <div className="form-grid"><Field label={t('dueAt')} hint={t('deadlineZone')+': '+settings.timezone}><input type="datetime-local" value={data.due} onChange={e=>set('due',e.target.value)}/></Field><Field label={t('assignmentAudience')}><select value={data.subgroup} onChange={e=>set('subgroup',Number(e.target.value))}><option value={0}>{t('allGroups')}</option><option value={1}>{t('subgroup')} 1</option><option value={2}>{t('subgroup')} 2</option></select></Field></div>
  <Field label={t('materialUrl')}><input type="url" pattern="https://.*" maxLength={500} value={data.material_url} onChange={e=>set('material_url',e.target.value)} placeholder="https://"/></Field>
 </fieldset>{job.error&&<Notice error>{job.error}<button type="button" onClick={onReload}>{t('refreshConflict')}</button></Notice>}<div className="form-actions"><Button variant="secondary" onClick={onClose} disabled={job.busy}>{t('cancel')}</Button><Button type="submit" busy={job.busy}>{t('save')}</Button></div></form></Dialog>;
}
