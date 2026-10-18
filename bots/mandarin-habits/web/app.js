const tg = window.Telegram?.WebApp;
const root = document.querySelector('#app');
const dialog = document.querySelector('#dialog');
const icons = {
  sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4M17.6 17.6 19 19M5 19l1.4-1.4M17.6 6.4 19 5"/>',
  chart:'<path d="M4 20V10m8 10V4m8 16v-7M2 22h20"/>',
  gift:'<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M5 12v9h14v-9M12 8v13"/><path d="M12 8H8a3 3 0 1 1 3-3Zm0 0h4a3 3 0 1 0-3-3Z"/>',
  settings:'<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
  fire:'<path d="M13 2c1 5-5 6-3 11 1-2 3-3 5-4 1 3 4 4 4 8a7 7 0 0 1-14 0c0-4 2-6 4-8-1 4 0 5 1 5-1-6 5-7 3-12Z"/>',
  coin:'<circle cx="12" cy="12" r="9"/><path d="M12 6v12m3-9c-1-2-6-2-6 1s6 1 6 4-5 3-6 1"/>',
  bolt:'<path d="m13 2-9 12h7l-1 8 10-13h-8Z"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  play:'<path d="m9 5 10 7-10 7Z"/>',
  book:'<path d="M12 5v16M3 4c4-1 6-1 9 1 3-2 5-2 9-1v15c-4-1-6-1-9 2-3-3-5-3-9-2Z"/>',
  sport:'<path d="m4 8 12 12M2 10l8-8m4 20 8-8M2 6l4-4m12 20 4-4M7 13l6-6m-2 10 6-6"/>',
  leaf:'<path d="M20 3C8 2 2 8 5 15s15 5 15-12ZM4 21 16 8"/>',
  more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  trash:'<path d="M4 7h16M9 7V3h6v4M6 7l1 14h10l1-14M10 11v6m4-6v6"/>',
  arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
  star:'<path d="m12 3 2.8 5.6L21 9.5l-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.5l6.2-.9Z"/>',
  people:'<circle cx="9" cy="7" r="3"/><path d="M3 21v-4a6 6 0 0 1 12 0v4M16 4a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5v2"/>',
};
const icon = (name, size=19) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.leaf}</svg>`;
const mandarin = '<svg class="brand-mark" viewBox="0 0 40 44" fill="none" aria-hidden="true"><path d="M20 10C8 5 2 16 3 27c1 10 9 15 17 14 8 1 16-4 17-14 1-11-5-22-17-17Z" fill="#ff922e"/><path d="M20 13c-5 5-6 17-2 24M25 13c4 6 5 16 2 22" stroke="#da6d17" stroke-width="1.4"/><path d="M20 10c-1-5 3-8 8-8 0 5-3 8-8 8Z" fill="#ffbd72"/><path d="M20 11V6" stroke="#ffd1a0" stroke-width="2" stroke-linecap="round"/></svg>';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const days = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
const tabs = [['today','sun','Сегодня'],['progress','chart','Прогресс'],['rewards','gift','Награды'],['settings','settings','Настройки']];
let data, tab='today', filter='all', busy=false, demo=false, toastTimer;
let submitDialog = null;
const apiBase = /^\/habits\/[a-f0-9-]{36}\/$/.test(location.pathname) ? `${location.pathname}api/` : null;

function toast(message) {
  const element=document.querySelector('#toast');
  element.textContent=message; element.classList.add('visible');
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>element.classList.remove('visible'),4000);
}

async function api(path, payload) {
  if (!apiBase || !tg?.initData) throw Error('Открой приложение через кнопку OPEN APP в своём боте.');
  const response=await fetch(apiBase+path, {
    method:payload?'POST':'GET', credentials:'omit', cache:'no-store',
    headers:{'X-Telegram-Init-Data':tg.initData, ...(payload?{'Content-Type':'application/json'}:{})},
    ...(payload?{body:JSON.stringify(payload)}:{}), signal:AbortSignal.timeout(15000),
  });
  let result;
  try {result=await response.json();} catch {throw Error('Приложение пока недоступно. Проверь запуск бота и обновление Campus Flow.');}
  if (!response.ok) throw Error(result.error || 'Не удалось выполнить действие.');
  return result;
}

async function mutate(payload) {
  if (busy) throw Error('Дождись завершения предыдущего действия.');
  busy=true; root.setAttribute('aria-busy','true');
  try {
    const response=demo?demoAction(payload):await api('action',payload);
    data=response.state; render();
    tg?.HapticFeedback?.notificationOccurred('success');
    return response.result || {};
  } finally {busy=false; root.removeAttribute('aria-busy');}
}

function label(kind) {return kind==='sport'?'Движение':kind==='code'?'Java · Patchboard':'Моя привычка';}
function kindIcon(kind) {return kind==='sport'?'sport':kind==='code'?'book':'leaf';}
function at(goal) {
  return goal.kind.startsWith('habit:')?data.habits.find(h=>`habit:${h.id}`===goal.kind)?.time || '':data.options[`${goal.kind}_time`];
}
function name() {return data.options.display_name || tg?.initDataUnsafe?.user?.first_name || 'Мандарин';}
function btn(action, text, symbol, options='') {return `<button class="btn ${options || 'secondary'}" data-action="${action}">${symbol?icon(symbol,16):''}${text}</button>`;}

function render() {
  if (!data) return;
  const title=tabs.find(t=>t[0]===tab)[2];
  root.innerHTML=`${demo?'<div class="demo-banner">Демонстрация интерфейса · изменения не сохраняются</div>':''}
    <div class="shell"><header class="topbar"><div><div class="brand">${mandarin}<div>мандарин<span>.</span></div></div><div class="top-caption">МАЛЕНЬКИЕ ШАГИ. СВОЙ РИТМ.</div></div>
    <button class="identity" data-tab="settings" aria-label="Личные настройки"><span>${esc(name())}</span><div class="avatar">${esc(name().slice(0,1).toUpperCase())}</div></button></header>
    <main aria-label="${title}">${({today:todayPage,progress:progressPage,rewards:rewardsPage,settings:settingsPage})[tab]()}</main>
    <p class="sync-note">Только твой прогресс. Никаких гонок с другими.</p></div>
    <nav class="navbar" aria-label="Основная навигация"><div class="nav-inner">${tabs.map(([id,symbol,text])=>`<button class="nav-item ${id===tab?'active':''}" data-tab="${id}" ${id===tab?'aria-current="page"':''}>${icon(symbol,21)}<span>${text}</span></button>`).join('')}</div></nav>`;
  updateTimer();
}

function todayPage() {
  const done=data.goals.filter(g=>g.status==='done').length,total=data.goals.length;
  const pct=total?done/total:0;
  const date=new Intl.DateTimeFormat('ru-RU',{weekday:'long',day:'numeric',month:'long',timeZone:'UTC'}).format(new Date(`${data.date}T12:00:00Z`));
  const visible=data.goals.filter(g=>filter==='all'||(filter==='pending'?g.status==='pending':g.status==='done'));
  return `<section class="hero"><div><div class="eyebrow orange">${esc(date)}</div><h1>Каждый день —<br><span>чуть ближе к себе.</span></h1><p>${done===total&&total?'Ты закрыл все цели дня. Теперь можно выдохнуть.':'Не нужно успевать всё. Начни с одного небольшого дела.'}</p></div>
    <div class="ring" aria-label="Выполнено ${done} из ${total}"><svg viewBox="0 0 154 154" aria-hidden="true"><circle cx="77" cy="77" r="65"/><circle class="ring-value" cx="77" cy="77" r="65" stroke-dasharray="408.41" stroke-dashoffset="${408.41*(1-pct)}"/></svg><div class="ring-center"><strong>${done}<span class="subtle">/${total}</span></strong><small>целей сегодня</small></div></div></section>
    <div class="stats"><div class="stat">${icon('fire',23)}<div><strong>${data.profile.streak} <span class="subtle">дн.</span></strong><small>текущая серия</small></div></div><div class="stat">${icon('coin',23)}<div><strong>${data.profile.coins}</strong><small>твои монеты</small></div></div><div class="stat">${icon('bolt',23)}<div><strong>${data.profile.xp}</strong><small>опыта всего</small></div></div></div>
    ${data.focuses?.length?`<div class="focus-banner"><div><span class="kind">${icon('play',15)} Время для себя</span><small>${esc(data.goals.find(g=>g.id===data.focuses[0].goal_id)?.title || 'Твоё занятие')}</small></div><strong id="focus-time">—</strong></div>`:''}
    <div class="section-heading"><h2>Твой план на сегодня</h2>${btn('new-habit','Добавить','plus')}</div>
    <div class="filters" role="group" aria-label="Фильтр целей">${[['all','Все'],['pending','В планах'],['done','Готово']].map(([value,text])=>`<button class="filter ${filter===value?'active':''}" data-filter="${value}" aria-pressed="${filter===value}">${text}</button>`).join('')}</div>
    <div class="goals">${visible.length?visible.map(goalCard).join(''):`<div class="empty">${icon('leaf',28)}<h3>${total?'Здесь пока тихо':'Начни со своей привычки'}</h3><p>${total?'В этой вкладке пока нет целей.':'Чтение, прогулка, учёба — выбери то, что важно тебе.'}</p>${!total?btn('new-habit','Создать привычку','plus','primary'):''}</div>`}</div>
    <div class="tip">${icon('info',16)}<span>Выполни все цели дня — получи ещё 20 XP и 10 монет. Пропуск без штрафа. Награды подтверждаешь сам.</span></div>`;
}

function goalCard(g) {
  return `<article class="goal ${g.status}"><div class="goal-top"><span class="kind">${icon(kindIcon(g.kind),16)}${label(g.kind)}</span><span class="goal-time">${esc(at(g))} · ${g.minutes} мин</span></div><h3>${esc(g.title)}</h3><p class="goal-description">${esc(g.details)}</p>
    <div class="goal-rewards"><span>${icon('bolt',14)}+${g.xp} XP</span><span>${icon('coin',14)}+${g.coins} монет</span></div>
    ${g.status==='pending'?`<div class="goal-actions"><button class="btn primary" data-action="done" data-id="${g.id}">${icon('check',17)}Готово</button><button class="btn secondary" data-action="focus" data-id="${g.id}" aria-label="Начать таймер цели ${esc(g.title)}">${icon('play',17)}Таймер</button><button class="icon-button" data-action="goal-menu" data-id="${g.id}" aria-label="Другие действия с целью">${icon('more',19)}</button></div>`:`<div class="done-label">${icon(g.status==='done'?'check':'clock',17)}${g.status==='done'?'Цель выполнена. Шаг засчитан.':'Сегодня пропущено. Завтра новый шанс.'}</div>`}
    <div class="goal-more"><button data-action="details" data-id="${g.id}">Подробнее ${icon('arrow',13)}</button>${g.lesson?`<button data-action="lesson" data-id="${g.lesson}">Открыть урок ${icon('book',13)}</button>`:''}</div></article>`;
}

function progressPage() {
  const p=data.profile,max=Math.max(1,...data.week.map(w=>w.done));
  return `<div class="page-title"><div class="eyebrow orange">ТВОИ МАЛЕНЬКИЕ ПОБЕДЫ</div><h1>Посмотри, сколько уже сделано.</h1><p>Серия растёт за дни хотя бы с одной выполненной целью.</p></div>
    <div class="split"><section class="panel"><h2>Твой уровень</h2><div class="level-row"><strong>${p.level} уровень</strong><small>${p.xp} XP всего</small></div><progress max="200" value="${200-p.next_level}" aria-label="Опыт до следующего уровня"></progress><p>Ещё ${p.next_level} XP до следующего уровня.</p></section>
    <section class="panel"><h2>Последние 7 дней</h2><div class="week-chart">${data.week.map(w=>`<div class="week-day ${w.day===data.date?'today':''}"><svg viewBox="0 0 32 90" aria-label="${esc(w.day)}: ${w.done} целей"><rect x="3" y="0" width="26" height="90" rx="7" fill="#24231f"/><rect x="3" y="${90-Math.max(4,w.done/max*90)}" width="26" height="${Math.max(4,w.done/max*90)}" rx="6" fill="${w.done?'#ff922e':'#49402f'}"/></svg>${days[(new Date(`${w.day}T12:00:00Z`).getUTCDay()+6)%7]}</div>`).join('')}</div></section></div>
    <div class="stats"><div class="stat">${icon('check',23)}<div><strong>${p.completed}</strong><small>целей выполнено</small></div></div><div class="stat">${icon('fire',23)}<div><strong>${p.streak}</strong><small>дней подряд</small></div></div><div class="stat">${icon('star',23)}<div><strong>${p.achievements.length}</strong><small>достижений</small></div></div></div>
    ${data.options.code_enabled==='1'?`<section class="panel"><div class="section-heading"><h2>Java · Patchboard</h2><span class="kind">${data.course.completed.length}/30</span></div><progress max="30" value="${data.course.completed.length}" aria-label="Прогресс курса"></progress><p>${data.course.current?`Следующее занятие: ${data.course.current}. Незавершённые темы не перескакивают.`:'Все занятия твоего маршрута завершены.'}</p>${data.course.current?`<button class="btn secondary" data-action="lesson" data-id="${data.course.current}">${icon('book',16)}Открыть занятие</button>`:''}</section>`:''}
    <div class="split"><section class="panel"><h2>Достижения</h2>${p.achievements.length?p.achievements.map(a=>`<div class="achievement">${icon('star',20)}${esc(a)}</div>`).join(''):'<p>Первое достижение — за первую выполненную цель. Начни сегодня.</p>'}</section><section class="panel"><h2>История шагов</h2>${data.history.length?data.history.map(r=>`<div class="ledger-row"><span>${esc(r.note)}</span><strong>${r.coins>0?'+':''}${r.coins} монет</strong></div>`).join(''):'<p>Здесь появятся твои выполненные цели и полученные награды.</p>'}</section></div>`;
}

function rewardsPage() {
  return `<div class="page-title"><div class="eyebrow orange">ПОВОД ПОРАДОВАТЬ СЕБЯ</div><h1>Усилия заслуживают награды.</h1><p>Выбирай приятные вещи и обменивай на заработанные монеты.</p></div>
    <div class="panel balance-card"><div><div class="eyebrow">ТВОЙ БАЛАНС</div><strong>${data.profile.coins}</strong><small>монет за маленькие победы</small></div>${icon('coin',56)}</div>
    <div class="section-heading"><h2>Твои награды</h2>${btn('new-reward','Добавить','plus')}</div><div class="reward-grid">${data.rewards.length?data.rewards.map(r=>`<article class="reward"><div class="reward-icon">${icon('gift',22)}</div><h3>${esc(r.title)}</h3><div class="price">${r.cost} монет</div><div class="reward-footer"><button class="btn ${data.profile.coins>=r.cost?'primary':'secondary'}" data-action="buy" data-id="${r.id}">${data.profile.coins>=r.cost?'Хочу эту награду':`Осталось ${r.cost-data.profile.coins} монет`}</button><button class="icon-button" data-action="reward-remove" data-id="${r.id}" aria-label="Удалить награду ${esc(r.title)}">${icon('trash',17)}</button></div></article>`).join(''):`<div class="empty">${icon('gift',30)}<h3>Что тебя порадует?</h3><p>Кофе, любимый фильм или время на игру — добавь свою первую награду.</p>${btn('new-reward','Добавить награду','plus','primary')}</div>`}</div><div class="tip">${icon('info',16)}<span>Монеты виртуальные. Здесь нет платежей: награды для себя ты выполняешь сам.</span></div>`;
}

function setting(key, title, type='time') {
  return `<label class="field"><span>${title}</span><input type="${type}" data-setting="${key}" value="${esc(data.options[key])}" ${type==='time'?'min="08:00" max="22:59"':''} ${key==='display_name'?'maxlength="40" placeholder="Как тебя называть?"':''}></label>`;
}

function settingsPage() {
  const o=data.options,paused=o.paused_until&&data.date<o.paused_until;
  return `<div class="page-title"><div class="eyebrow orange">ПОД ТВОЙ ОБРАЗ ЖИЗНИ</div><h1>Твои правила. Твой ритм.</h1><p>Настройки и привычки личные. Изменения сохраняются сразу.</p></div>
    <div class="split"><section class="panel"><h2>О тебе</h2>${setting('display_name','Имя','text')}${setting('timezone','Часовой пояс, например Europe/Moscow','text')}<p>Время напоминаний считается по твоему часовому поясу. Тишина с 23:00 до 08:00.</p></section>
    <section class="panel"><h2>Стандартные направления</h2><label class="toggle-row"><span>Движение<small>Прогулки и посильная активность</small></span><input type="checkbox" data-setting="sport_enabled" ${o.sport_enabled==='1'?'checked':''}></label><label class="toggle-row"><span>Java · Patchboard<small>Твой курс из 30 занятий. Можно включить позже.</small></span><input type="checkbox" data-setting="code_enabled" ${o.code_enabled==='1'?'checked':''}></label><label class="field"><span>Нагрузка движения</span><select data-setting="difficulty">${[['light','Лёгкая'],['normal','Обычная'],['hard','Повыше']].map(([value,title])=>`<option value="${value}" ${o.difficulty===value?'selected':''}>${title}</option>`).join('')}</select></label></section></div>
    <section class="panel"><div class="section-heading"><h2>Свои привычки</h2>${btn('new-habit','Добавить','plus')}</div><p>Занятия, которые важны именно тебе. До 10 привычек; за каждую — 25 XP и 15 монет.</p>${data.habits.length?data.habits.map(h=>`<div class="habit-row"><div><strong>${esc(h.title)}</strong><small>${esc(h.time)} · ${h.minutes} мин · ${h.weekdays.split(',').map(d=>days[Number(d)]).join(', ')}</small></div><button class="icon-button" data-action="habit-remove" data-id="${h.id}" aria-label="Убрать привычку ${esc(h.title)}">${icon('trash',17)}</button></div>`).join(''):'<p>Добавь чтение, обучение, растяжку или другое своё занятие.</p>'}</section>
    <div class="split"><section class="panel"><h2>Расписание</h2><div class="two-fields">${setting('sport_time','Движение')}${setting('code_time','Java')}</div>${setting('summary_time','Итог дня')}<p>Время действует для новых и ещё не отправленных напоминаний. Уже полученные сообщения повторно не приходят.</p></section>
    <section class="panel"><h2>Время на отдых</h2><p>${paused?`Напоминания на паузе до ${esc(o.paused_until)}.`:'Можно поставить напоминания на паузу. Серия дней при этом не замораживается.'}</p><div class="section-heading">${paused?btn('resume','Возобновить','play','primary'):btn('pause','Пауза на 1 день','clock')}</div><p>Выполнение целей остаётся доступным. Напоминания останавливаются.</p>${o.code_enabled==='1'&&!data.course.completed.length?`<div class="field"><span>Первое занятие уже сделал?</span><button class="btn secondary" data-action="course-start">Выбрать начальное занятие</button></div>`:''}</section></div>
    ${data.role==='owner'?`<section class="panel"><div class="kind">${icon('people',18)}Для близких</div><h2 class="section-heading">В одном боте. Каждый по-своему.</h2><p>Приглашение действует 24 часа и принимается один раз. У каждого отдельные привычки, монеты и настройки.</p><div class="section-heading">${btn('invite','Создать приглашение','plus','primary')}</div><p>Список участников: /members в чате. Отключить доступ: /revoke ID. Ты не видишь личный прогресс приглашённых.</p></section>`:''}
    <div class="tip">${icon('info',16)}<span>Резервная копия твоего профиля доступна командой /backup в чате бота.</span></div>`;
}

function modal(title, body, submitText, onSubmit) {
  submitDialog=onSubmit;
  dialog.innerHTML=`<form id="modal-form"><div class="dialog-head"><h2 id="dialog-title">${title}</h2><button type="button" class="icon-button" data-close aria-label="Закрыть">${icon('close',20)}</button></div>${body}<div id="dialog-error" role="alert"></div><div class="dialog-actions"><button type="button" class="btn secondary" data-close>Закрыть</button>${submitText?`<button class="btn primary" type="submit">${submitText}</button>`:''}</div></form>`;
  if (!dialog.open) dialog.showModal();
}

function newHabit() {
  modal('Новая привычка', `<p class="subtle">Небольшое дело, для которого хочется находить время.</p><label class="field"><span>Название</span><input name="title" required maxlength="60" placeholder="Например, читать 10 страниц"></label><label class="field"><span>Что именно сделать</span><textarea name="details" maxlength="1000" placeholder="Простой и понятный результат"></textarea></label><div class="two-fields"><label class="field"><span>Сколько минут</span><input name="minutes" type="number" min="1" max="180" value="15" required></label><label class="field"><span>Когда напомнить</span><input name="time" type="time" min="08:00" max="22:59" value="18:00" required></label></div><div class="field"><span>В какие дни</span><div class="weekday-picker">${days.map((d,i)=>`<label class="day-check"><input type="checkbox" name="days" value="${i}" checked><span>${d}</span></label>`).join('')}</div></div>`, 'Добавить', async f=>{
    await mutate({action:'habit_add', title:f.get('title'),details:f.get('details'),minutes:Number(f.get('minutes')),time:f.get('time'),weekdays:f.getAll('days').map(Number)});
    dialog.close();toast('Привычка добавлена. Первый шаг — сегодня.');
  });
}

function newReward() {
  modal('Твоя награда', '<p class="subtle">Выбери что-то приятное, ради чего хочется сделать ещё один шаг.</p><label class="field"><span>Название</span><input name="title" required maxlength="60" placeholder="Кофе в любимом месте"></label><label class="field"><span>Цена в монетах</span><input name="cost" type="number" min="1" max="1000000" value="80" required></label>', 'Добавить',async f=>{
    await mutate({action:'reward_add',title:f.get('title'),cost:Number(f.get('cost'))});dialog.close();toast('Награда добавлена.');
  });
}

function markdown(text) {
  let output='', code=false, lines=[];
  const flush=()=>{output+=`<pre><code>${esc(lines.join('\n'))}</code></pre>`;lines=[];};
  for(const line of text.split('\n')) {
    if(line.startsWith('```')) {if(code)flush();code=!code;continue;}
    if(code){lines.push(line);continue;}
    const heading=line.match(/^(#{1,3})\s+(.*)/);
    if(heading){output+=`<h${heading[1].length}>${esc(heading[2])}</h${heading[1].length}>`;continue;}
    if(!line.trim())continue;
    const safe=esc(line).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
    output+=`<p>${safe}</p>`;
  }
  if(code)flush();return output;
}

async function handleAction(action,id) {
  if(action==='new-habit')return newHabit();
  if(action==='new-reward')return newReward();
  if(action==='demo'){demo=true;data=demoState();return render();}
  if(action==='retry')return load();
  if(action==='done') {await mutate({action:'done',id});return toast('Шаг засчитан. Так держать!');}
  if(['focus','snooze','pause','resume'].includes(action)) {
    const result=await mutate({action,...(action==='pause'?{days:1}:action==='resume'?{}:{id})});
    dialog.close();return toast(result.message || (action==='pause'?'Напоминания на паузе.':'Напоминания включены.'));
  }
  if(action==='details') {
    const g=data.goals.find(g=>g.id===id);
    return modal('Твоя цель',`<h3>${esc(g.title)}</h3><p class="detail-text">${esc(g.details)}</p>`,null,null);
  }
  if(action==='goal-menu') {
    const g=data.goals.find(g=>g.id===id);
    return modal('В своём темпе',`<p class="subtle">${esc(g.title)}</p><div class="field"><button class="btn secondary" type="button" data-action="snooze" data-id="${id}">${icon('clock',17)}Напомнить через 30 минут</button><button class="btn ghost" type="button" data-action="skip" data-id="${id}">Пропустить сегодня</button></div>`,null,null);
  }
  if(action==='skip')return modal('Сегодня без этой цели?', '<p class="subtle">Награда не начислится. Вернуть эту цель сегодня будет нельзя, но завтра появится новый план.</p>','Пропустить',async()=>{await mutate({action:'skip',id});dialog.close();toast('Пропущено без штрафа.');});
  if(action==='buy') {
    const result=await mutate({action:'quote',id});
    return modal('Немного радости',`<p class="detail-text">Получить «${esc(result.quote.title)}» за ${result.quote.cost} монет? После подтверждения монеты спишутся.</p><p class="subtle">Награду ты выполняешь сам. Реальных платежей нет.</p>`,'Получить',async()=>{await mutate({action:'redeem',purchase:result.quote.id});dialog.close();toast('Награда твоя. Приятного отдыха!');});
  }
  if(action==='reward-remove'||action==='habit-remove')return modal('Убрать из списка?',`<p class="subtle">${action==='habit-remove'?'Привычка и её ожидающие напоминания отключатся. Выполненные цели останутся в истории.':'Награда больше не будет доступна для покупки. История сохранится.'}</p>`,'Убрать',async()=>{await mutate({action:action.replace('-','_'),id});dialog.close();toast('Убрано из списка.');});
  if(action==='course-start')return modal('С какого занятия начнём?', '<p class="subtle">Доступно до первого зачёта курса в боте. За прошлые занятия монеты не начисляются.</p><label class="field"><span>Номер занятия</span><input type="number" min="1" max="30" name="day" value="2" required></label>','Выбрать',async f=>{await mutate({action:'course_start',day:Number(f.get('day'))});dialog.close();toast('Начальное занятие выбрано.');});
  if(action==='lesson') {
    const result=demo?{title:`День ${id} · Java`,markdown:'# Твой урок Java\n\nЗдесь открывается полная исходная заметка занятия из плана Patchboard. В демонстрации реальные заметки не загружаются.'}:await api(`lesson/${id}`);
    return modal('Занятие Patchboard',`<article class="lesson-note">${markdown(result.markdown)}</article>`,null,null);
  }
  if(action==='invite') {
    const result=await mutate({action:'invite'});
    return modal('Делитесь заботой о себе',`<p class="subtle">Передай эту ссылку близкому человеку. Приглашение одноразовое, действует 24 часа. Личные данные каждого останутся отдельными.</p><pre class="invite-code">${esc(result.invite)}</pre>`,'Скопировать',async()=>{
      try{await navigator.clipboard.writeText(result.invite);toast('Приглашение скопировано.');dialog.close();}catch{throw Error('Выдели и скопируй ссылку вручную.');}
    });
  }
}

document.addEventListener('click',async event=>{
  const button=event.target.closest('button'); if(!button)return;
  if(button.hasAttribute('data-close')){dialog.close();return;}
  if(button.dataset.tab){tab=button.dataset.tab;render();window.scrollTo({top:0,behavior:'instant'});return;}
  if(button.dataset.filter){filter=button.dataset.filter;render();return;}
  if(!button.dataset.action)return;
  button.disabled=true;
  try{await handleAction(button.dataset.action,Number(button.dataset.id));}
  catch(error){toast(error.message || 'Не удалось выполнить действие.');}
  finally{button.disabled=false;}
});

document.addEventListener('change',async event=>{
  const input=event.target;if(!input.dataset.setting)return;
  const value=input.type==='checkbox'?(input.checked?'1':'0'):input.value.trim();
  input.disabled=true;
  try{await mutate({action:'setting',key:input.dataset.setting,value});toast('Сохранено.');}
  catch(error){toast(error.message);render();}
  finally{input.disabled=false;}
});

dialog.addEventListener('submit',async event=>{
  event.preventDefault();if(!submitDialog)return;
  const button=dialog.querySelector('[type=submit]');button.disabled=true;
  try{await submitDialog(new FormData(event.target));}
  catch(error){dialog.querySelector('#dialog-error').textContent=error.message;}
  finally{button.disabled=false;}
});
dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}});

function updateTimer() {
  const element=document.querySelector('#focus-time');if(!element||!data?.focuses?.length)return;
  const seconds=Math.max(0,Math.ceil(data.focuses[0].due_at-Date.now()/1000));
  element.textContent=seconds?`${Math.floor(seconds/60).toString().padStart(2,'0')}:${(seconds%60).toString().padStart(2,'0')}`:'Время вышло';
}
setInterval(updateTimer,1000);

function demoState() {
  const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow'}).format(new Date());
  const week=Array.from({length:7},(_,i)=>{const d=new Date(`${today}T12:00:00Z`);d.setUTCDate(d.getUTCDate()-6+i);return{day:d.toISOString().slice(0,10),done:[2,1,3,2,0,2,1][i]};});
  return {date:today,role:'owner',profile:{xp:345,coins:165,level:2,next_level:55,streak:3,completed:14,achievements:['Первый шаг','Десять выполненных целей','Три дня подряд']},
    options:{display_name:'Мандарин',timezone:'Europe/Moscow',sport_time:'10:00',code_time:'19:00',summary_time:'21:30',code_enabled:'1',sport_enabled:'1',difficulty:'normal',paused_until:''},
    goals:[{id:1,kind:'sport',title:'Прогулка без спешки',details:'Пройдись 20 минут в удобном темпе. Можно выбрать новый маршрут или просто подышать свежим воздухом.',minutes:20,xp:30,coins:20,status:'done',lesson:null},
      {id:2,kind:'code',title:'Типы, условия и небольшие методы',details:'Продолжи свой проект Patchboard. Разбери типы данных, добавь условия и вынеси повторяющийся код в методы.',minutes:60,xp:50,coins:30,status:'pending',lesson:2},
      {id:3,kind:'habit:1',title:'Почитать для себя',details:'Десять страниц любимой книги. Телефон можно отложить: это время только для тебя.',minutes:15,xp:25,coins:15,status:'pending',lesson:null}],
    habits:[{id:1,title:'Почитать для себя',details:'Десять страниц любимой книги.',minutes:15,time:'21:00',weekdays:'0,1,2,3,4,5,6'}],
    course:{current:2,completed:[1],start:1,total:30},week,focuses:[],
    rewards:[{id:1,title:'Кофе в любимом месте',cost:80},{id:2,title:'Вечер за любимой игрой',cost:120},{id:3,title:'Небольшая покупка для себя',cost:300}],
    history:[{note:'Прогулка без спешки',coins:20,xp:30},{note:'Три дня подряд',coins:25,xp:0}]};
}

function demoAction(p) {
  const g=data.goals.find(g=>g.id===p.id);let result={};
  if(p.action==='done'&&g?.status==='pending'){g.status='done';data.profile.coins+=g.coins;data.profile.xp+=g.xp;data.profile.completed++;data.history.unshift({note:g.title,coins:g.coins,xp:g.xp});}
  else if(p.action==='skip'&&g)g.status='skipped';
  else if(p.action==='setting')data.options[p.key]=p.value;
  else if(p.action==='pause')data.options.paused_until='2099-01-01';
  else if(p.action==='resume')data.options.paused_until='';
  else if(p.action==='habit_add'){const id=Math.max(0,...data.habits.map(h=>h.id))+1;data.habits.push({...p,id,weekdays:p.weekdays.join(',')});data.goals.push({id:Math.max(0,...data.goals.map(g=>g.id))+1,kind:`habit:${id}`,title:p.title,details:p.details,minutes:p.minutes,status:'pending',xp:25,coins:15,lesson:null});}
  else if(p.action==='habit_remove'){data.habits=data.habits.filter(h=>h.id!==p.id);data.goals=data.goals.filter(g=>g.kind!==`habit:${p.id}`);}
  else if(p.action==='reward_add')data.rewards.push({id:Math.max(0,...data.rewards.map(r=>r.id))+1,title:p.title,cost:p.cost});
  else if(p.action==='reward_remove')data.rewards=data.rewards.filter(r=>r.id!==p.id);
  else if(p.action==='quote'){const r=data.rewards.find(r=>r.id===p.id);result.quote={...r,id:String(r.id)};}
  else if(p.action==='redeem'){const r=data.rewards.find(r=>String(r.id)===p.purchase);if(data.profile.coins<r.cost)throw Error('Пока не хватает монет.');data.profile.coins-=r.cost;}
  else if(p.action==='invite')result.invite='Демонстрация: реальное приглашение выдаёт твой запущенный бот.';
  else if(p.action==='course_start')data.course.current=p.day;
  else if(p.action==='focus'){data.focuses=[{goal_id:p.id,due_at:Date.now()/1000+g.minutes*60}];result.message='Таймер включён в демонстрации.';}
  else if(p.action==='snooze')result.message='В реальном боте напомню через 30 минут.';
  data.profile.level=1+Math.floor(data.profile.xp/200);data.profile.next_level=200-data.profile.xp%200;
  return {state:data,result};
}

async function load() {
  if(demo){data=demoState();return render();}
  try{data=await api('state');render();}
  catch(error){root.innerHTML=`<main class="welcome">${mandarin}<div class="eyebrow orange">МАНДАРИН · СВОЙ РИТМ</div><h1>Здесь начинается<br>твой маленький шаг.</h1><p>${esc(error.message)}</p>${btn('retry','Попробовать ещё раз','arrow','primary')}${btn('demo','Посмотреть интерфейс',null,'ghost')}</main>`;}
}

if(tg){tg.ready();tg.expand();tg.setHeaderColor?.('#10100f');tg.setBackgroundColor?.('#10100f');if(tg.isVersionAtLeast?.('7.10'))tg.setBottomBarColor?.('#10100f');}
if(new URLSearchParams(location.search).get('demo')==='1')demo=true;
load();
setInterval(async()=>{if(!demo&&data&&!busy&&!document.hidden&&!dialog.open&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName)){try{data=await api('state');render();}catch(error){toast(error.message);}}},60000);
