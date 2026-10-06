import {useState} from 'react';
import {ArrowUpRight,CheckCircle2,Clock,ExternalLink,Pencil,Trash2} from 'lucide-react';
import {api} from './api';
import {errorText} from './i18n';
import {locale} from './date';
import {instantToLocalInput} from './study';
import type {Assignment,AssignmentProgress,ProgressStatus,Subject} from './types';
import {Badge,Button,Dialog,IconButton,Notice,useApp,useT} from './ui';
import {lessonTitle} from './Lessons';

export const progressStatuses:ProgressStatus[]=['not_started','in_progress','ready','done'];
export function formatDeadline(due:string|null,lang:'ru'|'en',zone:string){return due?new Intl.DateTimeFormat(locale(lang),{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:zone}).format(new Date(due)):'';}

export function DueBadge({assignment}:{assignment:Assignment}){
 const {settings,lang}=useApp();const t=useT();
 if(!assignment.due_at)return <span className="assignment-no-due">{t('noDueDate')}</span>;
 const now=new Date();const overdue=Date.parse(assignment.due_at)<now.getTime()&&assignment.progress.status!=='done';
 const today=instantToLocalInput(assignment.due_at,settings.timezone).slice(0,10)===instantToLocalInput(now.toISOString(),settings.timezone).slice(0,10);
 return <span className={'assignment-due '+(overdue?'overdue':today?'due-today':'')}><Clock size={14}/>{formatDeadline(assignment.due_at,lang,settings.timezone)}{overdue&&<Badge tone="red">{t('overdue')}</Badge>}{!overdue&&today&&assignment.progress.status!=='done'&&<Badge tone="amber">{t('dueToday')}</Badge>}</span>;
}

function ProgressControl({assignment,onUpdate,onReload}:{assignment:Assignment;onUpdate:(assignment:Assignment)=>void;onReload:()=>void}){
 const t=useT();const {lang,notify}=useApp();const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 async function save(status:ProgressStatus){
  setBusy(true);setError('');
  try{const progress=await api<AssignmentProgress>(`/api/schedule/assignments/${assignment.id}/progress`,'PUT',{status,revision:assignment.progress.revision});onUpdate({...assignment,progress});notify(t('progressSaved'));}
  catch(e){setError(errorText(lang,e));}
  finally{setBusy(false);}
 }
 return <div className="progress-control"><label><span>{t('personalStatus')}</span><select aria-label={t('personalStatus')+' · '+assignment.title} value={assignment.progress.status} disabled={busy} onChange={e=>void save(e.target.value as ProgressStatus)}>{progressStatuses.map(status=><option key={status} value={status}>{t(status)}</option>)}</select></label>{busy&&<small role="status">{t('loading')}</small>}{error&&<Notice error>{error}<button onClick={onReload}>{t('refreshConflict')}</button></Notice>}</div>;
}

export default function AssignmentCard({assignment,subject,onUpdate,onReload,onEdit,onArchive,brief=false}:{assignment:Assignment;subject?:Subject;onUpdate:(assignment:Assignment)=>void;onReload:()=>void;onEdit?:()=>void;onArchive?:()=>void;brief?:boolean}){
 const t=useT();const [detail,setDetail]=useState(false);const {lang}=useApp();
 const subjectName=subject?lessonTitle(subject,lang):assignment.subject_title;
 const material=assignment.material_url.startsWith('https://')?assignment.material_url:'';
 return <article className={`assignment-card ${assignment.progress.status}${brief?' brief':''}`}>
  <div className="assignment-card-head"><a className="assignment-subject" href={'#subject/'+encodeURIComponent(assignment.subject_key)}>{subjectName}<ArrowUpRight size={13}/></a>{assignment.progress.status==='done'&&<CheckCircle2 size={18} className="done-icon"/>}{assignment.subgroup>0&&<Badge>{t('subgroup')} {assignment.subgroup}</Badge>}</div>
  <button type="button" className="assignment-title" onClick={()=>setDetail(true)} aria-label={t('assignmentOpen')+' · '+assignment.title}><h3>{assignment.title}</h3></button>
  {!brief&&assignment.description&&<p className="assignment-description">{assignment.description}</p>}
  <DueBadge assignment={assignment}/>
  <div className="assignment-card-foot"><ProgressControl assignment={assignment} onUpdate={onUpdate} onReload={onReload}/>{(material||onEdit||onArchive)&&<div className="assignment-actions">{material&&<a className="icon-button" aria-label={t('materials')+' · '+assignment.title} title={t('materials')} href={material} target="_blank" rel="noreferrer"><ExternalLink size={17}/></a>}{onEdit&&<IconButton label={t('editAssignment')+' · '+assignment.title} onClick={onEdit}><Pencil size={17}/></IconButton>}{onArchive&&<IconButton label={t('archiveAssignment')+' · '+assignment.title} onClick={onArchive}><Trash2 size={17}/></IconButton>}</div>}</div>
  {detail&&<Dialog title={assignment.title} onClose={()=>setDetail(false)} wide><div className="assignment-detail"><a className="text-link" href={'#subject/'+encodeURIComponent(assignment.subject_key)} onClick={()=>setDetail(false)}>{subjectName}<ArrowUpRight size={16}/></a><div className="badge-row"><Badge>{assignment.subgroup?t('subgroup')+' '+assignment.subgroup:t('allGroups')}</Badge><DueBadge assignment={assignment}/></div><p className="assignment-full-description">{assignment.description||'—'}</p>{material&&<a className="button secondary" href={material} target="_blank" rel="noreferrer">{t('materials')}<ExternalLink size={17}/></a>}<ProgressControl assignment={assignment} onUpdate={onUpdate} onReload={()=>{setDetail(false);onReload();}}/><p className="personal-status-note">{t('personalStatuses')}</p>{onEdit&&<Button variant="secondary" onClick={()=>{setDetail(false);onEdit();}}><Pencil size={17}/>{t('editAssignment')}</Button>}</div></Dialog>}
 </article>;
}
