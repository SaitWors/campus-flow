import {useCallback,useEffect,useState} from 'react';
import {Bot,KeyRound,LockKeyhole,Play,RefreshCw,ShieldCheck,Square,Trash2} from 'lucide-react';
import {api,ApiError,setCsrf} from './api';
import {Button,Notice,Loading,useApp} from './ui';

type Guard={owner:boolean;mfa_enabled:boolean;unlocked:boolean;runner_ready:boolean};
type ManagedBot={
 id:string;name:string;image:string;desired_state:'running'|'stopped';
 observed_state:'running'|'stopped'|'error';revision:number;observed_revision:number;
 last_error:string;last_logs:string;last_checked:string|null;
};
const emptyForm={name:'',image:'',token:''};
const msg=(error:unknown)=>error instanceof ApiError?error.code:'network';

export default function BotManager(){
 const {lang}=useApp(),ru=lang==='ru';
 const [guard,setGuard]=useState<Guard|null>(null),[bots,setBots]=useState<ManagedBot[]>([]);
 const [code,setCode]=useState(''),[form,setForm]=useState(emptyForm);
 const [editing,setEditing]=useState<string|null>(null),[creating,setCreating]=useState(false);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const refresh=useCallback(async()=>{
  const value=await api<Guard>('/api/auth/bots/guard');
  setGuard(value);
  if(value.unlocked)setBots(await api<ManagedBot[]>('/api/auth/bots'));
  else setBots([]);
 },[]);
 useEffect(()=>{void refresh().catch(e=>setError(msg(e)));},[refresh]);
 async function act(task:()=>Promise<unknown>){
  setBusy(true);setError('');
  try{await task();await refresh();}
  catch(e){setError(msg(e));if((e as ApiError).code==='bot_mfa_step_up_required')setGuard(g=>g?{...g,unlocked:false}:g);}
  finally{setBusy(false);}
 }
 async function unlock(){
  await act(async()=>{
   const result=await api<{csrf:string}>('/api/auth/bots/unlock','POST',{code});
   setCsrf(result.csrf);setCode('');
  });
 }
 async function save(){
  await act(async()=>{
   if(editing){
    await api('/api/auth/bots/'+editing,'PUT',form);
   }else{
    await api('/api/auth/bots','POST',form);
   }
   setForm(emptyForm);setCreating(false);setEditing(null);
  });
 }
 if(!guard)return <section className="panel">{error?<Notice error>{error}</Notice>:<Loading/>}</section>;
 if(!guard.owner)return <Notice error>{ru?'Эта панель доступна исключительно владельцу, создавшему первый аккаунт администратора.':'Only the original setup owner may access this panel.'}</Notice>;
 return <div className="admin-content">
  <section className="panel">
   <div className="section-title"><h2><Bot size={22}/> {ru?'Управление Telegram-ботами':'Telegram Bot Manager'}</h2><ShieldCheck size={22}/></div>
   <p className="muted">{ru?'Загрузка готовых образов GHCR по SHA-256. Код ботов не исполняется внутри Campus Flow.':'Deploy digest-pinned GHCR images. Bot code never runs inside Campus Flow.'}</p>
   {!guard.mfa_enabled?<div>
    <Notice error>{ru?'Сначала включите двухфакторную аутентификацию TOTP в настройках аккаунта.':'Enable TOTP two-factor authentication in account preferences first.'}</Notice>
    <a href="#preferences" className="button secondary"><KeyRound size={16}/>{ru?'Настроить 2FA':'Configure 2FA'}</a>
   </div>:!guard.unlocked?<form onSubmit={e=>{e.preventDefault();void unlock();}}>
    <div className="section-title"><h3><LockKeyhole size={18}/>{ru?'Подтвердите вход':'Verify identity'}</h3></div>
    <p className="muted">{ru?'Введите НОВЫЙ шестизначный код из приложения. Доступ действует 10 минут.':'Enter a fresh six-digit authenticator code. Access expires after 10 minutes.'}</p>
    <div className="form-grid"><label>{ru?'Код 2FA':'2FA code'}<input required pattern="[0-9]{6}" autoComplete="one-time-code" maxLength={6} value={code} onChange={e=>setCode(e.target.value)}/></label></div>
    <div className="button-row"><Button type="submit" busy={busy}><LockKeyhole size={17}/>{ru?'Открыть панель':'Unlock panel'}</Button></div>
   </form>:<>
    {!guard.runner_ready&&<Notice error>{ru?'Локальный исполнитель пока не настроен: управление ботами доступно, но контейнеры не запустятся до установки Bot Runner.':'Host runner is not configured. Bots cannot start until Bot Runner is installed.'}</Notice>}
    <div className="section-toolbar">
     <p className="muted">{ru?'Только владелец · не более 3 ботов · 192 МиБ и 0,35 vCPU на бот':'Owner only · up to 3 bots · 192 MiB and 0.35 vCPU per bot'}</p>
     <div className="button-row"><Button variant="secondary" onClick={()=>void act(async()=>{})}><RefreshCw size={16}/>{ru?'Обновить':'Refresh'}</Button>
      <Button disabled={bots.length>=3} onClick={()=>{setCreating(true);setEditing(null);setForm(emptyForm);}}>{ru?'Добавить бота':'Add bot'}</Button></div>
    </div>
    {bots.length===0&&!creating&&<p className="muted">{ru?'Боты пока не добавлены.':'No bots registered.'}</p>}
    {bots.map(bot=><article key={bot.id} className="template-card">
     <div className="template-info">
      <h3>{bot.name}</h3>
      <p>{ru?'Желаемое':'Desired'}: {bot.desired_state} · {ru?'Фактическое':'Actual'}: {bot.observed_state} · rev {bot.observed_revision}/{bot.revision}</p>
      <small>{bot.image}</small>
      {bot.last_error&&<Notice error>{bot.last_error}</Notice>}
      <details><summary>{ru?'Последние логи':'Recent logs'}</summary><pre className="audit-json">{bot.last_logs||'—'}</pre></details>
     </div>
     <div className="button-row">
      <Button disabled={busy||!guard.runner_ready} variant="secondary" onClick={()=>void act(()=>api('/api/auth/bots/'+bot.id+'/state','POST',{desired_state:bot.desired_state==='running'?'stopped':'running'}))}>{bot.desired_state==='running'?<><Square size={16}/>{ru?'Остановить':'Stop'}</>:<><Play size={16}/>{ru?'Запустить':'Start'}</>}</Button>
      <Button variant="secondary" disabled={busy} onClick={()=>{setEditing(bot.id);setCreating(true);setForm({name:bot.name,image:bot.image,token:''});}}>{ru?'Изменить':'Edit'}</Button>
      <Button variant="ghost" disabled={busy} onClick={()=>{if(window.confirm(ru?'Удалить бота и его контейнер?':'Delete bot and container?'))void act(()=>api('/api/auth/bots/'+bot.id,'DELETE'));}}><Trash2 size={16}/>{ru?'Удалить':'Delete'}</Button>
     </div>
    </article>)}
    {creating&&<form className="panel" onSubmit={e=>{e.preventDefault();void save();}}>
     <h3>{editing?(ru?'Изменить бота':'Edit bot'):(ru?'Новый бот':'New bot')}</h3>
     <div className="form-grid">
      <label>{ru?'Название':'Name'}<input required minLength={2} maxLength={60} value={form.name} onChange={e=>setForm(v=>({...v,name:e.target.value}))}/></label>
      <label>{ru?'Образ GHCR с @sha256':'GHCR image with @sha256'}<input required maxLength={260} placeholder="ghcr.io/saitwors/mybot@sha256:..." value={form.image} onChange={e=>setForm(v=>({...v,image:e.target.value}))}/></label>
      <label>{ru?'Токен BotFather':'BotFather token'}<input type="password" required={!editing} autoComplete="off" maxLength={160} value={form.token} onChange={e=>setForm(v=>({...v,token:e.target.value}))}/></label>
     </div>
     <p className="muted">{ru?'Токен сохраняется в зашифрованном виде и не отображается повторно. При редактировании оставьте пустым, чтобы не менять его.':'Token is encrypted and never shown again. Leave blank while editing to keep it.'}</p>
     <div className="button-row"><Button type="submit" busy={busy}>{ru?'Сохранить':'Save'}</Button><Button variant="secondary" disabled={busy} onClick={()=>{setCreating(false);setEditing(null);setForm(emptyForm);}}>{ru?'Отмена':'Cancel'}</Button></div>
    </form>}
   </>}
   {error&&<Notice error>{error}</Notice>}
  </section>
 </div>;
}
