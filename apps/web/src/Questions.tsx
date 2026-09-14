import {useEffect,useRef,useState} from 'react';
import {Check,ChevronLeft,ChevronRight,LockKeyhole,MessageCircle,Plus,Send} from 'lucide-react';
import {api,actionKey,ApiError} from './api';
import {errorText,memberRoles} from './i18n';
import type {User} from './types';
import {Badge,Button,Dialog,Empty,Field,Loading,Notice,useApp} from './ui';

const copy={
 ru:{title:'Вопросы',hint:'Личная переписка со старостой, заместителем или администратором.',privacy:'Вопрос видят только вы и выбранный получатель. Другие студенты и сотрудники не имеют доступа.',ask:'Задать вопрос',open:'Открытые',closed:'Закрытые',empty:'Вопросов пока нет',recipient:'Кому',choose:'Выберите получателя',subject:'Тема',body:'Ваш вопрос',send:'Отправить',reply:'Ответить',message:'Сообщение',close:'Закрыть вопрос',reopen:'Открыть снова',new:'Новое',from:'От',to:'Кому',cancel:'Отмена',previous:'Назад',next:'Вперёд',closedHint:'Вопрос закрыт. Его можно открыть снова, если нужно уточнение.',unavailable:'Получатель больше недоступен. Закройте этот вопрос и создайте новый другому сотруднику.',limit:'Можно иметь до пяти открытых вопросов. Сначала закройте решённые.',messageLimit:'В переписке достигнут предел 200 сообщений. Создайте новый вопрос.',retry:'Повторить',noStaff:'Пока нет доступных получателей.',saved:'Вопрос отправлен',closedError:'Вопрос уже закрыт. Обновите переписку.'},
 en:{title:'Questions',hint:'Private conversations with your group head, deputy or administrator.',privacy:'Only you and the selected recipient can read this conversation. Other students and staff have no access.',ask:'Ask a question',open:'Open',closed:'Closed',empty:'No questions yet',recipient:'Recipient',choose:'Choose a recipient',subject:'Subject',body:'Your question',send:'Send',reply:'Reply',message:'Message',close:'Close question',reopen:'Reopen',new:'New',from:'From',to:'To',cancel:'Cancel',previous:'Previous',next:'Next',closedHint:'This question is closed. Reopen it if you need to follow up.',unavailable:'This recipient is no longer available. Close this question and choose another staff member for a new one.',limit:'You can have up to five open questions. Close resolved questions first.',messageLimit:'This conversation has reached 200 messages. Please start a new question.',retry:'Retry',noStaff:'There are no available recipients yet.',saved:'Question sent',closedError:'This question has been closed. Refresh the conversation.'}
};
type QKey=keyof typeof copy.ru;
function useQ(){const {lang}=useApp();return (k:QKey)=>copy[lang][k];}
function qError(lang:'ru'|'en',e:unknown){
 const k=({question_recipient_unavailable:'unavailable',question_open_limit:'limit',question_message_limit:'messageLimit',question_closed:'closedError'} as Record<string,QKey>)[(e as ApiError).code];
 return k?copy[lang][k]:errorText(lang,e);
}
type Question={id:string;owner_id:string;recipient_id:string;owner_name:string;recipient_name:string;title:string;closed:boolean;revision:number;unread:boolean;created_at:string;updated_at:string};
type Message={id:string;author_id:string;author_name:string;body:string;created_at:string};
type Detail=Question&{messages:Message[]};
type Recipient=Pick<User,'id'|'name'|'role'|'group_role'>;
function stamp(value:string,lang:'ru'|'en',zone:string){return new Intl.DateTimeFormat(lang==='ru'?'ru-RU':'en-GB',{dateStyle:'short',timeStyle:'short',timeZone:zone}).format(new Date(value));}

export default function Questions(){
 const t=useQ(),{lang,user,settings}=useApp();
 const [data,setData]=useState<{items:Question[];total:number}|null>(null),[closed,setClosed]=useState(false),[offset,setOffset]=useState(0);
 const [creating,setCreating]=useState(false),[selected,setSelected]=useState<string|null>(null),[error,setError]=useState(''),[tick,setTick]=useState(0);
 useEffect(()=>{let active=true,busy=false;setData(null);
  async function load(){if(busy)return;busy=true;try{const r=await api<{items:Question[];total:number}>('/api/notifications/questions?closed='+closed+'&offset='+offset);if(active){setData(r);setError('');}}catch(e){if(active){setData(null);setError(qError(lang,e));}}finally{busy=false;}}
  void load();const timer=setInterval(()=>{if(!document.hidden)void load();},8000);return()=>{active=false;clearInterval(timer);};
 },[closed,offset,tick,lang,user.id]);
 return <><div className="page-heading"><div><span className="eyebrow">CAMPUS FLOW / COMMUNITY</span><h1>{t('title')}</h1><p>{t('hint')}</p></div><Button onClick={()=>setCreating(true)}><Plus size={18}/>{t('ask')}</Button></div>
 <div className="question-privacy"><LockKeyhole size={19}/><span>{t('privacy')}</span></div>
 <div className="tabs">{[false,true].map(v=><button key={String(v)} className={closed===v?'active':''} onClick={()=>{setClosed(v);setOffset(0);}}>{t(v?'closed':'open')}</button>)}</div>
 {error?<Notice error>{error} <button className="text-link" onClick={()=>setTick(v=>v+1)}>{t('retry')}</button></Notice>:!data?<Loading/>:!data.items.length?<Empty title={t('empty')} description={t('hint')}/>:<div className="question-list">{data.items.map(q=><button className={'question-card '+(q.unread?'unread':'')} key={q.id} onClick={()=>setSelected(q.id)}>
 <div className="question-icon"><MessageCircle size={23}/></div><div className="question-summary"><div className="badge-row">{q.unread&&<Badge tone="blue">{t('new')}</Badge>}<small>{stamp(q.updated_at,lang,settings.timezone)}</small></div><h3>{q.title}</h3><p>{q.owner_id===user.id?t('to')+': '+q.recipient_name:t('from')+': '+q.owner_name}</p></div><ChevronRight size={20}/></button>)}</div>}
 {data&&data.total>20&&<div className="notification-pagination"><Button variant="secondary" disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-20))}><ChevronLeft size={17}/>{t('previous')}</Button><span>{offset+1}–{Math.min(offset+20,data.total)} / {data.total}</span><Button variant="secondary" disabled={offset+20>=data.total} onClick={()=>setOffset(offset+20)}>{t('next')}<ChevronRight size={17}/></Button></div>}
 {creating&&<QuestionForm onClose={()=>setCreating(false)} onCreated={id=>{setCreating(false);setClosed(false);setOffset(0);setSelected(id);setTick(v=>v+1);}}/>}
 {selected&&<Conversation key={selected} id={selected} onClose={()=>{setSelected(null);setTick(v=>v+1);}}/>}
 </>;
}

function QuestionForm({onClose,onCreated}:{onClose:()=>void;onCreated:(id:string)=>void}){
 const t=useQ(),{lang,notify}=useApp();
 const [recipients,setRecipients]=useState<Recipient[]|null>(null),[draft,setDraft]=useState({recipient_id:'',title:'',body:''}),[error,setError]=useState(''),[busy,setBusy]=useState(false),[tick,setTick]=useState(0);
 const key=useRef(actionKey());useEffect(()=>{key.current=actionKey();},[draft]);
 useEffect(()=>{let active=true;void api<Recipient[]>('/api/notifications/questions/recipients').then(r=>{if(active){setRecipients(r);setError('');}}).catch(e=>active&&setError(qError(lang,e)));return()=>{active=false;};},[lang,tick]);
 async function send(){setBusy(true);setError('');try{const q=await api<Question>('/api/notifications/questions','POST',draft,{'Idempotency-Key':key.current});notify(t('saved'));onCreated(q.id);}catch(e){setError(qError(lang,e));}finally{setBusy(false);}}
 return <Dialog title={t('ask')} onClose={onClose}><p className="muted">{t('privacy')}</p>{error&&<Notice error>{error} <button className="text-link" onClick={()=>setTick(v=>v+1)}>{t('retry')}</button></Notice>}{recipients===null?<Loading/>:!recipients.length?<Notice>{t('noStaff')}</Notice>:<form onSubmit={e=>{e.preventDefault();void send();}}><Field label={t('recipient')}><select aria-label={t('recipient')} required value={draft.recipient_id} onChange={e=>setDraft({...draft,recipient_id:e.target.value})}><option value="">{t('choose')}</option>{recipients.map(r=><option key={r.id} value={r.id}>{r.name} · {memberRoles(lang,r)}</option>)}</select></Field><Field label={t('subject')}><input required minLength={2} maxLength={120} value={draft.title} onChange={e=>setDraft({...draft,title:e.target.value})}/></Field><Field label={t('body')}><textarea required minLength={2} maxLength={2000} rows={5} value={draft.body} onChange={e=>setDraft({...draft,body:e.target.value})}/></Field><div className="form-actions"><Button variant="secondary" onClick={onClose}>{t('cancel')}</Button><Button type="submit" busy={busy}><Send size={17}/>{t('send')}</Button></div></form>}</Dialog>;
}

function Conversation({id,onClose}:{id:string;onClose:()=>void}){
 const t=useQ(),{lang,user,settings}=useApp();
 const [data,setData]=useState<Detail|null>(null),[body,setBody]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[tick,setTick]=useState(0);
 const key=useRef(actionKey());useEffect(()=>{key.current=actionKey();},[body]);
 useEffect(()=>{let active=true,loading=false,seen=0;
  async function load(){if(loading)return;loading=true;try{const q=await api<Detail>('/api/notifications/questions/'+id);if(active){setData(q);setError('');if(q.revision>seen){await api('/api/notifications/questions/'+id+'/read','POST',{revision:q.revision});seen=q.revision;}}}catch(e){if(active){setError(qError(lang,e));if(['unauthorized','account_inactive','not_found'].includes((e as ApiError).code))setData(null);}}finally{loading=false;}}
  void load();const timer=setInterval(()=>{if(!document.hidden)void load();},8000);return()=>{active=false;clearInterval(timer);};
 },[id,lang,tick]);
 async function send(){setBusy(true);setError('');try{await api('/api/notifications/questions/'+id+'/messages','POST',{body},{'Idempotency-Key':key.current});setBody('');setTick(v=>v+1);}catch(e){setError(qError(lang,e));}finally{setBusy(false);}}
 async function state(){if(!data)return;setBusy(true);setError('');try{await api('/api/notifications/questions/'+id,'PATCH',{revision:data.revision,closed:!data.closed});setTick(v=>v+1);}catch(e){setError(qError(lang,e));}finally{setBusy(false);}}
 return <Dialog title={data?.title||t('title')} onClose={onClose} wide>{error&&<Notice error>{error} <button className="text-link" onClick={()=>setTick(v=>v+1)}>{t('retry')}</button></Notice>}{!data?(!error&&<Loading/>):<><div className="conversation-participants"><LockKeyhole size={17}/><span>{data.owner_name} · {data.recipient_name}</span><Badge>{t(data.closed?'closed':'open')}</Badge></div><div className="conversation-messages" aria-label={t('title')}>{data.messages.map(m=><article className={'conversation-message '+(m.author_id===user.id?'mine':'')} key={m.id}><div><strong>{m.author_name}</strong><time dateTime={m.created_at}>{stamp(m.created_at,lang,settings.timezone)}</time></div><p>{m.body}</p></article>)}</div>
 {data.closed?<Notice>{t('closedHint')}</Notice>:<form className="conversation-compose" onSubmit={e=>{e.preventDefault();void send();}}><Field label={t('message')}><textarea required minLength={2} maxLength={2000} rows={3} value={body} onChange={e=>setBody(e.target.value)}/></Field><Button type="submit" busy={busy}><Send size={17}/>{t('reply')}</Button></form>}
 <div className="form-actions"><Button variant="secondary" busy={busy} onClick={()=>void state()}><Check size={17}/>{t(data.closed?'reopen':'close')}</Button></div></>}</Dialog>;
}
