import {useState} from 'react';
import {ArrowRight,CalendarDays,CheckCircle2,ShieldCheck} from 'lucide-react';
import {api} from './api';
import type {User} from './types';
import {Button,Field,Notice,useApp,useJob,useT} from './ui';

export type Session={user:User;csrf:string};
export default function Auth({setup,onSession}:{setup:boolean;onSession:(s:Session)=>void}){
 const t=useT();const {lang,setLang}=useApp();const job=useJob();
 const [mode,setMode]=useState<'login'|'register'|'reset'>( 'login');
 const [done,setDone]=useState('');const [name,setName]=useState('');const [email,setEmail]=useState('');const [password,setPassword]=useState('');const [token,setToken]=useState('');const [subgroup,setSubgroup]=useState(1);
 const heading=setup?'setupTitle':mode==='register'?'registerTitle':mode==='reset'?'resetPassword':'loginTitle';
 function change(next:typeof mode){setMode(next);setDone('');setPassword('');setToken('');}
 return <main className="auth-layout"><aside className="auth-brand"><a className="brand" href="#"><span className="brand-mark"><CalendarDays size={25}/></span><span>campus<span className="brand-light">flow</span><small>МТУСИ · БВТ2302</small></span></a><div className="auth-story"><span className="eyebrow">09.03.01 / {t('course')} 4</span><h1>{lang==='ru'?<>Вся неделя.<br/>В одном месте.</>:<>Your whole week.<br/>In one place.</>}</h1><p>{t('authSubtitle')}</p><div className="auth-strip"><CalendarDays size={21}/><span>{t('schedule')}</span><span className="strip-separator"/><ShieldCheck size={21}/><span>{t('queue')}</span></div></div><small>{t('notOfficial')}</small></aside><section className="auth-form-side"><div className="auth-language"><button onClick={()=>setLang(lang==='ru'?'en':'ru')}>{lang==='ru'?'English':'Русский'}</button></div><div className="auth-card"><div className="intro-icon"><ShieldCheck size={25}/></div><h2>{t(heading)}</h2><p className="muted">{t(setup?'setupSubtitle':mode==='register'?'inviteExplain':mode==='reset'?'resetExplain':'authSubtitle')}</p>
 {done?<><div className="success-panel"><CheckCircle2 size={28}/><h3>{t(done==='registered'?'registrationDone':'resetDone')}</h3>{done==='registered'&&<p>{t('registrationHint')}</p>}</div><Button onClick={()=>change('login')}>{t('backLogin')}<ArrowRight size={18}/></Button></>:<form onSubmit={e=>{e.preventDefault();void job.run(async()=>{
  if(setup){onSession(await api<Session>('/api/auth/setup','POST',{email,password,name,setup_key:token}));return;}
  if(mode==='login'){onSession(await api<Session>('/api/auth/login','POST',{email,password}));return;}
  if(mode==='register'){await api('/api/auth/register','POST',{email,password,name,subgroup,invite:token});setDone('registered');}
  else{await api('/api/auth/reset','POST',{password,token});setDone('reset');}
 });}}>
 {(setup||mode==='register')&&<Field label={t('name')}><input autoComplete="name" required minLength={2} maxLength={80} value={name} onChange={e=>setName(e.target.value)}/></Field>}
 {mode!=='reset'&&<Field label={t('email')}><input type="email" autoComplete="username" required maxLength={254} placeholder="student@example.com" value={email} onChange={e=>setEmail(e.target.value)}/></Field>}
 <Field label={t(mode==='reset'?'newPassword':'password')} hint={setup||mode!=='login'?t('passwordHint'):undefined}><input type="password" autoComplete={setup||mode!=='login'?'new-password':'current-password'} required minLength={setup||mode!=='login'?12:1} maxLength={128} value={password} onChange={e=>setPassword(e.target.value)}/></Field>
 {(setup||mode!=='login')&&<Field label={t(setup?'setupKey':mode==='register'?'inviteToken':'resetCode')} hint={setup?t('setupKeyHint'):undefined}><input required autoComplete="off" value={token} onChange={e=>setToken(e.target.value)} maxLength={200}/></Field>}
 {!setup&&mode==='register'&&<Field label={t('subgroup')}><select value={subgroup} onChange={e=>setSubgroup(+e.target.value)}><option value={1}>1</option><option value={2}>2</option></select></Field>}
 {job.error&&<Notice error>{job.error}</Notice>}<Button type="submit" busy={job.busy} className="full-width">{t(setup?'createAdmin':mode==='register'?'register':mode==='reset'?'resetSubmit':'login')}<ArrowRight size={18}/></Button>
 </form>}
 {!setup&&!done&&<div className="auth-links">{mode==='login'?<><button onClick={()=>change('register')}>{t('noAccount')} <strong>{t('register')}</strong></button><button onClick={()=>change('reset')}>{t('forgot')}</button></>:<button onClick={()=>change('login')}>{t('backLogin')}</button>}</div>}
 </div></section></main>;
}
