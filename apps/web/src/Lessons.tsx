import {lazy,Suspense,useEffect,useState} from 'react';
import {BookOpen,CalendarDays,ChevronRight,Clock,ExternalLink,MapPin,Monitor,Settings2,Trash2,UserRound,Users} from 'lucide-react';
import {api} from './api';
import {errorText} from './i18n';
import {format,fromIso} from './date';
import type {Lang,Lesson} from './types';
import {Badge,Button,Confirm,Dialog,Loading,Notice,useApp,useT} from './ui';

const LessonForm=lazy(()=>import('./LessonForm'));
export function lessonTitle(lesson:Pick<Lesson,'title'|'title_en'|'title_en_auto'>,lang:Lang){return lang==='en'?(lesson.title_en||lesson.title_en_auto||lesson.title):lesson.title;}

export function LessonCard({lesson,onClick,compact=false}:{lesson:Lesson;onClick:()=>void;compact?:boolean}){
 const {lang}=useApp();const t=useT();
 return <button type="button" className={`lesson-card ${lesson.kind}${lesson.status==='cancelled'?' cancelled':''}${compact?' compact':''}`} onClick={onClick}>
  <div className="lesson-card-top"><Badge tone={lesson.kind}>{t(lesson.kind)}</Badge><span>{lesson.start}–{lesson.end}</span></div>
  <h3>{lessonTitle(lesson,lang)}</h3>
  <div className="lesson-meta"><span>{lesson.mode==='remote'?<Monitor size={14}/>:<MapPin size={14}/>} {lesson.room||t(lesson.mode)}</span>{lesson.subgroup>0&&<span><Users size={14}/> {lesson.subgroup}</span>}</div>
  <div className="lesson-card-bottom">{lesson.status!=='confirmed'?<Badge tone={lesson.status==='cancelled'?'red':'amber'}>{t(lesson.status)}</Badge>:<span className="lesson-mode">{t(lesson.mode)}</span>}<ChevronRight size={15}/></div>
 </button>;
}

export function LessonDetail({initial,revision,onClose,guest=false}:{initial:Lesson;revision:number;onClose:()=>void;guest?:boolean}){
 const t=useT();const {lang,user,refresh}=useApp();
 const [lesson,setLesson]=useState(initial);const [edit,setEdit]=useState(false);const [remove,setRemove]=useState(false);const [error,setError]=useState('');
 useEffect(()=>{
  if(guest)return;
  let active=true;
  api<Lesson>('/api/schedule/occurrences/'+initial.id).then(l=>{if(active){setLesson(l);setError('');}}).catch(e=>active&&setError(errorText(lang,e)));
  return()=>{active=false;};
 },[initial.id,revision,lang,guest]);
 return <Dialog title={lessonTitle(lesson,lang)} onClose={onClose} wide>
  <div className="lesson-detail">
   <div className="badge-row"><Badge tone={lesson.kind}>{t(lesson.kind)}</Badge><Badge tone={lesson.status==='cancelled'?'red':lesson.status==='pending'?'amber':'green'}>{t(lesson.status)}</Badge><Badge>{t(lesson.mode)}</Badge>{lesson.demo&&<Badge tone="amber">{t('demo')}</Badge>}</div>
   <div className="detail-facts"><span><CalendarDays size={19}/>{format(fromIso(lesson.date),lang,{weekday:'long',day:'numeric',month:'long'})}</span><span><Clock size={19}/>{lesson.start}–{lesson.end}</span><span><MapPin size={19}/>{lesson.room||'—'}</span>{!guest&&<span><UserRound size={19}/>{lesson.teacher||'—'}</span>}<span><Users size={19}/>{lesson.subgroup?t('subgroup')+' '+lesson.subgroup:t('allGroups')}</span></div>
   {!guest&&lesson.note&&<p className="lesson-note">{lesson.note}</p>}{error&&<Notice error>{error}</Notice>}
   {guest?<><Notice>{t('guestHint')}</Notice><a className="button primary" href="#today">{t('login')}</a></>:<div className="button-row">
    {lesson.subject_key&&<a className="button secondary" href={'#subject/'+encodeURIComponent(lesson.subject_key)} onClick={onClose}><BookOpen size={17}/>{t('openSubject')}</a>}
    {lesson.meeting_url&&lesson.status!=='cancelled'&&<a className="button secondary" href={lesson.meeting_url} target="_blank" rel="noreferrer">{t('externalMeeting')}<ExternalLink size={17}/></a>}
    {user.role!=='student'&&<Button variant="secondary" onClick={()=>setEdit(true)}><Settings2 size={17}/>{t('editLesson')}</Button>}
    {(user.role==='admin'||user.role==='head')&&<Button variant="ghost" onClick={()=>setRemove(true)}><Trash2 size={17}/>{t('delete')}</Button>}
   </div>}
  </div>
  {edit&&<Suspense fallback={<Loading/>}><LessonForm lesson={lesson} onClose={()=>setEdit(false)} onSave={refresh}/></Suspense>}
  {remove&&<Confirm title={t('delete')} description={t('deleteLessonConfirm')} onClose={()=>setRemove(false)} onConfirm={async()=>{await api(`/api/schedule/occurrences/${lesson.id}?revision=${lesson.revision}`,'DELETE');refresh();onClose();}}/>}
 </Dialog>;
}
