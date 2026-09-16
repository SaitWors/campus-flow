import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {copy,errorText} from './i18n';
import type {Lesson,Rule,Subject,Presets,RulePreview} from './types';
import {Button,Dialog,Field,Notice,useApp,useJob,useT} from './ui';
export const lessonFields=['title','title_en','kind','teacher','room','mode','meeting_url','note','start','end','subgroup','queue_enabled'] as const;

export default function LessonForm({rule,lesson,onClose,onSave}:{rule?:Rule;lesson?:Lesson;onClose:()=>void;onSave:()=>void}){
 const t=useT();const {lang,settings}=useApp();const job=useJob();const initial=lesson||rule;
 const [data,setData]=useState<Record<string,string|number|boolean>>({title:initial?.title||'',title_en:initial?.title_en||'',kind:initial?.kind||'lecture',teacher:initial?.teacher||'',room:initial?.room||'',mode:initial?.mode||'onsite',meeting_url:initial?.meeting_url||'',note:initial?.note||'',start:initial?.start||'09:30',end:initial?.end||'11:00',subgroup:initial?.subgroup||0,queue_enabled:initial?.queue_enabled||false,weekday:rule?.weekday||0,parity:rule?.parity||'all',date:lesson?.date||settings.semester_start,status:lesson?.status||'confirmed'});
 const [subjects,setSubjects]=useState<Subject[]>([]);const [presets,setPresets]=useState<Presets|null>(null);const [catalogError,setCatalogError]=useState('');const timeTouched=useRef(false);
 const [changePreview,setChangePreview]=useState<RulePreview|null>(null);
 useEffect(()=>{let active=true;void Promise.all([api<Subject[]>('/api/schedule/subjects'),api<Presets>('/api/schedule/time-presets')]).then(([s,p])=>{if(!active)return;setSubjects(s);setPresets(p);if(!initial&&!timeTouched.current&&p.items[0])setData(d=>({...d,start:p.items[0].start,end:p.items[0].end}));}).catch(e=>active&&setCatalogError(errorText(lang,e)));return()=>{active=false;};},[initial,lang]);
 const [preview,setPreview]=useState(initial?.title_en_auto||'');const [translationState,setTranslationState]=useState<'idle'|'loading'|'ready'|'unavailable'|'disabled'>('idle');
 useEffect(()=>{let active=true;setPreview('');const title=String(data.title).trim();if(data.title_en||title.length<2||title.length>120){setTranslationState('idle');return;}setTranslationState('loading');const id=setTimeout(()=>{void api<{title_en_auto:string;translation_pending:boolean;translation_enabled:boolean}>('/api/schedule/title-preview','POST',{title}).then(r=>{if(active){setPreview(r.title_en_auto);setTranslationState(r.title_en_auto?'ready':r.translation_enabled===false?'disabled':'unavailable');}}).catch(()=>{if(active)setTranslationState('unavailable');});},750);return()=>{active=false;clearTimeout(id);};},[data.title,data.title_en]);
 function set(key:string,value:string|number|boolean){if(key==='start'||key==='end')timeTouched.current=true;setChangePreview(null);setData(d=>({...d,[key]:value,...(key==='kind'&&value==='lecture'?{queue_enabled:false}:{}),...(key==='title'?{title_en:''}:{})}));}
 function selectSubject(title:string){const subject=subjects.find(s=>s.title===title);setChangePreview(null);setData(d=>({...d,title:subject?.title||'',title_en:subject?.title_en||''}));}
 function selectTime(value:string){timeTouched.current=true;const slot=presets?.items[+value];if(value!==''&&slot){setChangePreview(null);setData(d=>({...d,start:slot.start,end:slot.end}));}}
 const slotIndex=presets?.items.findIndex(s=>s.start===data.start&&s.end===data.end)??-1;
 async function submit(){const body:Record<string,unknown>={};lessonFields.forEach(k=>body[k]=data[k]);
  if(lesson){Object.assign(body,{date:data.date,status:data.status,revision:lesson.revision});await api(`/api/schedule/occurrences/${lesson.id}`,'PATCH',body);}
  else{Object.assign(body,{weekday:data.weekday,parity:data.parity,revision:rule?.revision||0});
   if(rule&&!changePreview){setChangePreview(await api<RulePreview>(`/api/schedule/rules/${rule.id}/preview`,'POST',body));return;}
   try{await api('/api/schedule/rules'+(rule?'/'+rule.id:''),rule?'PUT':'POST',{...body,...(rule?{preview_token:changePreview!.preview_token}:{})});}
   catch(error){setChangePreview(null);throw error;}
  }onSave();onClose();
 }
 return <Dialog title={t(lesson?'editLesson':rule?'editTemplate':'addLesson')} onClose={()=>!job.busy&&onClose()} wide>
  <p className="muted">{t(lesson?'exceptionHint':'templateHint')}</p>
  {catalogError&&<Notice error>{catalogError}</Notice>}
  <form onSubmit={e=>{e.preventDefault();void job.run(submit);}}>
   <fieldset className="form-fieldset" disabled={job.busy}>
   <div className="form-grid">
    <Field label={t('chooseSubject')} hint={t('catalogHint')}><select aria-label={t('chooseSubject')} value={subjects.some(s=>s.title===data.title)?String(data.title):''} onChange={e=>selectSubject(e.target.value)}><option value="">{t('newSubject')}</option>{subjects.map(s=><option key={s.title} value={s.title}>{lang==='en'?(s.title_en||s.title_en_auto||s.title):s.title}</option>)}</select></Field>
    <Field label={t('timePreset')}><select value={slotIndex<0?'':String(slotIndex)} onChange={e=>selectTime(e.target.value)}><option value="">{t('customTime')}</option>{presets?.items.map((s,i)=><option key={i} value={i}>{s.label} · {s.start}–{s.end}</option>)}</select></Field>
    <Field label={t('title')}><input required minLength={2} maxLength={120} autoFocus value={String(data.title)} onChange={e=>set('title',e.target.value)}/></Field>
    <Field label={t('titleEn')} hint={t('autoTranslateHint')}><input aria-label={t('titleEn')} maxLength={120} value={String(data.title_en)} placeholder={preview||t('automatic')} onChange={e=>set('title_en',e.target.value)}/><span className="translation-note" role="status">{!data.title_en&&(translationState==='loading'?t('translating'):translationState==='disabled'?t('translationDisabled'):translationState==='unavailable'?t('autoUnavailable'):preview)}</span></Field>
    <Field label={t('kind')}><select value={String(data.kind)} onChange={e=>set('kind',e.target.value)}>{['lecture','lab','practice'].map(k=><option key={k} value={k}>{t(k)}</option>)}</select></Field>
    <Field label={t('teacher')}><input maxLength={100} value={String(data.teacher)} onChange={e=>set('teacher',e.target.value)}/></Field>
    {lesson?<><Field label={t('date')}><input type="date" required min={settings.semester_start} max={settings.semester_end} value={String(data.date)} onChange={e=>set('date',e.target.value)}/></Field><Field label={t('status')}><select value={String(data.status)} onChange={e=>set('status',e.target.value)}>{['confirmed','pending','cancelled'].map(k=><option key={k} value={k}>{t(k)}</option>)}</select></Field></>:<><Field label={t('weekday')}><select value={Number(data.weekday)} onChange={e=>set('weekday',+e.target.value)}>{copy[lang].weekdays.map((d,i)=><option key={i} value={i}>{d}</option>)}</select></Field><Field label={t('parity')}><select value={String(data.parity)} onChange={e=>set('parity',e.target.value)}>{['all','odd','even'].map(k=><option key={k} value={k}>{k==='all'?t('everyWeek'):t(k)}</option>)}</select></Field></>}
    <Field label={t('start')}><input type="time" required value={String(data.start)} onChange={e=>set('start',e.target.value)}/></Field><Field label={t('end')}><input type="time" required value={String(data.end)} onChange={e=>set('end',e.target.value)}/></Field>
    <Field label={t('format')}><select value={String(data.mode)} onChange={e=>set('mode',e.target.value)}>{['onsite','remote','hybrid'].map(k=><option key={k} value={k}>{t(k)}</option>)}</select></Field><Field label={t('room')}><input maxLength={80} value={String(data.room)} onChange={e=>set('room',e.target.value)}/></Field>
    <Field label={t('subgroup')}><select value={Number(data.subgroup)} onChange={e=>set('subgroup',+e.target.value)}><option value={0}>{t('allGroups')}</option><option value={1}>1</option><option value={2}>2</option></select></Field><Field label={t('meetingUrl')}><input type="url" pattern="https://.*" maxLength={500} value={String(data.meeting_url)} onChange={e=>set('meeting_url',e.target.value)} placeholder="https://"/></Field>
   </div>
   <Field label={t('note')}><textarea maxLength={500} value={String(data.note)} onChange={e=>set('note',e.target.value)}/></Field><label className="check-label"><input type="checkbox" checked={!!data.queue_enabled} disabled={data.kind==='lecture'} onChange={e=>set('queue_enabled',e.target.checked)}/>{t('queueEnabled')}</label>
   </fieldset>
   {changePreview&&<section className="change-preview" aria-live="polite"><h3>{t('affectedLessons')}: {changePreview.changes.length}</h3><p>{t('previewHint')}</p><p className="muted">{t('preservedExceptions')}: {changePreview.preserved_exceptions} · {t('pastLessons')}: {changePreview.past_lessons}</p>{!changePreview.changes.length?<p>{t('noLessonChanges')}</p>:<div className="preview-scroll">{changePreview.changes.map(({before,after})=><article key={after.id}><strong>{after.date} · {after.start}–{after.end}</strong>{!before&&<p>{t('newOccurrence')}</p>}{[...lessonFields,'status' as const].filter(k=>before?.[k]!==after[k]).map(k=><p key={k}><b>{t(k==='mode'?'format':k==='queue_enabled'?'queueEnabled':k==='title_en'?'titleEn':k==='meeting_url'?'meetingUrl':k)}</b>: {before?`${display(before[k])} → `:''}{display(after[k])}</p>)}</article>)}</div>}</section>}
   {job.error&&<Notice error>{job.error}</Notice>}<div className="form-actions"><Button variant="secondary" disabled={job.busy} onClick={onClose}>{t('cancel')}</Button><Button type="submit" busy={job.busy}>{t(rule?(changePreview?'applyChanges':'previewChanges'):'save')}</Button></div>
  </form>
 </Dialog>;
 function display(value:unknown){return typeof value==='boolean'?(value?'✓':'—'):value===0?t('allGroups'):value===undefined||value===''?'—':t(String(value));}
}
