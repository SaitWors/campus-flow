import {useEffect,useState} from 'react';
import {Plus,Trash2} from 'lucide-react';
import {api} from './api';
import {errorText} from './i18n';
import type {Presets,Subject,TimeSlot} from './types';
import {Button,Field,IconButton,Loading,Notice,useApp,useJob,useT} from './ui';

export default function Catalog(){
 const t=useT();const {lang}=useApp();const job=useJob();const [presets,setPresets]=useState<Presets|null>(null);const [subjects,setSubjects]=useState<Subject[]>([]);const [error,setError]=useState('');const [reload,setReload]=useState(0);
 useEffect(()=>{let active=true;void Promise.all([api<Presets>('/api/schedule/time-presets'),api<Subject[]>('/api/schedule/subjects')]).then(([p,s])=>{if(active){setPresets(p);setSubjects(s);setError('');}}).catch(e=>active&&setError(errorText(lang,e)));return()=>{active=false;};},[lang,reload]);
 function edit(i:number,k:keyof TimeSlot,value:string){setPresets(p=>p&&({...p,items:p.items.map((s,j)=>i===j?{...s,[k]:value}:s)}));}
 if(error)return <Notice error>{error}<Button variant="ghost" onClick={()=>setReload(v=>v+1)}>{t('retry')}</Button></Notice>;
 if(!presets)return <Loading/>;
 return <div className="catalog-grid"><section className="panel"><h2>{t('timePresets')}</h2><p className="muted">{t('presetsHint')}</p><form onSubmit={e=>{e.preventDefault();void job.run(async()=>setPresets(await api<Presets>('/api/schedule/time-presets','PUT',presets)),t('saved'));}}><fieldset className="form-fieldset" disabled={job.busy}>{presets.items.map((slot,i)=><div className="preset-row" key={i}><Field label={t('slotLabel')}><input required maxLength={30} value={slot.label} onChange={e=>edit(i,'label',e.target.value)}/></Field><Field label={t('start')}><input type="time" required value={slot.start} onChange={e=>edit(i,'start',e.target.value)}/></Field><Field label={t('end')}><input type="time" required value={slot.end} onChange={e=>edit(i,'end',e.target.value)}/></Field><IconButton label={t('removeSlot')+' '+slot.label} disabled={presets.items.length===1} onClick={()=>setPresets({...presets,items:presets.items.filter((_,j)=>i!==j)})}><Trash2 size={18}/></IconButton></div>)}</fieldset>{job.error&&<Notice error>{job.error}<Button variant="ghost" onClick={()=>setReload(v=>v+1)}>{t('refresh')}</Button></Notice>}<div className="button-row"><Button variant="secondary" disabled={job.busy||presets.items.length>=12} onClick={()=>setPresets({...presets,items:[...presets.items,{label:'',start:'',end:''}]})}><Plus size={17}/>{t('addSlot')}</Button><Button type="submit" busy={job.busy}>{t('save')}</Button></div></form></section><section className="panel"><h2>{t('savedSubjects')} <span className="count">{subjects.length}</span></h2><p className="muted">{t('catalogHint')}</p><div className="subject-list">{subjects.map(s=><article key={s.title}><strong>{s.title}</strong><small>{s.title_en||s.title_en_auto||'—'}</small></article>)}</div></section></div>;
}
