import type {Kind,Lesson,User} from './types';
export type StudyFilters={subgroup:number;kind:Kind|'all';view:'day'|'week'|'month';compact:boolean;collapsePast:boolean;collapseEmpty:boolean};
type LessonCountdown={value:number;unit:'minutes';remainder:null;remainderUnit:null}|{value:number;unit:'hours';remainder:number;remainderUnit:'minutes'}|{value:number;unit:'days';remainder:number;remainderUnit:'hours'};
export function lessonCountdown(minutes:number):LessonCountdown{
 if(minutes<60)return {value:minutes,unit:'minutes',remainder:null,remainderUnit:null};
 if(minutes<1440)return {value:Math.floor(minutes/60),unit:'hours',remainder:minutes%60,remainderUnit:'minutes'};
 return {value:Math.floor(minutes/1440),unit:'days',remainder:Math.floor(minutes%1440/60),remainderUnit:'hours'};
}
type DeviceStorage=Pick<Storage,'getItem'|'setItem'>;
export function assignmentSubgroup(subgroup:number,user:User){return user.role==='student'&&subgroup!==user.subgroup?0:subgroup;}
export function instantToLocalInput(value:string|null,zone:string){
 if(!value)return '';
 const instant=new Date(value);if(!Number.isFinite(instant.getTime()))return '';
 const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(instant);
 const part=(key:string)=>parts.find(p=>p.type===key)!.value;
 return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}`;
}
export function localInputToInstant(value:string,zone:string):string|null{
 if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))return null;
 const target=Date.parse(value+'Z');if(!Number.isFinite(target))return null;
 let candidate=target;
 for(let attempt=0;attempt<3;attempt++)candidate+=target-Date.parse(instantToLocalInput(new Date(candidate).toISOString(),zone)+'Z');
 const result=new Date(candidate).toISOString();
 return instantToLocalInput(result,zone)===value?result:null;
}
export function studyFilterKey(user:Pick<User,'id'>|null){return `cf-study-filters:${user?'account:'+user.id:'guest'}`;}
export function defaultStudyFilters(user:Pick<User,'subgroup'>|null):StudyFilters{return {subgroup:user&&[1,2].includes(user.subgroup)?user.subgroup:0,kind:'all',view:'week',compact:true,collapsePast:true,collapseEmpty:true};}
export function readStudyFilters(storage:DeviceStorage,user:User|null):StudyFilters{
 const defaults=defaultStudyFilters(user);
 try{
  const saved=JSON.parse(storage.getItem(studyFilterKey(user))||'null');
  if(!saved||typeof saved!=='object'||Array.isArray(saved))return defaults;
  return {
   subgroup:[0,1,2].includes(saved.subgroup)?saved.subgroup:defaults.subgroup,
   kind:['all','lecture','lab','practice'].includes(saved.kind)?saved.kind:defaults.kind,
   view:['day','week','month'].includes(saved.view)?saved.view:defaults.view,
   compact:typeof saved.compact==='boolean'?saved.compact:defaults.compact,
   collapsePast:typeof saved.collapsePast==='boolean'?saved.collapsePast:defaults.collapsePast,
   collapseEmpty:typeof saved.collapseEmpty==='boolean'?saved.collapseEmpty:defaults.collapseEmpty,
  };
 }catch{return defaults;}
}
export function saveStudyFilters(storage:DeviceStorage,user:User|null,filters:StudyFilters){try{storage.setItem(studyFilterKey(user),JSON.stringify(filters));}catch{/* Device preferences are optional. */}}
export function filterLessons(lessons:Lesson[],filters:Pick<StudyFilters,'subgroup'|'kind'>){return lessons.filter(l=>(!filters.subgroup||!l.subgroup||l.subgroup===filters.subgroup)&&(filters.kind==='all'||l.kind===filters.kind));}
export function nextLesson(lessons:Lesson[],now:Date,subgroup:number):{lesson:Lesson;state:'current'|'upcoming';minutes:number}|null{
 const instant=now.getTime();
 const eligible=lessons.filter(l=>l.status!=='cancelled'&&(!subgroup||!l.subgroup||l.subgroup===subgroup)&&Number.isFinite(Date.parse(l.starts_at))&&Date.parse(l.ends_at)>instant).sort((a,b)=>Date.parse(a.starts_at)-Date.parse(b.starts_at));
 const lesson=eligible.find(l=>Date.parse(l.starts_at)<=instant)||eligible[0];
 if(!lesson)return null;
 const state=Date.parse(lesson.starts_at)<=instant?'current':'upcoming';
 return {lesson,state,minutes:Math.ceil((Date.parse(state==='current'?lesson.ends_at:lesson.starts_at)-instant)/60000)};
}
