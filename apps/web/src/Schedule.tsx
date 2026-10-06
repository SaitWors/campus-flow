import {lazy,Suspense,useEffect,useState} from 'react';
import {CalendarDays,ChevronDown,ChevronLeft,ChevronRight,Clock,Download,FlaskConical,Plus,RefreshCw,SlidersHorizontal} from 'lucide-react';
import {copy} from './i18n';
import {addDays,addMonths,dateInZone,format,fromIso,iso,monday,monthGrid,parity} from './date';
import type {Lesson} from './types';
import {Badge,Button,Empty,IconButton,Loading,Notice,useApp,useT} from './ui';
import {filterLessons} from './study';
import {LessonCard,LessonDetail,lessonTitle} from './Lessons';
import {SubgroupFilter,useResource,useStudyFilters} from './useStudy';
const LessonForm=lazy(()=>import('./LessonForm'));

export default function Schedule({calendar=false,revision,guest=false}:{calendar?:boolean;revision:number;guest?:boolean}){
 const t=useT();const {settings,lang,user,refresh}=useApp();const manage=!guest&&user.role!=='student';
 const {filters,update,reset}=useStudyFilters(guest);
 const [date,setDate]=useState(()=>dateInZone(settings.timezone));const [selected,setSelected]=useState<Lesson|null>(null);const [adding,setAdding]=useState(false);
 useEffect(()=>{if(calendar)update({view:'month'});},[calendar,update]);
 const grid=filters.view==='month'?monthGrid(date):filters.view==='day'?[date]:Array.from({length:7},(_,i)=>addDays(monday(date),i));
 const start=iso(grid[0]);const end=iso(grid[grid.length-1]);
 const resource=useResource<Lesson[]>(`/api/schedule/${guest?'guest/':''}occurrences?start=${start}&end=${end}&subgroup=${filters.subgroup}`,revision,15000);
 const lessons=(resource.data||[]).slice().sort((a,b)=>(a.date+a.start).localeCompare(b.date+b.start));
 const filtered=filterLessons(lessons,filters);const today=iso(dateInZone(settings.timezone));const activeLessons=filtered.filter(l=>l.status!=='cancelled');
 const outside=end<settings.semester_start||start>settings.semester_end;
 const move=(direction:number)=>setDate(filters.view==='month'?addMonths(date,direction):addDays(date,direction*(filters.view==='week'?7:1)));
 return <>
  <div className="page-heading"><div><span className="eyebrow">{settings.group} / {settings.program}</span><h1>{t('schedule')}</h1><p>{filters.view==='month'?format(date,lang,{month:'long',year:'numeric'}):filters.view==='day'?format(date,lang,{weekday:'long',day:'numeric',month:'long'}):format(fromIso(start),lang,{day:'numeric',month:'long'})+' — '+format(fromIso(end),lang,{day:'numeric',month:'long',year:'numeric'})}</p></div><div className="heading-actions">{!guest&&<a className="button secondary" title={t('exportHint')} href={`/api/schedule/calendar.ics?start=${start}&end=${end}&lang=${lang}&subgroup=${filters.subgroup}`}><Download size={17}/><span>{t('export')}</span></a>}{manage&&<Button onClick={()=>setAdding(true)}><Plus size={18}/>{t('addLesson')}</Button>}</div></div>
  {!settings.configured&&<Notice>{t('needsCalendar')} {manage&&<a href="#admin">{t('setupSchedule')}</a>}</Notice>}
  {lessons.some(l=>l.demo)&&<div className="demo-banner"><Badge tone="amber">{t('demo')}</Badge>{t('demoWarning')}</div>}
  <section className="schedule-board">
   <div className="schedule-toolbar">
    <div className="date-navigation"><IconButton label={t('prev')} onClick={()=>move(-1)}><ChevronLeft size={20}/></IconButton><Button variant="secondary" onClick={()=>setDate(dateInZone(settings.timezone))}>{t('today')}</Button><IconButton label={t('next')} onClick={()=>move(1)}><ChevronRight size={20}/></IconButton><input aria-label={t('date')} type="date" value={iso(date)} onChange={e=>e.target.value&&setDate(fromIso(e.target.value))}/></div>
    <div className="view-switch" role="group" aria-label={t('calendarViews')}>{(['day','week','month'] as const).map(view=><button type="button" key={view} aria-pressed={filters.view===view} onClick={()=>update({view})}>{t(view)}</button>)}</div>
    <div className="schedule-filters"><SubgroupFilter value={filters.subgroup} onChange={subgroup=>update({subgroup})}/><select aria-label={t('kind')} value={filters.kind} onChange={e=>update({kind:e.target.value as typeof filters.kind})}>{['all','lecture','lab','practice'].map(k=><option key={k} value={k}>{t(k)}</option>)}</select><IconButton label={t('refresh')} onClick={resource.reload}><RefreshCw size={18}/></IconButton></div>
   </div>
   <div className="week-overview"><Badge tone="blue">{filters.view==='month'?t('month'):t(parity(date,settings))}</Badge><span><CalendarDays size={15}/>{activeLessons.length} {t('totalLessons')}</span><span><FlaskConical size={15}/>{activeLessons.filter(l=>l.kind==='lab').length} {t('labsCount')}</span><span className="timezone-note"><Clock size={14}/>{settings.timezone}</span></div>
   <details className="display-options"><summary><SlidersHorizontal size={15}/>{t('viewOptions')}<ChevronDown size={15}/></summary><div className="display-option-fields">{(['compact','collapsePast','collapseEmpty'] as const).map(key=><label key={key} className="check-label"><input type="checkbox" checked={filters[key]} onChange={e=>update({[key]:e.target.checked})}/>{t(key==='compact'?'compactCards':key)}</label>)}<Button variant="ghost" onClick={reset}>{t('resetFilters')}</Button></div><p className="field-hint">{t(guest?'guestFilters':'savedFilters')}</p></details>
   {lang==='en'&&lessons.some(l=>l.translation_pending)&&<p className="translation-note" role="status">{t('translationPending')}</p>}
   {outside&&<Notice>{t('outOfSemester')}</Notice>}
   {resource.error&&<Notice error>{resource.error}<button className="text-link" onClick={resource.reload}>{t('retry')}</button></Notice>}
   {resource.loading?<Loading/>:!resource.data?null:filters.view==='month'?<>
    <div className="month-grid">{copy[lang].weekdayShort.map(d=><div key={d} className="month-weekday">{d}</div>)}{grid.map(d=>{
     const key=iso(d);const dayLessons=filtered.filter(l=>l.date===key);
     return <button type="button" key={key} className={'month-cell '+(d.getMonth()!==date.getMonth()?'other-month ':'')+(key===today?'is-today ':'')+(key===iso(date)?'selected':'')} aria-label={format(d,lang,{dateStyle:'full'})+', '+dayLessons.length+' '+t('totalLessons')} aria-pressed={key===iso(date)} onClick={()=>setDate(d)}><span className="month-date">{d.getDate()}</span><span className="month-parity">{d.getDay()===1?t(parity(d,settings)):''}</span><span className="month-events">{dayLessons.slice(0,2).map(l=><span key={l.id} className={'mini-lesson '+l.kind+(l.status==='cancelled'?' cancelled':'')}>{l.start} {lessonTitle(l,lang)}</span>)}{dayLessons.length>2&&<small>+{dayLessons.length-2}</small>}</span>{dayLessons.length>0&&<span className="mobile-count">{dayLessons.length}</span>}</button>;
    })}</div>
    <div className="selected-day"><div className="section-title"><h3>{format(date,lang,{weekday:'long',day:'numeric',month:'long'})}</h3><Badge>{t(parity(date,settings))}</Badge></div><p className="field-hint">{t('monthHint')}</p><div className="lesson-grid">{filtered.filter(l=>l.date===iso(date)).map(l=><LessonCard key={l.id} lesson={l} compact={filters.compact} onClick={()=>setSelected(l)}/>)}</div>{!filtered.some(l=>l.date===iso(date))&&<p className="quiet-empty">{t('freeDay')}</p>}</div>
   </>:<div className="week-list">{grid.map(d=>{
    const key=iso(d);const items=filtered.filter(l=>l.date===key);const collapse=filters.view!=='day'&&((filters.collapsePast&&key<today)||(filters.collapseEmpty&&!items.length));
    return <details key={`${key}:${collapse}`} open={!collapse} className={'day-section '+(key===today?'is-today ':'')+(key<today?'past-day':'')}>
     <summary className="day-summary"><div className="day-label"><strong>{String(d.getDate()).padStart(2,'0')}</strong><div><span>{copy[lang].weekdays[(d.getDay()+6)%7]}</span><small>{format(d,lang,{month:'short'})}{key===today?' · '+t('today'):''}</small></div></div><span className="day-summary-state">{items.length?items.length+' '+t('totalLessons'):t('freeDay')}{key<today&&items.length?' · '+t('pastDay'):''}<ChevronDown size={16}/></span></summary>
     <div className="day-lessons">{items.length?items.map(l=><LessonCard key={l.id} lesson={l} compact={filters.compact} onClick={()=>setSelected(l)}/>):<div className="free-day">{t('freeDay')}</div>}</div>
    </details>;
   })}{!filtered.length&&<Empty title={t(lessons.length?'noResults':filters.view==='day'?'freeDay':'emptyWeek')} description={t(manage?'templateHint':'emptyHint')}><div className="button-row">{lessons.length>0&&<Button variant="secondary" onClick={reset}>{t('resetFilters')}</Button>}{manage&&<Button onClick={()=>setAdding(true)}><Plus size={17}/>{t('addLesson')}</Button>}</div></Empty>}</div>}
  </section>
  <div className="schedule-legend">{['lecture','lab','practice'].map(k=><span key={k}><span className={'legend-dot '+k}/>{t(k)}</span>)}<span>{t('liveSchedule')}</span></div>
  {selected&&<LessonDetail initial={selected} revision={revision} guest={guest} onClose={()=>setSelected(null)}/>}
  {adding&&<Suspense fallback={<Loading/>}><LessonForm onClose={()=>setAdding(false)} onSave={refresh}/></Suspense>}
 </>;
}
