import {useEffect,useState} from 'react';
import {api} from './api';
import {addDays,dateInZone,iso} from './date';
import {Button,Field,Notice,useApp,useJob,useT} from './ui';
type OfflineStatus={ok:boolean;saved_at:string|null};
async function offlineMessage(type:string,extra:Record<string,unknown>={}):Promise<OfflineStatus>{
 if(!('serviceWorker' in navigator)||!isSecureContext)throw {code:'network'};
 await navigator.serviceWorker.register('/sw.js',{scope:'/'});
 const registration=await Promise.race([navigator.serviceWorker.ready,new Promise<never>((_,reject)=>setTimeout(()=>reject({code:'network'}),10000))]);
 return new Promise((resolve,reject)=>{const channel=new MessageChannel();const timer=setTimeout(()=>{channel.port1.close();reject({code:'network'});},15000);channel.port1.onmessage=e=>{clearTimeout(timer);channel.port1.close();if(e.data.ok)resolve(e.data);else reject({code:'network'});};registration.active?.postMessage({type,...extra},[channel.port2]);});
}
export default function CalendarTools(){
 const t=useT();const {lang,settings,notify}=useApp();const job=useJob();const [subgroup,setSubgroup]=useState(0);const [saved,setSaved]=useState<string|null>(null);
 const supported='serviceWorker' in navigator&&isSecureContext;
 const link=new URL('/api/schedule/guest/calendar.ics?'+new URLSearchParams({lang,subgroup:String(subgroup)}),location.origin).href;
 useEffect(()=>{let active=true;if(supported)void offlineMessage('offline-status').then(r=>active&&setSaved(r.saved_at)).catch(()=>{});return()=>{active=false;};},[supported]);
 return <><section className="panel"><h2>{t('calendarSubscription')}</h2><p className="muted">{t('subscriptionHint')}</p><Field label={t('subgroup')}><select value={subgroup} onChange={e=>setSubgroup(+e.target.value)}><option value={0}>{t('allGroups')}</option><option value={1}>1</option><option value={2}>2</option></select></Field><Field label={t('subscriptionUrl')}><input className="subscription-url" readOnly value={link} onFocus={e=>e.target.select()}/></Field><Button variant="secondary" onClick={async()=>{try{await navigator.clipboard.writeText(link);notify(t('copied'));}catch{document.querySelector<HTMLInputElement>('.subscription-url')?.select();}}}>{t('copy')}</Button></section><section className="panel"><h2>{t('offlineCopy')}</h2><p className="muted">{t('offlineHint')}</p>{!supported?<Notice>{t('offlineUnavailable')}</Notice>:<><p>{saved?`${t('offlineSavedAt')}: ${new Date(saved).toLocaleString(lang==='ru'?'ru-RU':'en-GB')}`:''}</p><div className="button-row offline-actions"><Button busy={job.busy} onClick={()=>void job.run(async()=>{const today=dateInZone(settings.timezone);await api('/api/schedule/guest/settings');const result=await offlineMessage('offline-save',{start:iso(today),end:iso(addDays(today,27)),subgroup});setSaved(result.saved_at);},t('offlineSaved'))}>{t('saveOffline')}</Button>{saved&&<><a className="button secondary" href="/offline.html">{t('openOffline')}</a><Button variant="ghost" busy={job.busy} onClick={()=>void job.run(async()=>{await offlineMessage('offline-delete');setSaved(null);})}>{t('deleteOffline')}</Button></>}</div></>}{job.error&&<Notice error>{job.error}</Notice>}</section></>;
}
