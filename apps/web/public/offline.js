/* Uses textContent only: subject names are untrusted timetable data. */
let snapshot = null;
let lang = 'ru';
try { if (localStorage.getItem('cf-language') === 'en') lang = 'en'; } catch {}
const $ = id => document.getElementById(id);
const copy = {
  ru: {title:'Сохранённое расписание',warning:'Это копия, она может устареть. Очереди и изменения доступны при подключении.',saved:'Сохранено',online:'Открыть сайт',delete:'Удалить копию',empty:'Сохранённой копии нет. Сохраните её в настройках сайта при подключении.',none:'На сохранённый период занятий нет.',group:'Подгруппа',all:'Вся группа',cancelled:'Отменена',pending:'Уточняется',lecture:'Лекция',lab:'Лабораторная',practice:'Практика'},
  en: {title:'Saved timetable',warning:'This is a snapshot and may be outdated. Queues and changes require a connection.',saved:'Saved',online:'Open website',delete:'Delete copy',empty:'No saved copy. Save one in settings while online.',none:'No classes in this saved period.',group:'Subgroup',all:'Whole group',cancelled:'Cancelled',pending:'Unconfirmed',lecture:'Lecture',lab:'Lab',practice:'Practice'}
};
function text(tag, value) { const node=document.createElement(tag); node.textContent=value; return node; }
function render() {
 const t=copy[lang]; document.documentElement.lang=lang;
 for(const key of ['title','warning','online','delete']) $(key).textContent=t[key];
 $('language').textContent=lang==='ru'?'English':'Русский'; $('lessons').replaceChildren();
 $('metadata').textContent=snapshot?`${snapshot.settings.group} · ${t.saved}: ${new Date(snapshot.saved_at).toLocaleString(lang==='ru'?'ru-RU':'en-GB')} · ${snapshot.start} — ${snapshot.end}`:'';
 $('delete').hidden=!snapshot;
 if(!snapshot){$('lessons').append(text('p',t.empty));return;}
 let last='';const items=snapshot.lessons.slice().sort((a,b)=>(a.date+a.start).localeCompare(b.date+b.start));
 if(!items.length)$('lessons').append(text('p',t.none));
 for(const lesson of items){if(last!==lesson.date){last=lesson.date;$('lessons').append(text('h2',new Date(last+'T12:00:00Z').toLocaleDateString(lang==='ru'?'ru-RU':'en-GB',{dateStyle:'full',timeZone:'UTC'})));}
  const article=document.createElement('article');article.className=lesson.status==='cancelled'?'cancelled':'';
  article.append(text('span',lesson.start+'–'+lesson.end));const content=document.createElement('div');
  content.append(text('strong',lang==='en'?(lesson.title_en||lesson.title_en_auto||lesson.title):lesson.title));
  content.append(text('p',[t[lesson.kind],lesson.room,lesson.subgroup?t.group+' '+lesson.subgroup:t.all,lesson.status!=='confirmed'?t[lesson.status]:''].filter(Boolean).join(' · ')));
  article.append(content);$('lessons').append(article);
 }
}
$('language').onclick=()=>{lang=lang==='ru'?'en':'ru';render();};
$('delete').onclick=async()=>{const cache=await caches.open('campus-public-offline-v1');await cache.delete('/offline-data.json');snapshot=null;render();};
(async()=>{try{const cache=await caches.open('campus-public-offline-v1');const response=await cache.match('/offline-data.json');snapshot=response?await response.json():null;}catch{}render();})();
