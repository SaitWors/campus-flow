import {useCallback,useEffect,useState} from 'react';
import {api} from './api';
import {errorText} from './i18n';
import type {User} from './types';
import {defaultStudyFilters,readStudyFilters,saveStudyFilters,type StudyFilters} from './study';
import {useApp,useT} from './ui';

export function useStudyFilters(guest=false){
 const {user}=useApp();const owner:User|null=guest?null:user;
 const [filters,setFilters]=useState<StudyFilters>(()=>{try{return readStudyFilters(localStorage,owner);}catch{return defaultStudyFilters(owner);}});
 useEffect(()=>{try{saveStudyFilters(localStorage,owner,filters);}catch{/* Preferences are optional. */}},[filters,owner]);
 const update=useCallback((values:Partial<StudyFilters>)=>setFilters(f=>({...f,...values})),[]);
 return {filters,update,reset:()=>setFilters(defaultStudyFilters(owner))};
}

export function SubgroupFilter({value,onChange,assignments=false}:{value:number;onChange:(value:number)=>void;assignments?:boolean}){
 const {user}=useApp();const t=useT();
 const student=assignments&&user?.role==='student';
 return <select aria-label={t('subgroup')} value={value} onChange={e=>onChange(Number(e.target.value))}>
  <option value={0}>{t(student?'commonAndMine':'allGroups')}</option>
  {[1,2].filter(s=>!student||s===user.subgroup).map(s=><option key={s} value={s}>{t('subgroup')} {s}{user?.subgroup===s?' · '+t('mySubgroup'):''}</option>)}
 </select>;
}

export function useResource<T>(url:string,revision=0,pollMs=0){
 const {lang}=useApp();const [data,setData]=useState<T|null>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(true);const [attempt,setAttempt]=useState(0);
 const reload=useCallback(()=>setAttempt(v=>v+1),[]);
 useEffect(()=>{
  let active=true,inFlight=false;
  setData(null);setLoading(true);setError('');
  async function load(){
   if(inFlight||document.hidden)return;
   inFlight=true;
   try{const result=await api<T>(url);if(active){setData(result);setError('');}}
   catch(e){if(active)setError(errorText(lang,e));}
   finally{inFlight=false;if(active)setLoading(false);}
  }
  const visible=()=>{if(!document.hidden)void load();};
  void load();document.addEventListener('visibilitychange',visible);
  const timer=pollMs?setInterval(()=>void load(),pollMs):undefined;
  return()=>{active=false;document.removeEventListener('visibilitychange',visible);if(timer)clearInterval(timer);};
 },[url,revision,attempt,lang,pollMs]);
 return {data,setData,error,loading,reload};
}

export function useStudyClock(){
 const [now,setNow]=useState(()=>new Date());
 useEffect(()=>{const tick=()=>{if(!document.hidden)setNow(new Date());};const timer=setInterval(tick,30000);document.addEventListener('visibilitychange',tick);return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',tick);};},[]);
 return now;
}
