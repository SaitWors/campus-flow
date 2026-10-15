import {useCallback,useEffect,useRef,useState} from 'react';
import {Activity,ArrowUpRight,Bot,Check,CheckCheck,ChevronRight,Clock3,Copy,Cpu,ExternalLink,FileText,KeyRound,LockKeyhole,MessageCircleMore,Play,Plus,RefreshCw,RotateCw,Search,Send,Server,Settings2,ShieldCheck,Square,Terminal,Trash2} from 'lucide-react';
import {api,ApiError,setCsrf} from './api';
import {Button,Confirm,Dialog,Field,IconButton,Loading,Notice,useApp} from './ui';
import {botError,botStatus,filterBots,statusLabels,type BotGuard,type BotPlatform,type ManagedBot,type StatusFilter} from './bots';
import './bots.css';

const guide='https://github.com/SaitWors/campus-flow/blob/main/docs/BOT_MANAGER_RU.md';
const codeOf=(error:unknown)=>error instanceof ApiError?error.code:'network';
const platformName=(platform:BotPlatform)=>platform==='discord'?'Discord':'Telegram';

function PlatformIcon({platform,size=21}:{platform:BotPlatform;size?:number}){
 return <span className={`bot-platform-icon ${platform}`} aria-hidden="true">{platform==='discord'?<MessageCircleMore size={size}/>:<Send size={size}/>}</span>;
}

export default function BotManager(){
 const {lang,notify}=useApp(),ru=lang==='ru';
 const [guard,setGuard]=useState<BotGuard|null>(null),[bots,setBots]=useState<ManagedBot[]>([]);
 const [error,setError]=useState(''),[busy,setBusy]=useState<string|null>(null),[code,setCode]=useState('');
 const [editor,setEditor]=useState<ManagedBot|'new'|null>(null),[deleting,setDeleting]=useState<ManagedBot|null>(null),[detailsId,setDetailsId]=useState<string|null>(null);
 const [query,setQuery]=useState(''),[platform,setPlatform]=useState<BotPlatform|'all'>('all'),[status,setStatus]=useState<StatusFilter>('all');
 const [now,setNow]=useState(Date.now()),[deadline,setDeadline]=useState<number|null>(null),[offset,setOffset]=useState(0),[updated,setUpdated]=useState<number|null>(null);
 const version=useRef(0),pending=useRef(false),mounted=useRef(true);
 const clearPrivate=useCallback(()=>{
  version.current++;setBots([]);setEditor(null);setDeleting(null);setDetailsId(null);setCode('');setDeadline(null);setUpdated(null);
 },[]);
 const handleError=useCallback((e:unknown)=>{
  const value=codeOf(e);setError(value);
  if(['bot_mfa_step_up_required','bot_mfa_required','bot_owner_only','unauthorized','account_inactive'].includes(value)){
   clearPrivate();setGuard(g=>g?{...g,unlocked:false,...(value==='bot_owner_only'?{owner:false}:{}),...(value==='bot_mfa_required'?{mfa_enabled:false}:{})}:g);
  }
 },[clearPrivate]);
 const refresh=useCallback(async()=>{
  const request=++version.current;
  try{
   const next=await api<BotGuard>('/api/auth/bots/guard');
   const received=Date.now();
   const list=next.unlocked?await api<ManagedBot[]>('/api/auth/bots'):[];
   if(!mounted.current||request!==version.current)return;
   setGuard(next);setBots(list);setError('');setUpdated(Date.now());setNow(Date.now());
   const server=Date.parse(next.server_time),until=next.access_expires_at?Date.parse(next.access_expires_at):NaN;
   setOffset(Number.isFinite(server)?server-received:0);
   setDeadline(next.unlocked&&Number.isFinite(until)?received+Math.max(0,until-server):null);
   if(!next.unlocked)clearPrivate();
  }catch(e){if(mounted.current&&request===version.current)handleError(e);}
 },[clearPrivate,handleError]);
 useEffect(()=>{mounted.current=true;void refresh();return()=>{mounted.current=false;version.current++;};},[refresh]);
 useEffect(()=>{
  if(!guard?.unlocked)return;
  const id=setInterval(()=>{if(document.visibilityState==='visible'&&!pending.current)void refresh();},10000);
  const visible=()=>{if(document.visibilityState==='visible'&&!pending.current)void refresh();};
  document.addEventListener('visibilitychange',visible);
  return()=>{clearInterval(id);document.removeEventListener('visibilitychange',visible);};
 },[guard?.unlocked,refresh]);
 useEffect(()=>{
  if(!guard?.unlocked)return;
  const id=setInterval(()=>{
   const current=Date.now();setNow(current);
   if(deadline&&current>=deadline){clearPrivate();setGuard(g=>g?{...g,unlocked:false}:g);setError('bot_mfa_step_up_required');}
  },1000);
  return()=>clearInterval(id);
 },[deadline,guard?.unlocked,clearPrivate]);
 async function act(key:string,task:()=>Promise<unknown>,success?:string){
  if(pending.current)return;
  pending.current=true;version.current++;setBusy(key);setError('');
  try{await task();if(mounted.current){await refresh();if(success)notify(success);}}
  catch(e){if(mounted.current){handleError(e);throw e;}}
  finally{pending.current=false;if(mounted.current)setBusy(null);}
 }
 const command=(bot:ManagedBot,action:'state'|'restart')=>{
  void act(bot.id,()=>api(`/api/auth/bots/${bot.id}/${action}`,'POST',action==='state'?{desired_state:bot.desired_state==='running'?'stopped':'running'}:{}),ru?'Команда отправлена. Статус обновится после ответа Runner.':'Command sent. Status will update after the runner responds.').catch(()=>{});
 };
 const details=bots.find(bot=>bot.id===detailsId),visible=filterBots(bots,query,platform,status,now+offset);
 const running=bots.filter(bot=>botStatus(bot,now+offset)==='running').length;
 const attention=bots.filter(bot=>['error','stale'].includes(botStatus(bot,now+offset))).length;
 const remaining=deadline?Math.min(600,Math.max(0,Math.ceil((deadline-now)/1000))):0;
 const sessionTime=`${Math.floor(remaining/60)}:${String(remaining%60).padStart(2,'0')}`;
 if(!guard)return <section className="panel bot-loading">{error?<><Notice error>{botError(error,lang)}</Notice><Button variant="secondary" onClick={()=>void refresh()}><RefreshCw size={16}/>{ru?'Повторить':'Try again'}</Button></>:<Loading/>}</section>;
 if(!guard.owner)return <Notice error>{botError('bot_owner_only',lang)}</Notice>;
 return <div className="bot-workspace">
  <header className="bot-hero">
   <div className="bot-hero-copy"><span className="bot-eyebrow"><span/>{ru?'ЛИЧНОЕ ПРОСТРАНСТВО':'PRIVATE WORKSPACE'}</span><h2>{ru?'Центр управления ботами':'Your bots, under control'}</h2><p>{ru?'Telegram и Discord в одном месте. Подключайте своих помощников и следите за их работой.':'Telegram and Discord in one place. Connect your assistants and keep an eye on their activity.'}</p><div className="bot-platform-tags"><span><Send size={14}/>Telegram</span><span><MessageCircleMore size={14}/>Discord</span><span><ShieldCheck size={14}/>{ru?'Только для владельца':'Owner access'}</span></div></div>
   <div className="bot-hero-art" aria-hidden="true"><div className="bot-orbit"/><span className="bot-orbit-center"><Bot size={38}/></span><span className="bot-orbit-telegram"><Send size={22}/></span><span className="bot-orbit-discord"><MessageCircleMore size={25}/></span><span className="bot-orbit-lock"><LockKeyhole size={15}/></span></div>
  </header>
  {!guard.mfa_enabled||!guard.unlocked?<section className="bot-access">
   <div className="bot-access-icon"><LockKeyhole size={28}/></div>
   <div><span className="bot-eyebrow">{ru?'ЗАЩИЩЁННЫЙ ДОСТУП':'PROTECTED ACCESS'}</span><h3>{!guard.mfa_enabled?(ru?'Сначала защитите аккаунт':'Secure your account first'):(ru?'Подтвердите, что это вы':'Verify your identity')}</h3><p>{!guard.mfa_enabled?(ru?'Включите двухфакторную аутентификацию в настройках. После этого вы сможете подключать и запускать ботов.':'Enable two-factor authentication in settings to connect and run your bots.'):(ru?'Введите свежий код из приложения-аутентификатора. Панель откроется на 10 минут.':'Enter a fresh authenticator code. The panel will open for 10 minutes.')}</p>
    {!guard.mfa_enabled?<a href="#preferences" className="button primary"><KeyRound size={16}/>{ru?'Настроить 2FA':'Set up 2FA'}<ChevronRight size={16}/></a>:<form className="bot-unlock-form" onSubmit={e=>{e.preventDefault();void act('unlock',async()=>{const result=await api<{csrf:string}>('/api/auth/bots/unlock','POST',{code});setCsrf(result.csrf);setCode('');}).catch(()=>{});}}><Field label={ru?'Код из приложения':'Authenticator code'}><input autoFocus required inputMode="numeric" pattern="[0-9]{6}" autoComplete="one-time-code" maxLength={6} placeholder="000000" value={code} onChange={e=>setCode(e.target.value.replace(/\D/g,''))}/></Field><Button type="submit" busy={busy==='unlock'} disabled={code.length!==6}><LockKeyhole size={16}/>{ru?'Открыть панель':'Unlock panel'}</Button></form>}
    {error&&<Notice error>{botError(error,lang)}</Notice>}
   </div>
   <div className="bot-access-facts"><span><ShieldCheck size={17}/>{ru?'Токены зашифрованы':'Encrypted tokens'}</span><span><Clock3 size={17}/>{ru?'10 минут на сеанс':'10-minute access'}</span><span><Server size={17}/>{ru?'Отдельные контейнеры':'Separate containers'}</span></div>
  </section>:<>
   <div className="bot-session"><span><ShieldCheck size={16}/>{ru?'Личность подтверждена':'Identity verified'}</span><span className={remaining<60?'bot-session-expiring':''}><Clock3 size={14}/>{ru?'Доступ ещё':'Access remaining'} <strong>{sessionTime}</strong></span><button disabled={!!busy} onClick={()=>void act('lock',async()=>{await api('/api/auth/bots/lock','POST',{});clearPrivate();setGuard(g=>g?{...g,unlocked:false}:g);}).catch(()=>{})}><LockKeyhole size={14}/>{ru?'Закрыть доступ':'Lock panel'}</button></div>
   <div className="bot-metrics">
    <div><span><Bot size={17}/>{ru?'Подключено':'Connected'}</span><strong>{bots.length}<small> / {guard.limits.max_bots}</small></strong><div className="bot-slot-meter" aria-hidden="true">{Array.from({length:guard.limits.max_bots},(_,i)=><i key={i} className={i<bots.length?'filled':''}/>)}</div></div>
    <div><span><Activity size={17}/>{ru?'Работают сейчас':'Confirmed running'}</span><strong>{running.toString().padStart(2,'0')}</strong><small>{ru?'По последнему ответу Runner':'From the latest runner report'}</small></div>
    <div><span><Cpu size={17}/>{ru?'Лимит на бота':'Per-bot limit'}</span><strong>{guard.limits.memory_mb}<small> MiB</small></strong><small>{guard.limits.cpus.toLocaleString(lang)} vCPU · {ru?'Лимит, а не расход':'Allocation cap, not usage'}</small></div>
    <div><span><Server size={17}/>Bot Runner</span><strong className="bot-runner-label">{guard.runner_ready?(ru?'Настроен':'Configured'):(ru?'Не подключён':'Not configured')}</strong><small>{guard.runner_ready?(ru?'Обмен статусами каждые ~20 с':'Reports about every 20 seconds'):(ru?'Нужна настройка на сервере':'Host setup required')}</small></div>
   </div>
   {error&&<Notice error>{botError(error,lang)}</Notice>}
   {!guard.runner_ready&&<Notice>{ru?'Настройте Bot Runner на сервере, чтобы запускать ботов. Добавлять и редактировать настройки можно уже сейчас.':'Set up Bot Runner on the host to run bots. You can add and edit their settings now.'} <a className="text-link" href={guide} target="_blank" rel="noreferrer">{ru?'Инструкция':'Setup guide'}<ArrowUpRight size={14}/></a></Notice>}
   <div className="bot-main-grid">
    <section className="bot-fleet" aria-label={ru?'Мои боты':'My bots'}>
     <div className="bot-section-heading"><div><h3>{ru?'Мои боты':'My bots'}<span>{bots.length.toString().padStart(2,'0')}</span></h3><p>{attention?(ru?`${attention} требуют внимания`:`${attention} need attention`):(ru?'Всё необходимое для ежедневного управления.':'Everything you need for everyday management.')}</p></div><Button disabled={!!busy||bots.length>=guard.limits.max_bots} onClick={()=>setEditor('new')}><Plus size={17}/>{ru?'Добавить бота':'Add bot'}</Button></div>
     <div className="bot-toolbar"><label className="bot-search"><Search size={17}/><input type="search" aria-label={ru?'Найти бота':'Find a bot'} placeholder={ru?'Найти бота…':'Find a bot…'} value={query} onChange={e=>setQuery(e.target.value)}/></label><IconButton label={ru?'Обновить статусы':'Refresh statuses'} disabled={!!busy} onClick={()=>void act('refresh',async()=>{}).catch(()=>{})}><RefreshCw size={17} className={busy==='refresh'?'spin':''}/></IconButton></div>
     <div className="bot-filters"><div className="bot-platform-filters" role="group" aria-label={ru?'Платформа':'Platform'}>{(['all','telegram','discord'] as const).map(value=><button key={value} aria-pressed={platform===value} onClick={()=>setPlatform(value)}>{value==='all'?(ru?'Все':'All'):platformName(value)}</button>)}</div><select aria-label={ru?'Фильтр по статусу':'Filter by status'} value={status} onChange={e=>setStatus(e.target.value as StatusFilter)}><option value="all">{ru?'Все статусы':'All statuses'}</option><option value="running">{ru?'Работают':'Running'}</option><option value="attention">{ru?'Требуют внимания':'Need attention'}</option></select></div>
     {bots.length===0?<div className="bot-empty"><div className="bot-empty-symbol"><Bot size={34}/><span>+</span></div><h4>{ru?'Первый помощник — на подходе':'Make room for your first assistant'}</h4><p>{ru?'Подключите Telegram- или Discord-бота. Его состояние и последние логи появятся здесь.':'Connect a Telegram or Discord bot. Its status and recent logs will appear here.'}</p><Button disabled={!!busy} onClick={()=>setEditor('new')}><Plus size={16}/>{ru?'Подключить первого бота':'Connect your first bot'}</Button><small>{ru?'До 3 лёгких ботов на вашем сервере':'Up to 3 lightweight bots on your host'}</small></div>:visible.length===0?<div className="bot-empty bot-no-results"><Search size={30}/><h4>{ru?'Ничего не найдено':'No matching bots'}</h4><p>{ru?'Попробуйте другой запрос или сбросьте фильтры.':'Try another search or reset the filters.'}</p><Button variant="secondary" onClick={()=>{setQuery('');setPlatform('all');setStatus('all');}}>{ru?'Сбросить фильтры':'Reset filters'}</Button></div>:<div className="bot-card-grid">{visible.map(bot=><BotCard key={bot.id} bot={bot} now={now+offset} busy={busy} runnerReady={guard.runner_ready} onCommand={action=>command(bot,action)} onEdit={()=>setEditor(bot)} onDetails={()=>setDetailsId(bot.id)} onDelete={()=>setDeleting(bot)}/>)}</div>}
     <div className="bot-sync-note"><RefreshCw size={12}/><span>{ru?'Автообновление каждые 10 с':'Refreshes every 10 seconds'}{updated?` · ${ru?'Обновлено':'Updated'} ${new Date(updated).toLocaleTimeString(lang,{hour:'2-digit',minute:'2-digit'})}`:''}</span></div>
    </section>
    <aside className="bot-sidebar"><QuickStart/><section className="bot-side-panel bot-side-security"><ShieldCheck size={23}/><h4>{ru?'Секреты остаются секретами':'Secrets stay secret'}</h4><p>{ru?'Токен хранится зашифрованным и не показывается повторно. Логи очищаются от текущего токена.':'Tokens are encrypted and never shown again. The current token is redacted from logs.'}</p><a href={guide} target="_blank" rel="noreferrer">{ru?'Как устроен запуск':'How deployment works'}<ArrowUpRight size={14}/></a></section></aside>
   </div>
   {editor&&<BotEditor bot={editor==='new'?null:editor} busy={!!busy} onClose={()=>!busy&&setEditor(null)} onSave={async data=>{await act('save',async()=>{await api(editor==='new'?'/api/auth/bots':`/api/auth/bots/${editor.id}`,editor==='new'?'POST':'PUT',data);setEditor(null);},ru?'Настройки бота сохранены.':'Bot settings saved.');}}/>}
   {deleting&&<Confirm title={ru?`Удалить «${deleting.name}»?`:`Delete “${deleting.name}”?`} description={ru?'Runner остановит и удалит контейнер при следующей синхронизации. Настройки и сохранённый токен будут удалены.':'The runner will stop and remove the container on its next sync. Settings and the stored token will be deleted.'} onClose={()=>setDeleting(null)} onConfirm={async()=>{await act('delete',async()=>{await api(`/api/auth/bots/${deleting.id}`,'DELETE');setDeleting(null);},ru?'Бот удалён из панели.':'Bot removed from the panel.');}}/>}
   {details&&<BotDetails bot={details} now={now+offset} onClose={()=>setDetailsId(null)}/>}
  </>}
 </div>;
}

function BotCard({bot,now,busy,runnerReady,onCommand,onEdit,onDetails,onDelete}:{bot:ManagedBot;now:number;busy:string|null;runnerReady:boolean;onCommand:(action:'state'|'restart')=>void;onEdit:()=>void;onDetails:()=>void;onDelete:()=>void}){
 const {lang}=useApp(),ru=lang==='ru',state=botStatus(bot,now),transitioning=['starting','stopping','updating'].includes(state);
 const [repository,digest]=bot.image.split('@sha256:');
 return <article className={`bot-card bot-card-${state}`}>
  <div className="bot-card-head"><PlatformIcon platform={bot.platform}/><div><h4>{bot.name}</h4><span>{platformName(bot.platform)}</span></div><span className={`bot-status ${state}`}><i/>{statusLabels[state][ru?0:1]}</span></div>
  <div className="bot-image-ref"><span>{ru?'ОБРАЗ ПРИЛОЖЕНИЯ':'APPLICATION IMAGE'}</span><code title={bot.image}>{repository.replace('ghcr.io/saitwors/','')}<small>sha256:{digest?.slice(0,12)}…</small></code></div>
  <p className={`bot-card-note ${state==='error'?'is-error':''}`}>{state==='stale'?(ru?'Runner давно не присылал статус. Проверьте службу на сервере.':'No recent runner report. Check the host service.'):state==='error'?(ru?'Откройте логи, чтобы найти причину.':'Open the logs to investigate.'):transitioning?(ru?'Команда ожидает Runner. Обычно от 20 с; загрузка образа дольше.':'Waiting for the runner. Usually 20+ seconds; image downloads take longer.'):state==='waiting'?(ru?'Runner ещё не проверял этого бота.':'The runner has not checked this bot yet.'):state==='running'?(ru?'Контейнер запущен. Подключение к платформе проверяйте в логах.':'Container running. Check logs for platform connectivity.'):(ru?'Бот остановлен. Можно запустить, когда понадобится.':'Bot stopped. Start it whenever you need it.')}</p>
  <div className="bot-card-footer"><Button variant={bot.desired_state==='running'?'secondary':'primary'} busy={busy===bot.id} disabled={!!busy||!runnerReady||(transitioning&&bot.desired_state==='stopped')} onClick={()=>onCommand('state')}>{bot.desired_state==='running'?<Square size={14}/>:<Play size={14}/>} {bot.desired_state==='running'?(ru?'Остановить':'Stop'):(ru?'Запустить':'Start')}</Button><div><IconButton label={ru?`Перезапустить ${bot.name}`:`Restart ${bot.name}`} disabled={!!busy||!runnerReady||transitioning||bot.desired_state!=='running'} onClick={()=>onCommand('restart')}><RotateCw size={16}/></IconButton><IconButton label={ru?`Логи ${bot.name}`:`Logs for ${bot.name}`} onClick={onDetails}><Terminal size={17}/></IconButton><IconButton label={ru?`Настроить ${bot.name}`:`Configure ${bot.name}`} disabled={!!busy} onClick={onEdit}><Settings2 size={16}/></IconButton><IconButton label={ru?`Удалить ${bot.name}`:`Delete ${bot.name}`} disabled={!!busy} onClick={onDelete}><Trash2 size={16}/></IconButton></div></div>
 </article>;
}

function QuickStart(){
 const {lang}=useApp(),ru=lang==='ru';const [platform,setPlatform]=useState<BotPlatform>('telegram');
 return <section className="bot-side-panel bot-quick-start"><span className="bot-eyebrow">{ru?'С ЧЕГО НАЧАТЬ':'GETTING STARTED'}</span><h4>{ru?'От идеи до запуска':'From idea to running'}</h4><div className="bot-guide-switch" role="group" aria-label={ru?'Инструкция для платформы':'Platform guide'}>{(['telegram','discord'] as const).map(value=><button key={value} aria-pressed={platform===value} onClick={()=>setPlatform(value)}>{platformName(value)}</button>)}</div><ol><li><span>01</span><div><strong>{ru?'Создайте бота':'Create your bot'}</strong><p>{platform==='telegram'?(ru?'Получите токен у @BotFather.':'Get a token from @BotFather.'):(ru?'Создайте приложение и получите Bot Token. Пригласите бота на сервер.':'Create an application, get its Bot Token and invite the bot to a server.')}</p><a href={platform==='telegram'?'https://t.me/BotFather':'https://discord.com/developers/applications'} target="_blank" rel="noreferrer">{platform==='telegram'?'BotFather':'Developer Portal'}<ArrowUpRight size={12}/></a></div></li><li><span>02</span><div><strong>{ru?'Подготовьте приложение':'Prepare the application'}</strong><p>{ru?'Опубликуйте свой образ в GHCR и скопируйте полный digest SHA-256.':'Publish your image to GHCR and copy its full SHA-256 digest.'}</p></div></li><li><span>03</span><div><strong>{ru?'Подключите и запустите':'Connect and launch'}</strong><p>{ru?'Добавьте образ и токен в панель. Нажмите «Запустить» и проверьте логи.':'Add the image and token here. Press Start and check the logs.'}</p></div></li></ol><a className="bot-guide-link" href={guide} target="_blank" rel="noreferrer"><FileText size={15}/>{ru?'Полная инструкция':'Full setup guide'}<ArrowUpRight size={14}/></a></section>;
}

type BotForm={name:string;platform:BotPlatform;image:string;token:string};
function BotEditor({bot,busy,onClose,onSave}:{bot:ManagedBot|null;busy:boolean;onClose:()=>void;onSave:(data:BotForm)=>Promise<void>}){
 const {lang}=useApp(),ru=lang==='ru';
 const [form,setForm]=useState<BotForm>({name:bot?.name||'',platform:bot?.platform||'telegram',image:bot?.image||'',token:''}),[error,setError]=useState('');
 const tokenRequired=!bot||bot.platform!==form.platform;
 return <Dialog wide title={bot?(ru?'Настройка бота':'Bot settings'):(ru?'Подключить бота':'Connect a bot')} onClose={onClose}><form className="bot-editor" onSubmit={e=>{e.preventDefault();setError('');void onSave({...form,name:form.name.trim(),image:form.image.trim(),token:form.token.trim()}).catch(e=>setError(codeOf(e)));}}><fieldset disabled={busy}><legend>{ru?'Выберите платформу':'Choose a platform'}</legend><div className="bot-platform-choices">{(['telegram','discord'] as const).map(value=><label key={value} className={form.platform===value?'selected':''}><input type="radio" name="bot-platform" value={value} checked={form.platform===value} onChange={()=>setForm(f=>({...f,platform:value,token:''}))}/><PlatformIcon platform={value}/><span><strong>{platformName(value)}</strong><small>{value==='telegram'?(ru?'Личные чаты и группы':'Direct messages and groups'):(ru?'Серверы и сообщества':'Servers and communities')}</small></span>{form.platform===value&&<Check size={17}/>}</label>)}</div><Field label={ru?'Название бота':'Bot name'} hint={ru?'Любое понятное имя для этой панели.':'A recognizable name for this panel.'}><input autoFocus required minLength={2} maxLength={60} placeholder={ru?'Например, помощник группы':'For example, study group helper'} value={form.name} onChange={e=>setForm(f=>({...f,name:e.target.value}))}/></Field><Field label={ru?'Образ приложения':'Application image'} hint={ru?'Готовый публичный образ из вашего GHCR. Нужен digest, а не тег версии.':'A prebuilt public image from your GHCR. Use a digest instead of a version tag.'}><input required pattern="ghcr\.io/saitwors/[a-z0-9][a-z0-9._\/\-]*@sha256:[a-f0-9]{64}" maxLength={260} spellCheck={false} placeholder="ghcr.io/saitwors/my-bot@sha256:…" value={form.image} onChange={e=>setForm(f=>({...f,image:e.target.value}))}/></Field><Field label={form.platform==='telegram'?(ru?'Токен из BotFather':'BotFather token'):(ru?'Bot Token из Discord':'Discord Bot Token')} hint={bot&&!tokenRequired?(ru?'Оставьте пустым, чтобы сохранить текущий токен.':'Leave blank to keep the current token.'):(ru?'Токен будет зашифрован и не появится в интерфейсе повторно.':'The token will be encrypted and never displayed again.')}><input type="password" autoComplete="new-password" required={tokenRequired} maxLength={200} spellCheck={false} placeholder={bot&&!tokenRequired?(ru?'Текущий токен сохранён':'Current token stored'):(ru?'Вставьте токен':'Paste the token')} value={form.token} onChange={e=>setForm(f=>({...f,token:e.target.value}))}/></Field>{form.platform==='discord'&&<div className="bot-editor-hint"><MessageCircleMore size={19}/><p>{ru?'Для Discord используйте подключение через Gateway. Если бот читает обычные сообщения, настройте Message Content Intent в Developer Portal и в коде.':'Use a Gateway connection for Discord. If your bot reads ordinary messages, configure Message Content Intent in the Developer Portal and in code.'} <a href="https://discord.com/developers/applications" target="_blank" rel="noreferrer">Developer Portal<ExternalLink size={12}/></a></p></div>}<div className="bot-editor-resource"><Cpu size={15}/>{ru?'Лимит контейнера: 192 MiB RAM · 0,35 vCPU. Запись доступна в /tmp.':'Container limit: 192 MiB RAM · 0.35 vCPU. Writes available in /tmp.'}</div></fieldset>{error&&<Notice error>{botError(error,lang)}</Notice>}<div className="form-actions"><Button variant="secondary" disabled={busy} onClick={onClose}>{ru?'Отмена':'Cancel'}</Button><Button type="submit" busy={busy}>{bot?<Check size={16}/>:<Plus size={16}/>} {bot?(ru?'Сохранить изменения':'Save changes'):(ru?'Подключить бота':'Connect bot')}</Button></div></form></Dialog>;
}

function BotDetails({bot,now,onClose}:{bot:ManagedBot;now:number;onClose:()=>void}){
 const {lang}=useApp(),ru=lang==='ru';
 const [tab,setTab]=useState<'logs'|'config'>('logs'),[copied,setCopied]=useState(false),[copyError,setCopyError]=useState(false);
 const timer=useRef<ReturnType<typeof setTimeout>|null>(null);useEffect(()=>()=>{if(timer.current)clearTimeout(timer.current);},[]);
 const state=botStatus(bot,now);
 return <Dialog wide title={bot.name} onClose={onClose}><div className="bot-details-header"><PlatformIcon platform={bot.platform}/><span>{platformName(bot.platform)}</span><span className={`bot-status ${state}`}><i/>{statusLabels[state][ru?0:1]}</span></div><div className="bot-detail-tabs" role="group" aria-label={ru?'Диагностика бота':'Bot diagnostics'}><button aria-pressed={tab==='logs'} onClick={()=>setTab('logs')}><Terminal size={16}/>{ru?'Логи':'Logs'}</button><button aria-pressed={tab==='config'} onClick={()=>setTab('config')}><Settings2 size={16}/>{ru?'Конфигурация':'Configuration'}</button></div>{tab==='logs'?<><div className="bot-log-heading"><span>{ru?'Последние строки вывода':'Recent output'}</span><Button variant="ghost" disabled={!bot.last_logs} onClick={async()=>{try{if(!navigator.clipboard)throw new Error('clipboard unavailable');await navigator.clipboard.writeText(bot.last_logs);setCopied(true);setCopyError(false);if(timer.current)clearTimeout(timer.current);timer.current=setTimeout(()=>setCopied(false),2000);}catch{setCopyError(true);}}}>{copied?<CheckCheck size={15}/>:<Copy size={15}/>} {copied?(ru?'Скопировано':'Copied'):(ru?'Копировать':'Copy')}</Button></div>{bot.last_error&&<Notice error>{bot.last_error}</Notice>}<pre className="bot-log-output" tabIndex={0}>{bot.last_logs||(ru?'Логов пока нет. Запустите бота и дождитесь первого отчёта Runner.':'No logs yet. Start the bot and wait for the first runner report.')}</pre>{copyError&&<Notice error>{ru?'Не удалось скопировать. Выделите и скопируйте текст вручную.':'Could not copy. Select and copy the text manually.'}</Notice>}<p className="bot-log-caption">{ru?'Здесь вывод приложения, а не журнал действий Campus Flow.':'Application output, separate from the Campus Flow audit log.'}</p></>:<dl className="bot-config-list"><div><dt>{ru?'Образ':'Image'}</dt><dd><code>{bot.image}</code></dd></div><div><dt>{ru?'Версия настройки':'Configuration revision'}</dt><dd>{bot.observed_revision} / {bot.revision} {ru?'подтверждено':'confirmed'}</dd></div><div><dt>{ru?'Последний отчёт':'Latest report'}</dt><dd>{bot.last_checked?new Date(bot.last_checked).toLocaleString(lang):(ru?'Ещё не получен':'Not received yet')}</dd></div><div><dt>{ru?'Ресурсы':'Resources'}</dt><dd>192 MiB · 0.35 vCPU</dd></div><div><dt>{ru?'Переменные токена':'Token variables'}</dt><dd><code>BOT_TOKEN</code>, <code>{bot.platform==='telegram'?'TELEGRAM_BOT_TOKEN':'DISCORD_TOKEN'}</code></dd></div></dl>}</Dialog>;
}
