import {useState} from 'react';
import {ArrowRight,BookOpen,CalendarDays,CheckCircle2,ChevronRight,ClipboardList,Clock,MapPin,RefreshCw,Sun} from 'lucide-react';
import {addDays,dateInZone,format,fromIso,iso,parity} from './date';
import {assignmentSubgroup,filterLessons,lessonCountdown,nextLesson} from './study';
import type {Assignment,Lesson,Subject} from './types';
import {Badge,Empty,IconButton,Loading,Notice,useApp,useT} from './ui';
import {LessonCard,LessonDetail,lessonTitle} from './Lessons';
import AssignmentCard from './AssignmentCard';
import {SubgroupFilter,useResource,useStudyClock,useStudyFilters} from './useStudy';

export default function Today({revision}:{revision:number}){
 const {user,settings,lang}=useApp();const t=useT();const {filters,update}=useStudyFilters();const now=useStudyClock();
 const todayDate=dateInZone(settings.timezone);const today=iso(todayDate);const end=iso(addDays(todayDate,14));
 const lessons=useResource<Lesson[]>(`/api/schedule/occurrences?start=${today}&end=${end}&subgroup=${filters.subgroup}`,revision,15000);
 const assignments=useResource<Assignment[]>(`/api/schedule/assignments?subgroup=${assignmentSubgroup(filters.subgroup,user)}`,revision,60000);
 const subjects=useResource<Subject[]>('/api/schedule/subjects',revision);
 const [selected,setSelected]=useState<Lesson|null>(null);
 const items=lessons.data||[];const daily=filterLessons(items,filters).filter(l=>l.date===today).sort((a,b)=>a.start.localeCompare(b.start));
 const next=nextLesson(items,now,filters.subgroup);const work=assignments.data||[];const unfinished=work.filter(a=>a.progress.status!=='done');
 const countdown=lessonCountdown(next?.minutes||0);
 const deadlines=unfinished.filter(a=>a.due_at).sort((a,b)=>Date.parse(a.due_at!)-Date.parse(b.due_at!)).slice(0,4);
 const replace=(assignment:Assignment)=>assignments.setData(previous=>previous?.map(a=>a.id===assignment.id?assignment:a)||null);
 const refresh=()=>{lessons.reload();assignments.reload();subjects.reload();};
 return <>
  <div className="page-heading today-heading"><div><span className="eyebrow">{format(todayDate,lang,{weekday:'long',day:'numeric',month:'long'})} · {settings.group}</span><h1>{t('today')}</h1><p>{t('greeting')}, {user.name.split(' ')[0]}. {t('todayHint')}</p></div><div className="today-filter"><SubgroupFilter value={filters.subgroup} onChange={subgroup=>update({subgroup})}/><IconButton label={t('refresh')} onClick={refresh}><RefreshCw size={18}/></IconButton></div></div>
  {!settings.configured&&<Notice>{t('needsCalendar')}{user.role!=='student'&&<a href="#admin">{t('setupSchedule')}</a>}</Notice>}
  {items.some(lesson=>lesson.demo)&&<div className="demo-banner"><Badge tone="amber">{t('demo')}</Badge>{t('demoWarning')}</div>}
  <div className="today-overview"><div><CalendarDays size={18}/><strong>{daily.filter(l=>l.status!=='cancelled').length}</strong><span>{t('totalLessons')}</span></div><div><ClipboardList size={18}/><strong>{unfinished.length}</strong><span>{t('unfinished')}</span></div><div><Badge tone="blue">{t(parity(todayDate,settings))}</Badge><span>{settings.timezone}</span></div></div>
  <div className="today-layout"><div className="today-primary">
   {lessons.error&&<Notice error>{lessons.error}<button onClick={lessons.reload}>{t('retry')}</button></Notice>}
   {lessons.loading?<section className="current-lesson empty-next"><Loading/></section>:next?<button type="button" className={'current-lesson '+next.state} onClick={()=>setSelected(next.lesson)}>
    <div className="current-lesson-main"><span className="current-label"><span className="live-dot"/>{t(next.state==='current'?'currentLesson':'nextLesson')}{next.lesson.status==='pending'&&<Badge tone="amber">{t('pending')}</Badge>}</span><h2>{lessonTitle(next.lesson,lang)}</h2><div className="current-facts"><span><Clock size={16}/>{next.lesson.date!==today?format(fromIso(next.lesson.date),lang,{day:'numeric',month:'short'})+' · ':''}{next.lesson.start}–{next.lesson.end}</span><span><MapPin size={16}/>{next.lesson.room||t(next.lesson.mode)}</span>{next.lesson.subgroup>0&&<span>{t('subgroup')} {next.lesson.subgroup}</span>}</div><span className="current-open">{t('details')}<ArrowRight size={17}/></span></div>
    <div className="countdown"><span>{t(next.state==='current'?'untilEnd':'untilStart')}</span><strong>{countdown.value}<small>{t(countdown.unit==='days'?(countdown.value===1?'countdownDay':'countdownDays'):countdown.unit)}</small></strong>{countdown.remainder!==null&&<small>{countdown.remainder} {t(countdown.remainderUnit)}</small>}<ChevronRight size={20}/></div>
   </button>:<section className="current-lesson empty-next"><Sun size={30}/><h2>{t('noUpcomingLesson')}</h2><p>{t('nextTwoWeeks')}</p><a href="#schedule" className="text-link">{t('openSchedule')}<ArrowRight size={16}/></a></section>}
   <section className="today-section"><div className="section-title"><h2><CalendarDays size={20}/>{t('todayLessons')}</h2><a className="text-link" href="#schedule">{t('schedule')}<ArrowRight size={16}/></a></div>{filters.kind!=='all'&&<div className="filter-applied"><Badge tone={filters.kind}>{t(filters.kind)}</Badge><button className="text-link" onClick={()=>update({kind:'all'})}>{t('showAll')}</button></div>}
    {lessons.loading?<Loading/>:daily.length?<div className="today-lessons">{daily.map(l=><LessonCard key={l.id} lesson={l} compact={filters.compact} onClick={()=>setSelected(l)}/>)}</div>:<Empty title={t('freeDay')} description={t('noTodayLessonsHint')}><a className="button secondary" href="#schedule">{t('openSchedule')}</a></Empty>}
   </section>
   <div className="today-shortcuts"><a href="#subjects"><span className="shortcut-icon"><BookOpen size={21}/></span><span><strong>{t('subjects')}</strong><small>{t('subjectHint')}</small></span><ChevronRight size={18}/></a><a href="#assignments"><span className="shortcut-icon"><CheckCircle2 size={21}/></span><span><strong>{t('personalWork')}</strong><small>{t('personalStatus')}</small></span><ChevronRight size={18}/></a></div>
  </div><aside className="today-deadlines"><div className="section-title"><h2><Clock size={20}/>{t('deadlines')}</h2><Badge>{deadlines.length}</Badge></div>{assignments.error&&<Notice error>{assignments.error}<button onClick={assignments.reload}>{t('retry')}</button></Notice>}{assignments.loading?<Loading/>:deadlines.length?<div className="deadline-list">{deadlines.map(a=><AssignmentCard key={a.id} assignment={a} subject={subjects.data?.find(s=>s.key===a.subject_key)} brief onUpdate={replace} onReload={assignments.reload}/>)}</div>:<Empty title={t('noDeadlines')} description={t('noDeadlinesHint')}/>}<a className="button secondary full-width" href="#assignments">{t('openAssignments')}<ArrowRight size={17}/></a><p className="personal-status-note">{t('personalStatuses')}</p></aside></div>
  {selected&&<LessonDetail initial={selected} revision={revision} onClose={()=>setSelected(null)}/>}
 </>;
}
