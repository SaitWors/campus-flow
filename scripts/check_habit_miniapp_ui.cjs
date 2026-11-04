/* Run against the disposable tests.serve_miniapp server, never production. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const fs=require('node:fs');
const path=require('node:path');
const origin=process.env.HABIT_UI_ORIGIN||'http://127.0.0.1:8765';
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin))throw Error('Only a disposable loopback fixture is supported.');
const target=origin+'/habits/10000000-0000-0000-0000-000000000001/';
const out=process.env.HABIT_UI_OUTPUT||'test-results/habits';
fs.mkdirSync(out,{recursive:true});
const token='123456:'+'A'.repeat(35);
let browser;
function signed(uid){const values={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:uid,first_name:uid===123?'Мандарин':'Мандаринка'})};const key=crypto.createHmac('sha256','WebAppData').update(token).digest();values.hash=crypto.createHmac('sha256',key).update(Object.keys(values).sort().map(k=>`${k}=${values[k]}`).join('\n')).digest('hex');return new URLSearchParams(values).toString();}
async function prepare(browser,uid,width=390){const context=await browser.newContext({viewport:{width,height:844},deviceScaleFactor:1,reducedMotion:'reduce'});await context.route('https://telegram.org/**',r=>r.abort());await context.addInitScript(({raw,name})=>{window.Telegram={WebApp:{initData:raw,initDataUnsafe:{user:{first_name:name}},ready(){},expand(){},setHeaderColor(){},setBackgroundColor(){},setBottomBarColor(){},isVersionAtLeast(){return true;}}};},{raw:signed(uid),name:uid===123?'Мандарин':'Мандаринка'});return context;}
async function noOverflow(page){assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'Horizontal overflow');}
(async()=>{const entry=await fetch(target);assert.equal(entry.status,200);assert.match(entry.headers.get('content-type'),/^text\/html/);
 if(process.env.HABIT_GATEWAY==='1'){
   for(const query of ['', '?tgWebAppStartParam=habits%2Ftoday']){
     const shortEntry=target.slice(0,-1)+query;
     const redirect=await fetch(shortEntry,{redirect:'manual'});
     assert.equal(redirect.status,308,'The entry without a trailing slash must open the Mini App.');
     assert.equal(redirect.headers.get('location'),new URL(target).pathname+query,'Keep HTTPS at the outer proxy and preserve Telegram query parameters.');
     const followed=await fetch(shortEntry);
     assert.equal(followed.status,200);
     assert.equal(followed.url,target+query);
     assert.match(followed.headers.get('content-type'),/^text\/html/);
   }
   for(const invalid of ['not-a-uuid','10000000-0000-0000-0000-000000000001/unknown']){
     assert.equal((await fetch(origin+'/habits/'+invalid,{redirect:'manual'})).status,404);
   }
   const hostile=await fetch(target+'api/lesson/99',{redirect:'manual'});assert.equal(hostile.status,200);assert.equal(hostile.headers.get('content-type'),'application/json');assert.equal(hostile.headers.get('set-cookie'),null);assert.equal(hostile.headers.get('refresh'),null);assert.equal(hostile.headers.get('x-content-type-options'),'nosniff');assert.match(hostile.headers.get('content-security-policy'),/sandbox/);assert.match(await hostile.text(),/habitGatewayUnsafe/);
   const probe=await fetch(target+'api/lesson/98',{headers:{Cookie:'cf_session=private',Authorization:'Bearer private','X-Internal-Token':'private'}});assert.deepEqual(await probe.json(),{cookie:'',authorization:'',internal:''});
 }
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});const context=await prepare(browser,123);const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));const gatewayEntry=target.slice(0,-1)+'?tgWebAppStartParam=habits%2Ftoday#tgWebAppVersion=8.0';await page.goto(process.env.HABIT_GATEWAY==='1'?gatewayEntry:target);await page.getByRole('heading',{name:'Твой план на сегодня'}).waitFor();
 if(process.env.HABIT_GATEWAY==='1')assert.equal(page.url(),target+'?tgWebAppStartParam=habits%2Ftoday#tgWebAppVersion=8.0','Preserve Telegram parameters and hash through the redirect.');
 await noOverflow(page);await page.screenshot({path:path.join(out,'today-mobile.png'),fullPage:true});
 for(const [name,file] of [['Прогресс','progress-mobile.png'],['Награды','rewards-mobile.png'],['Настройки','settings-mobile.png']]){await page.getByRole('navigation').getByRole('button',{name,exact:true}).click();await noOverflow(page);await page.screenshot({path:path.join(out,file),fullPage:true});}
 await page.getByRole('navigation').getByRole('button',{name:'Сегодня',exact:true}).click();await page.getByRole('button',{name:'Добавить',exact:true}).click();await page.getByLabel('Название',{exact:true}).fill('Растяжка');await page.getByLabel('Что именно сделать').fill('Пять спокойных упражнений');await page.getByRole('dialog').getByRole('button',{name:'Добавить',exact:true}).click();await page.getByRole('heading',{name:'Растяжка',exact:true}).waitFor();
 const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Растяжка',exact:true})});await card.getByRole('button',{name:'Начать таймер цели Растяжка'}).click();await page.locator('#focus-time').waitFor();await card.getByRole('button',{name:'Готово',exact:true}).click();await card.getByText('Цель выполнена. Шаг засчитан.').waitFor();
 await page.getByRole('navigation').getByRole('button',{name:'Награды',exact:true}).click();await page.getByRole('button',{name:'Добавить',exact:true}).click();await page.getByLabel('Название',{exact:true}).fill('Кофе');await page.getByLabel('Цена в монетах').fill('10');await page.getByRole('dialog').getByRole('button',{name:'Добавить',exact:true}).click();const reward=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Кофе',exact:true})});await reward.getByRole('button',{name:'Хочу эту награду'}).click();await page.getByRole('dialog').getByRole('button',{name:'Получить',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
 await page.getByRole('navigation').getByRole('button',{name:'Настройки',exact:true}).click();await page.getByLabel('Имя',{exact:true}).fill('Мой ритм');await page.getByLabel('Имя',{exact:true}).press('Tab');await page.getByText('Сохранено.',{exact:true}).waitFor();await page.getByRole('checkbox',{name:'Java · Patchboard'}).uncheck();await page.getByRole('navigation').getByRole('button',{name:'Сегодня',exact:true}).click();await page.waitForFunction(()=>!Array.from(document.querySelectorAll('article')).some(e=>e.textContent.includes('Java · Patchboard')));
 await page.setViewportSize({width:350,height:780});await noOverflow(page);await page.setViewportSize({width:1280,height:900});await noOverflow(page);await page.screenshot({path:path.join(out,'today-desktop.png'),fullPage:true});
 const guestContext=await prepare(browser,456);const guest=await guestContext.newPage();await guest.goto(target);await guest.getByRole('heading',{name:'Твой план на сегодня'}).waitFor();assert.equal(await guest.getByRole('article').count(),1);await guest.getByRole('navigation').getByRole('button',{name:'Настройки',exact:true}).click();assert.equal(await guest.getByRole('button',{name:'Создать приглашение'}).count(),0);await guest.getByLabel('Имя',{exact:true}).fill('Мандаринка');await guest.getByLabel('Имя',{exact:true}).press('Tab');await guest.getByText('Сохранено.',{exact:true}).waitFor();
 const ownerState=await (await fetch(target+'api/state',{headers:{'X-Telegram-Init-Data':signed(123)}})).json();assert.equal(ownerState.options.display_name,'Мой ритм');assert.equal(ownerState.options.code_enabled,'0');
 const unknown=await browser.newPage({viewport:{width:390,height:844}});await unknown.route('https://telegram.org/**',r=>r.abort());await unknown.goto(target);await unknown.getByText('Открой приложение через кнопку OPEN APP в своём боте.',{exact:true}).waitFor();assert.equal(await unknown.getByRole('article').count(),0);
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS: mobile/desktop, goals, focus, rewards, settings, two isolated profiles, unauthenticated access.');})().catch(async e=>{console.error(e);await browser?.close();process.exitCode=1;});
