import type {Lang,Settings} from './types';
export function iso(d:Date){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
export function fromIso(value:string){const [y,m,d]=value.split('-').map(Number);return new Date(y,m-1,d,12);}
export function addDays(d:Date,n:number){const copy=new Date(d);copy.setDate(copy.getDate()+n);return copy;}
export function monday(d:Date){return addDays(d,-(d.getDay()+6)%7);}
export function addMonths(d:Date,n:number){return new Date(d.getFullYear(),d.getMonth()+n,1,12);}
export function monthGrid(d:Date){const first=new Date(d.getFullYear(),d.getMonth(),1,12);const start=monday(first);return Array.from({length:42},(_,i)=>addDays(start,i));}
export function locale(lang:Lang){return lang==='ru'?'ru-RU':'en-GB';}
export function format(d:Date,lang:Lang,options:Intl.DateTimeFormatOptions){return new Intl.DateTimeFormat(locale(lang),options).format(d);}
export function dateInZone(zone='Europe/Moscow'){const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());return fromIso(`${parts.find(p=>p.type==='year')!.value}-${parts.find(p=>p.type==='month')!.value}-${parts.find(p=>p.type==='day')!.value}`);}
export function parity(d:Date,s:Settings){const a=fromIso(s.anchor_monday);const days=Math.round((Date.UTC(d.getFullYear(),d.getMonth(),d.getDate())-Date.UTC(a.getFullYear(),a.getMonth(),a.getDate()))/86400000);return ((Math.floor(days/7)%2===0)===(s.anchor_parity==='odd'))?'odd':'even';}
