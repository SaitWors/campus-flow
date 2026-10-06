import {lazy,Suspense,useCallback,useEffect,useMemo,useState} from 'react';
import {Bell,BookOpen,CalendarDays,Circle,ClipboardList,Eclipse,GraduationCap,House,LogOut,MessageCircle,Monitor,Moon,MoreHorizontal,Settings2,ShieldCheck,Sun} from 'lucide-react';
import {api,setCsrf} from './api';
import {nextTheme,parseTheme,resolveTheme} from './theme';
import {copy,errorText,memberRoles} from './i18n';
import {format,fromIso} from './date';
import type {Lang,Settings,Theme,User} from './types';
import {AppContext,Badge,Button,Dialog,IconButton,Loading,Notice,Toast,useApp,useT} from './ui';
import type {Session} from './Auth';
import {NotificationProvider,NotificationBell,NotificationInbox} from './Notifications';
import {clearDevicePush} from './pushDevice';
import {transition} from './motion';

const Auth=lazy(()=>import('./Auth'));
const Today=lazy(()=>import('./Today'));
const Schedule=lazy(()=>import('./Schedule'));
const Assignments=lazy(()=>import('./Assignments'));
const Subjects=lazy(()=>import('./Subjects'));
const Admin=lazy(()=>import('./Admin'));
const Questions=lazy(()=>import('./Questions'));
const Preferences=lazy(()=>import('./Preferences'));

const defaults:Settings={group:'БВТ2302',program:'09.03.01',course:4,semester_start:'2026-09-01',semester_end:'2027-01-31',anchor_monday:'2026-08-31',anchor_parity:'odd',timezone:'Europe/Moscow',revision:1,configured:false};
function preference(key:string,fallback:string){try{return localStorage.getItem(key)||fallback;}catch{return fallback;}}
function savePreference(key:string,value:string){try{localStorage.setItem(key,value);}catch{/* Device preferences are optional. */}}
function route(){const value=location.hash.slice(1);if(['today','schedule','calendar','assignments','subjects','admin','preferences','notifications','questions','guest','guest-calendar'].includes(value))return value;if(/^subject\/[A-Za-z0-9._%~-]+$/.test(value)){try{decodeURIComponent(value.slice(8));return value;}catch{/* Invalid fragments return to Today. */}}return 'today';}

export default function App(){
 const [lang,setLang]=useState<Lang>(preference('cf-language','ru')==='en'?'en':'ru');const [theme,setTheme]=useState<Theme>(()=>parseTheme(preference('cf-theme','system')));
 const [user,setUser]=useState<User|null>(null);const [settings,setSettings]=useState(defaults);const [revision,setRevision]=useState(0);const [ready,setReady]=useState(false);const [setup,setSetup]=useState(false);const [error,setError]=useState('');const [toast,setToast]=useState<{message:string;error:boolean}|null>(null);const [page,setPage]=useState(route);
 const guest=!user&&(page==='guest'||page==='guest-calendar');
 const refresh=useCallback(()=>setRevision(v=>v+1),[]);const notify=useCallback((message:string,error=false)=>setToast({message,error}),[]);const closeToast=useCallback(()=>setToast(null),[]);
 const onSession=useCallback((session:Session)=>{setCsrf(session.csrf);setUser(session.user);setSetup(false);refresh();window.dispatchEvent(new Event('campus-session-changed'));if(location.hash.startsWith('#guest'))location.hash='today';},[refresh]);
 useEffect(()=>{document.documentElement.lang=lang;savePreference('cf-language',lang);},[lang]);
 useEffect(()=>{savePreference('cf-theme',theme);const query=matchMedia('(prefers-color-scheme: dark)');const apply=()=>document.documentElement.dataset.theme=resolveTheme(theme,query.matches);apply();query.addEventListener('change',apply);return()=>query.removeEventListener('change',apply);},[theme]);
 useEffect(()=>{const listener=()=>transition(()=>setPage(route()));window.addEventListener('hashchange',listener);return()=>window.removeEventListener('hashchange',listener);},[]);
 useEffect(()=>{let active=true;async function load(){try{const status=await api<{needs_setup:boolean}>('/api/auth/status');if(!active)return;setSetup(status.needs_setup);let session:Session|null=null;try{session=await api<Session>('/api/auth/me');}catch(e){if(!['unauthorized','account_inactive'].includes((e as {code:string}).code))throw e;}if(!active)return;setCsrf(session?.csrf||'');setUser(session?.user||null);if(session||guest){const s=await api<Settings>(guest?'/api/schedule/guest/settings':'/api/schedule/settings');if(active)setSettings(s);}if(active)setError('');}catch(e){if(active)setError(errorText(lang,e));}finally{if(active)setReady(true);}}void load();return()=>{active=false;};},[revision,lang,guest]);
 const context=useMemo(()=>({lang,setLang,theme,setTheme,user:user!,settings,refresh,notify}),[lang,theme,user,settings,refresh,notify]);
 async function logout(){try{await api('/api/auth/logout','POST',{});await clearDevicePush().catch(()=>{});setUser(null);setCsrf('');location.hash='today';}catch(e){notify(errorText(lang,e),true);}}
 return <AppContext.Provider value={context}>{!ready?<Loading/>:error&&!user?<div className="standalone-error"><Notice error>{error}</Notice><Button onClick={refresh}>{copy[lang].retry}</Button></div>:!user?<Suspense fallback={<Loading/>}>{guest?<Guest calendar={page==='guest-calendar'} revision={revision}/>:<Auth key={String(setup)} setup={setup} onSession={onSession} onGuest={()=>{location.hash='guest';}}/>}</Suspense>:<NotificationProvider key={user.id}><div className="app-shell"><a className="skip-link" href="#main" onClick={event=>{event.preventDefault();document.getElementById('main')?.focus();}}>{copy[lang].skipLink}</a><Sidebar page={page} onLogout={logout}/><div className="workspace"><header className="topbar"><div className="workspace-label"><GraduationCap size={19}/><span>МТУСИ</span><span className="top-divider"/>{settings.group}<Badge>{lang==='ru'?settings.course+' курс':'Year '+settings.course}</Badge></div><div className="topbar-actions"><NotificationBell/><button className="language-button" onClick={()=>setLang(lang==='ru'?'en':'ru')} aria-label={copy[lang].language}>{lang.toUpperCase()}</button><IconButton label={copy[lang].appearance+': '+copy[lang][theme]} onClick={()=>setTheme(nextTheme(theme))}>{theme==='system'?<Monitor size={19}/>:theme==='dark'?<Moon size={19}/>:theme==='black'?<Eclipse size={19}/>:theme==='ultra-black'?<Circle size={19}/>:<Sun size={19}/>}</IconButton><a className="avatar small" title={user.name} aria-label={copy[lang].account} href="#preferences">{initials(user.name)}</a></div></header><main id="main" tabIndex={-1} className="main-content">{error&&<Notice error>{error}<button onClick={refresh}>{copy[lang].retry}</button></Notice>}<Suspense fallback={<Loading/>}><Page page={page} revision={revision} onSession={onSession}/></Suspense></main><footer className="app-footer"><span>{copy[lang].notOfficial}</span><span>{settings.timezone} · v1.1.0</span></footer></div><BottomNav page={page} onLogout={logout}/></div></NotificationProvider>}{toast&&<Toast {...toast} onClose={closeToast}/>}</AppContext.Provider>;
}

function Page({page,revision,onSession}:{page:string;revision:number;onSession:(session:Session)=>void}){
 const {user}=useApp();
 if(page==='admin'&&user.role!=='student')return <Admin revision={revision}/>;
 if(page==='questions')return <Questions/>;
 if(page==='notifications')return <NotificationInbox/>;
 if(page==='preferences')return <Preferences onSession={onSession}/>;
 if(page==='assignments')return <Assignments revision={revision}/>;
 if(page==='subjects'||page.startsWith('subject/'))return <Subjects revision={revision} subjectKey={page.startsWith('subject/')?decodeURIComponent(page.slice(8)):undefined}/>;
 if(page==='schedule'||page==='calendar')return <Schedule calendar={page==='calendar'} revision={revision}/>;
 return <Today revision={revision}/>;
}
function initials(name:string){return name.split(' ').map(part=>part[0]).slice(0,2).join('');}
const primaryNav=[{id:'today',Icon:House},{id:'schedule',Icon:CalendarDays},{id:'assignments',Icon:ClipboardList},{id:'subjects',Icon:BookOpen}];
function activePage(page:string){return page.startsWith('subject/')?'subjects':page==='calendar'?'schedule':page;}
function Sidebar({page,onLogout}:{page:string;onLogout:()=>void}){
 const t=useT();const {user,lang,settings}=useApp();const active=activePage(page);
 const nav=[...primaryNav,{id:'questions',Icon:MessageCircle},...(user.role==='student'?[]:[{id:'admin',Icon:ShieldCheck}]),{id:'preferences',Icon:Settings2}];
 return <aside className="sidebar"><a className="brand" href="#today"><span className="brand-mark"><CalendarDays size={24}/></span><span>campus<span className="brand-light">flow</span><small>{settings.group}</small></span></a><p className="nav-label">{t('workspace')}</p><nav aria-label={t('navigation')}>{nav.map(({id,Icon})=><a key={id} aria-label={t(id)} href={'#'+id} className={active===id?'active':''} aria-current={active===id?'page':undefined}><Icon size={20}/><span>{t(id)}</span>{active===id&&<span className="nav-active-mark"/>}</a>)}</nav><div className="sidebar-bottom"><div className="semester-rail"><span>{t('semester')}</span><strong>{format(fromIso(settings.semester_start),lang,{month:'short',year:'numeric'})}</strong><small>{t('to')} {format(fromIso(settings.semester_end),lang,{day:'numeric',month:'short'})}</small></div><div className="sidebar-user"><div className="avatar">{initials(user.name)}</div><div><strong>{user.name}</strong><small>{memberRoles(lang,user)}</small></div><IconButton label={t('logout')} onClick={onLogout}><LogOut size={18}/></IconButton></div></div></aside>;
}
function BottomNav({page,onLogout}:{page:string;onLogout:()=>void}){
 const t=useT();const {user}=useApp();const active=activePage(page);const [more,setMore]=useState(false);const secondary=[{id:'questions',Icon:MessageCircle},{id:'notifications',Icon:Bell},...(user.role==='student'?[]:[{id:'admin',Icon:ShieldCheck}]),{id:'preferences',Icon:Settings2}];
 return <><nav className="bottom-nav" aria-label={t('navigation')}>{primaryNav.map(({id,Icon})=><a key={id} href={'#'+id} aria-label={t(id)} aria-current={active===id?'page':undefined} className={active===id?'active':''}><Icon size={20}/><span>{t(id==='assignments'?'workNav':id)}</span></a>)}<button type="button" onClick={()=>setMore(true)} className={!primaryNav.some(item=>item.id===active)?'active':''} aria-expanded={more}><MoreHorizontal size={20}/><span>{t('more')}</span></button></nav>{more&&<Dialog title={t('more')} onClose={()=>setMore(false)}><nav className="more-nav" aria-label={t('more')}>{secondary.map(({id,Icon})=><a key={id} href={'#'+id} onClick={()=>setMore(false)} aria-current={active===id?'page':undefined}><Icon size={21}/>{t(id)}</a>)}</nav><Button variant="ghost" className="full-width more-logout" onClick={()=>{setMore(false);onLogout();}}><LogOut size={18}/>{t('logout')}</Button></Dialog>}</>;
}
function Guest({calendar,revision}:{calendar:boolean;revision:number}){
 const {lang,setLang,theme,setTheme,settings}=useApp();const t=useT();
 return <div className="guest-shell"><header className="topbar"><a className="brand guest-brand" href="#guest"><span className="brand-mark"><CalendarDays size={24}/></span><span>campus<span className="brand-light">flow</span></span></a><div className="topbar-actions"><button className="language-button" onClick={()=>setLang(lang==='ru'?'en':'ru')}>{lang.toUpperCase()}</button><IconButton label={t('appearance')} onClick={()=>setTheme(nextTheme(theme))}><Moon size={19}/></IconButton><a href="#today" className="button primary">{t('login')}</a></div></header><main className="main-content"><div className="guest-notice"><Badge tone="blue">{t('guest')}</Badge><p>{t('guestHint')}</p></div><nav className="tabs" aria-label={t('navigation')}><a href="#guest" aria-current={!calendar?'page':undefined}>{t('schedule')}</a><a href="#guest-calendar" aria-current={calendar?'page':undefined}>{t('calendar')}</a></nav><Schedule calendar={calendar} revision={revision} guest/></main><footer className="app-footer"><span>{t('notOfficial')}</span><span>{settings.timezone}</span></footer></div>;
}
