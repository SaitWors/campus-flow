import {createContext,useCallback,useContext,useEffect,useMemo,useRef,useState,type ReactNode} from 'react';
import {ArrowUpRight,BellRing,CheckCheck,ChevronLeft,ChevronRight,Clock,Megaphone,RefreshCw,Settings2,Smartphone,Trash2,X} from 'lucide-react';
import {api,ApiError,actionKey} from './api';
import {errorText} from './i18n';
import {nt,type NKey} from './notificationCopy';
import {deviceMeta,disablePush,enablePush,reconnectPush,registration,supportIssue,type PushConfig} from './pushDevice';
import {Badge,Button,Confirm,Dialog,Empty,Field,IconButton,Loading,Notice,useApp} from './ui';

export type NotificationItem={
 id:string;category:'schedule'|'queue'|'announcements';title:string;title_en?:string;body:string;body_en?:string;
 route:string;important:boolean;read:boolean;created_at:string;expires_at:string;author?:string;
};
type InboxData={items:NotificationItem[];unread:number;total:number;popups:NotificationItem[];as_of:string};
type InboxContext={data:InboxData;error:string;loading:boolean;reload:()=>void;read:(ids?:string[])=>Promise<void>;
 offset:number;setOffset:(n:number)=>void;unreadOnly:boolean;setUnreadOnly:(v:boolean)=>void};
const empty:InboxData={items:[],unread:0,total:0,popups:[],as_of:''};
const NotificationContext=createContext<InboxContext>(null!);
const useInbox=()=>useContext(NotificationContext);
const useN=()=>{const {lang}=useApp();return (key:NKey)=>nt(lang,key);};
function nError(lang:'ru'|'en',e:unknown){
 const key=({push_permission:'cancelled',push_not_configured:'serverOff',push_device_limit:'deviceLimit',
 push_account_conflict:'accountConflict',validation:'validation'} as Record<string,NKey>)[(e as ApiError).code];
 return key?nt(lang,key):e instanceof ApiError?errorText(lang,e):nt(lang,'deviceError');
}
function content(item:NotificationItem,lang:'ru'|'en',field:'title'|'body'){
 return lang==='en'?(item[field+'_en' as 'title_en'|'body_en']||item[field]):item[field];
}
function routeTo(value:string){location.hash=['#schedule','#queues','#notifications'].includes(value)?value:'#notifications';}

export function NotificationProvider({children}:{children:ReactNode}){
 const {user,lang,refresh}=useApp();const t=useN();
 const [data,setData]=useState(empty),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 const [offset,setOffset]=useState(0),[unreadOnly,setUnreadOnly]=useState(false),[tick,setTick]=useState(0);
 const [prompt,setPrompt]=useState(false),[dismissed,setDismissed]=useState<string[]>([]),[sessionTick,setSessionTick]=useState(0);
 useEffect(()=>{const changed=()=>setSessionTick(v=>v+1);window.addEventListener('campus-session-changed',changed);return()=>window.removeEventListener('campus-session-changed',changed);},[]);
 const reload=useCallback(()=>setTick(v=>v+1),[]);
 useEffect(()=>{let active=true,busy=false;
   async function load(){if(busy)return;busy=true;try{
     const value=await api<InboxData>('/api/notifications/inbox?offset='+offset+'&unread_only='+unreadOnly);
     if(active){setData(value);setError('');}
   }catch(e){if(active){setError(nError(lang,e));if(['unauthorized','account_inactive'].includes((e as ApiError).code)){setData(empty);refresh();}}}
   finally{busy=false;if(active)setLoading(false);}}
   void load();const interval=setInterval(()=>{if(!document.hidden)void load();},6000);
   const focus=()=>{if(!document.hidden)void load();};window.addEventListener('focus',focus);document.addEventListener('visibilitychange',focus);
   const changed=()=>void load();navigator.serviceWorker?.addEventListener('message',changed);
   return()=>{active=false;clearInterval(interval);window.removeEventListener('focus',focus);document.removeEventListener('visibilitychange',focus);navigator.serviceWorker?.removeEventListener('message',changed);};
 },[user.id,offset,unreadOnly,tick,lang,refresh]);
 useEffect(()=>{let active=true;
   if(window.isSecureContext&&'serviceWorker' in navigator)void registration().catch(()=>{});
   void api<PushConfig>('/api/notifications/config').then(async config=>{
     if(!active||!config.enabled)return;
     if(!supportIssue()&&deviceMeta()?.owner===user.id)await reconnectPush(config,user.id);
     let until=0;try{until=Number(localStorage.getItem('cf-push-prompt:'+user.id)||0);}catch{}
     if(active)setPrompt(!deviceMeta()&&until<Date.now());
   }).catch(()=>{});
   return()=>{active=false;};
 },[user.id,sessionTick]);
 const read=useCallback(async(ids?:string[])=>{
   await api('/api/notifications/read','POST',ids?{ids}:{before:data.as_of});
   setDismissed(v=>[...v,...(ids||data.items.map(n=>n.id))]);reload();
   window.dispatchEvent(new Event('campus-notifications-read'));
 },[data.as_of,data.items,reload]);
 useEffect(()=>{const changed=()=>reload();window.addEventListener('campus-notifications-read',changed);
   let channel:BroadcastChannel|undefined;try{channel=new BroadcastChannel('campus-notifications');channel.onmessage=changed;}catch{}
   const broadcast=()=>channel?.postMessage('read');window.addEventListener('campus-notifications-read',broadcast);
   return()=>{channel?.close();window.removeEventListener('campus-notifications-read',changed);window.removeEventListener('campus-notifications-read',broadcast);};
 },[reload]);
 const value=useMemo(()=>({data,error,loading,reload,read,offset,setOffset,unreadOnly,setUnreadOnly}),[data,error,loading,reload,read,offset,unreadOnly]);
 const popup=data.popups.find(n=>!dismissed.includes(n.id));
 function dismissPrompt(){setPrompt(false);try{localStorage.setItem('cf-push-prompt:'+user.id,String(Date.now()+7*86400000));}catch{}}
 return <NotificationContext.Provider value={value}>{children}
 {popup&&<aside className="important-announcement" role="alert" aria-label={t('important')}>
   <div className="announcement-kicker"><Megaphone size={18}/>{t('important')}<span/></div>
   <h2>{content(popup,lang,'title')}</h2><p>{content(popup,lang,'body')}</p>
   {popup.author&&<small>{popup.author}</small>}
   <div className="button-row"><Button onClick={()=>void read([popup.id]).catch(e=>setError(nError(lang,e)))}>{t('understood')}</Button><a href="#notifications" onClick={()=>setDismissed(v=>[...v,popup.id])}>{t('history')}</a></div>
 </aside>}
 {!popup&&prompt&&<aside className="push-optin" aria-label={t('push')}><BellRing size={21}/><div><strong>{t('push')}</strong><p>{t('pushHint')}</p><a href="#preferences" onClick={dismissPrompt}>{t('setup')} <ArrowUpRight size={14}/></a></div><IconButton label={t('later')} onClick={dismissPrompt}><X size={17}/></IconButton></aside>}
 </NotificationContext.Provider>;
}

export function NotificationBell(){
 const t=useN(),{data,setOffset,setUnreadOnly}=useInbox();const [open,setOpen]=useState(false);
 return <><button type="button" className="icon-button notification-trigger" aria-label={t('notifications')+(data.unread?' ('+data.unread+')':'')} title={t('notifications')} aria-haspopup="dialog" aria-expanded={open}
 onClick={()=>{setOffset(0);setUnreadOnly(false);setOpen(true);}}>
 <BellRing size={21} strokeWidth={1.7}/><span className="t-badge" data-open={data.unread>0} aria-hidden="true"><span className="t-badge-dot">{data.unread>99?'99+':data.unread}</span></span>
 </button>{open&&<Dialog title={t('notifications')} onClose={()=>setOpen(false)} wide><NotificationInbox compact onNavigate={()=>setOpen(false)}/></Dialog>}</>;
}

export function NotificationInbox({compact=false,onNavigate}:{compact?:boolean;onNavigate?:()=>void}){
 const t=useN(),{lang}=useApp(),{data,error,loading,reload,read,offset,setOffset,unreadOnly,setUnreadOnly}=useInbox();
 const [localError,setLocalError]=useState(''),[busy,setBusy]=useState(false);
 async function mark(ids?:string[]){setBusy(true);try{await read(ids);setLocalError('');}catch(e){setLocalError(nError(lang,e));}finally{setBusy(false);}}
 return <section className={'notification-inbox '+(compact?'compact':'')}>
 {!compact&&<div className="page-heading"><div><span className="eyebrow">CAMPUS FLOW / INBOX</span><h1>{t('notifications')}</h1><p>{t('inboxHint')}</p></div><BellRing className="heading-icon" size={32}/></div>}
 <div className="notification-toolbar"><div className="tabs"><button className={!unreadOnly?'active':''} onClick={()=>{setUnreadOnly(false);setOffset(0);}}>{t('all')}</button><button className={unreadOnly?'active':''} onClick={()=>{setUnreadOnly(true);setOffset(0);}}>{t('unread')} <span>{data.unread}</span></button></div><div className="button-row"><IconButton label={t('refresh')} onClick={reload}><RefreshCw size={17}/></IconButton><a className="icon-button" aria-label={t('settings')} title={t('settings')} href="#preferences" onClick={onNavigate}><Settings2 size={18}/></a><Button variant="secondary" disabled={!data.unread} busy={busy} onClick={()=>void mark()}><CheckCheck size={17}/>{t('markAll')}</Button></div></div>
 {(error||localError)&&<Notice error>{error||localError}</Notice>}
 {loading?<Loading/>:data.items.length===0?<Empty title={t('empty')} description={t('emptyHint')}/>:<div className="notification-list">{data.items.map(item=><article className={'notification-card '+(item.read?'read':'unread')} key={item.id}>
 <span className={'notification-symbol '+item.category}>{item.category==='announcements'?<Megaphone size={20}/>:item.category==='queue'?<BellRing size={20}/>:<Clock size={20}/>}</span>
 <div className="notification-copy"><div className="notification-meta"><span>{t(item.category)}</span><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString(lang==='ru'?'ru-RU':'en-GB',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}</time>{!item.read&&<i aria-label={t('unread')}/>}</div>
 <h3>{content(item,lang,'title')}</h3><p>{content(item,lang,'body')}</p>{item.author&&<small>{item.author}</small>}
 <div className="button-row">{item.route!=='#notifications'&&<Button variant="ghost" onClick={()=>{void mark([item.id]);routeTo(item.route);onNavigate?.();}}>{t('open')}<ArrowUpRight size={15}/></Button>}{!item.read&&<Button variant="ghost" busy={busy} onClick={()=>void mark([item.id])}><CheckCheck size={15}/>{t('markRead')}</Button>}</div></div>
 </article>)}</div>}
 {data.total>30&&<div className="notification-pagination"><Button variant="secondary" disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-30))}><ChevronLeft size={17}/>{t('previous')}</Button><span>{offset+1}–{Math.min(data.total,offset+30)} / {data.total}</span><Button variant="secondary" disabled={offset+30>=data.total} onClick={()=>setOffset(offset+30)}>{t('next')}<ChevronRight size={17}/></Button></div>}
 </section>;
}

type Preferences={revision:number;schedule:boolean;queue:boolean;announcements:boolean;important_popups:boolean;show_details:boolean;quiet_enabled:boolean;quiet_start:string;quiet_end:string;timezone:string;language:'ru'|'en'};
type Device={id:string;label:string;updated_at:string};
export function NotificationSettings(){
 const t=useN(),{user,lang,notify}=useApp(),{reload}=useInbox();
 const [pref,setPref]=useState<Preferences|null>(null),[config,setConfig]=useState<PushConfig|null>(null),[devices,setDevices]=useState<Device[]>([]);
 const [connected,setConnected]=useState(false),[label,setLabel]=useState(()=>/Android|iPhone|iPad/.test(navigator.userAgent)?'Phone':'Computer');
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[attempt,setAttempt]=useState(0);
 const issue=supportIssue();
 useEffect(()=>{let active=true;void Promise.all([api<Preferences>('/api/notifications/preferences'),api<PushConfig>('/api/notifications/config'),api<Device[]>('/api/notifications/subscriptions')]).then(async([p,c,d])=>{
   if(!active)return;setPref(p);setConfig(c);setDevices(d);
   const meta=deviceMeta();setConnected(Boolean(meta?.owner===user.id&&meta.key===c.public_key&&d.some(x=>x.id===meta.id)&&!supportIssue()&&Notification.permission==='granted'));setError('');
 }).catch(e=>active&&setError(nError(lang,e)));return()=>{active=false;};},[user.id,attempt,lang]);
 async function run(job:()=>Promise<unknown>,message?:string){setBusy(true);setError('');try{await job();if(message)notify(message);}catch(e){setError(nError(lang,e));}finally{setBusy(false);}}
 async function refreshDevices(){setDevices(await api<Device[]>('/api/notifications/subscriptions'));reload();}
 const bool=(key:'schedule'|'queue'|'announcements'|'important_popups'|'show_details'|'quiet_enabled',text:NKey)=><label className="notification-toggle"><span>{t(text)}</span><input type="checkbox" checked={pref?.[key]||false} onChange={e=>setPref(p=>p&&({...p,[key]:e.target.checked}))}/></label>;
 return <section className="panel notification-settings" id="notification-settings"><div className="section-title"><div><h2>{t('settings')}</h2><p className="muted">{t('preferencesHint')}</p></div><BellRing size={24}/></div>
 {error&&<Notice error>{error}<button className="text-link" onClick={()=>setAttempt(n=>n+1)}>{t('retry')}</button></Notice>}
 {!pref||!config?<Loading/>:<>
 <div className="push-device-card"><div className="section-title"><h3><Smartphone size={19}/> {t('push')}</h3><Badge tone={connected?'green':'neutral'}>{t(connected?'enabled':'disabled')}</Badge></div><p>{t('pushHint')}</p>
 {issue&&<Notice>{t(issue)}</Notice>}{!config.enabled&&<Notice>{t('serverOff')}</Notice>}
 {!connected&&<Field label={t('deviceName')}><input maxLength={60} value={label} onChange={e=>setLabel(e.target.value)}/></Field>}
 <div className="button-row">{connected?<Button variant="secondary" busy={busy} onClick={()=>void run(async()=>{await disablePush(user.id);setConnected(false);await refreshDevices();})}>{t('disable')}</Button>:<Button disabled={Boolean(issue)||!config.enabled||!label.trim()} busy={busy} onClick={()=>void run(async()=>{await enablePush(config,user.id,label);setConnected(true);await refreshDevices();})}><BellRing size={17}/>{t('enable')}</Button>}
 <Button variant="secondary" busy={busy} disabled={!connected} onClick={()=>void run(async()=>{await api('/api/notifications/test','POST',{});reload();},t('testSent'))}>{t('test')}</Button></div></div>
 <form onSubmit={e=>{e.preventDefault();void run(async()=>{setPref(await api<Preferences>('/api/notifications/preferences','PUT',pref));reload();},t('saved'));}}>
 <h3>{t('categories')}</h3>{bool('schedule','schedule')}{bool('queue','queue')}{bool('announcements','announcements')}
 <div className="notification-setting-section">{bool('important_popups','popups')}{bool('show_details','details')}<p className="field-hint">{t('detailsHint')}</p></div>
 <div className="notification-setting-section">{bool('quiet_enabled','quiet')}<p className="field-hint">{t('quietHint')}</p>{pref.quiet_enabled&&<div className="form-grid"><Field label={t('from')}><input type="time" required value={pref.quiet_start} onChange={e=>setPref({...pref,quiet_start:e.target.value})}/></Field><Field label={t('until')}><input type="time" required value={pref.quiet_end} onChange={e=>setPref({...pref,quiet_end:e.target.value})}/></Field></div>}</div>
 <div className="form-grid"><Field label={t('timezone')}><select value={pref.timezone} onChange={e=>setPref({...pref,timezone:e.target.value})}>{Array.from(new Set([pref.timezone,'Europe/Moscow','Europe/Kaliningrad','Europe/Samara','Asia/Yekaterinburg','UTC'])).map(z=><option key={z}>{z}</option>)}</select></Field><Field label={t('language')}><select value={pref.language} onChange={e=>setPref({...pref,language:e.target.value as 'ru'|'en'})}><option value="ru">Русский</option><option value="en">English</option></select></Field></div>
 <Button type="submit" busy={busy}>{t('save')}</Button></form>
 <div className="notification-setting-section"><h3>{t('devices')}</h3><p className="field-hint">{t('devicesHint')}</p>{!devices.length?<p className="muted">{t('noDevices')}</p>:devices.map(d=><div className="push-device-row" key={d.id}><Smartphone size={19}/><div><strong>{d.label}</strong><small>{new Date(d.updated_at).toLocaleDateString(lang==='ru'?'ru-RU':'en-GB')}</small></div><Button variant="ghost" busy={busy} onClick={()=>void run(async()=>{if(d.id===deviceMeta()?.id){await disablePush(user.id);setConnected(false);}else await api('/api/notifications/subscriptions/'+d.id,'DELETE');await refreshDevices();})}>{t('remove')}</Button></div>)}</div>
 <div className="install-hint"><Smartphone size={25}/><div><h3>{t('install')}</h3><p>{t('installHint')}</p></div></div>
 </>}</section>;
}

type Announcement={id:string;title:string;body:string;title_en:string;body_en:string;audience:string;important:boolean;hours:number;author:string;expires_at:string;withdrawn:boolean};
const newAnnouncement={title:'',body:'',title_en:'',body_en:'',audience:'all',important:true,hours:24};
export function Announcements(){
 const t=useN(),{lang,notify}=useApp(),{reload}=useInbox();
 const [draft,setDraft]=useState(newAnnouncement),[items,setItems]=useState<Announcement[]>([]),[preview,setPreview]=useState(false),[withdrawing,setWithdrawing]=useState<Announcement|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[tick,setTick]=useState(0);
 const requestKey=useRef(actionKey());
 useEffect(()=>{requestKey.current=actionKey();},[draft]);
 useEffect(()=>{let active=true;void api<Announcement[]>('/api/notifications/announcements').then(d=>{if(active)setItems(d);}).catch(e=>active&&setError(nError(lang,e)));return()=>{active=false;};},[tick,lang]);
 async function publish(){setBusy(true);setError('');try{
   await api('/api/notifications/announcements','POST',draft,{'Idempotency-Key':requestKey.current});
   setDraft(newAnnouncement);setPreview(false);setTick(v=>v+1);reload();notify(t('published'));
 }catch(e){setError(nError(lang,e));}finally{setBusy(false);}}
 return <div className="announcements-layout"><section className="panel"><div className="section-title"><h2>{t('publish')}</h2><Megaphone size={24}/></div><p className="muted">{t('publishHint')}</p>
 <form onSubmit={e=>{e.preventDefault();setPreview(true);}}>
 <Field label={t('title')}><input required minLength={2} maxLength={120} value={draft.title} onChange={e=>setDraft({...draft,title:e.target.value})}/></Field>
 <Field label={t('body')}><textarea required minLength={2} maxLength={2000} rows={5} value={draft.body} onChange={e=>setDraft({...draft,body:e.target.value})}/></Field>
 <details className="translation-options"><summary>English</summary><Field label={t('titleEn')} hint={t('optional')}><input maxLength={120} value={draft.title_en} onChange={e=>setDraft({...draft,title_en:e.target.value})}/></Field><Field label={t('bodyEn')}><textarea maxLength={2000} value={draft.body_en} onChange={e=>setDraft({...draft,body_en:e.target.value})}/></Field></details>
 <div className="form-grid"><Field label={t('audience')}><select value={draft.audience} onChange={e=>setDraft({...draft,audience:e.target.value})}>{(['all','managers','subgroup1','subgroup2'] as const).map(a=><option key={a} value={a}>{t(a==='all'?'everyone':a)}</option>)}</select></Field><Field label={t('duration')}><input type="number" min={1} max={168} required value={draft.hours} onChange={e=>setDraft({...draft,hours:+e.target.value})}/></Field></div>
 <label className="notification-toggle"><span>{t('importantToggle')}</span><input type="checkbox" checked={draft.important} onChange={e=>setDraft({...draft,important:e.target.checked})}/></label>
 {error&&<Notice error>{error}</Notice>}<Button type="submit"><Megaphone size={17}/>{t('preview')}</Button></form></section>
 <section className="panel"><h2>{t('recent')}</h2>{!items.length?<Empty title={t('noAnnouncements')}/>:<div className="announcement-history">{items.map(a=><article key={a.id}><div className="section-title"><Badge tone={a.withdrawn?'neutral':a.important?'amber':'blue'}>{t(a.withdrawn?'withdrawn':Date.parse(a.expires_at)<=Date.now()?'expired':'active')}</Badge><small>{a.author}</small></div><h3>{lang==='en'?(a.title_en||a.title):a.title}</h3><p>{lang==='en'?(a.body_en||a.body):a.body}</p><small>{t('until')} {new Date(a.expires_at).toLocaleString(lang==='ru'?'ru-RU':'en-GB')}</small>{!a.withdrawn&&Date.parse(a.expires_at)>Date.now()&&<Button variant="ghost" onClick={()=>setWithdrawing(a)}><Trash2 size={15}/>{t('withdraw')}</Button>}</article>)}</div>}</section>
 {preview&&<Dialog title={t('preview')} onClose={()=>!busy&&setPreview(false)}><div className="announcement-preview"><Badge tone={draft.important?'amber':'blue'}>{t(draft.important?'important':'announcements')}</Badge><h2>{lang==='en'?(draft.title_en||draft.title):draft.title}</h2><p>{lang==='en'?(draft.body_en||draft.body):draft.body}</p><p className="muted">{t(draft.audience==='all'?'everyone':draft.audience as NKey)} · {draft.hours} h</p></div>{error&&<Notice error>{error}</Notice>}<p>{t('publishConfirm')}</p><Button busy={busy} onClick={()=>void publish()}>{t('publish')}</Button></Dialog>}
 {withdrawing&&<Confirm title={t('withdraw')} description={t('withdrawHint')} onClose={()=>setWithdrawing(null)} onConfirm={async()=>{await api('/api/notifications/announcements/'+withdrawing.id,'DELETE');setTick(v=>v+1);reload();}}/>}
 </div>;
}
