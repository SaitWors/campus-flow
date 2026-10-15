// Deterministic UI contract. All API calls are intercepted; no real bots or data are changed.
// Run against a built Campus Flow or local Vite, with the existing Playwright test environment.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const base=process.env.CAMPUS_BASE_URL||'http://localhost:8080';
const image='ghcr.io/saitwors/guild-helper@sha256:'+'a'.repeat(64);
const sample=(id,name,platform,state='stopped')=>({id,name,platform,image,desired_state:state,observed_state:state,revision:1,observed_revision:1,last_logs:'Connected to the gateway\nReady for /ping',last_error:'',last_checked:new Date().toISOString()});

(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-dev-shm-usage']}: {})});
 await fs.mkdir('test-results',{recursive:true});
 const errors=[];let page;
 try{
  const context=await browser.newContext({baseURL:base,viewport:{width:1360,height:1000},serviceWorkers:'block'});
  page=await context.newPage();page.setDefaultTimeout(10000);
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('status of 4'))errors.push(m.text());});
  let bots=[],mfa=false,unlocked=false,runner=true,expiry=null,csrf='fixture-csrf',failSave=false,accessMs=600000;
  const mutations=[];
  const settings={group:'БВТ2302',course:4,program:'09.03.01',semester_start:'2026-09-01',semester_end:'2027-01-31',anchor_monday:'2026-08-31',anchor_parity:'odd',timezone:'Europe/Moscow',revision:1,configured:true};
  await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();
   const json=(value,status=200)=>route.fulfill({status,json:value});
   if(path==='/api/auth/status')return json({needs_setup:false});
   if(path==='/api/auth/me')return json({user:{id:'fixture-owner',name:'Владелец',email:'owner@example.test',role:'admin',status:'active',subgroup:1,group_role:'none'},csrf});
   if(path==='/api/schedule/settings')return json(settings);
   if(path==='/api/schedule/rules')return json([]);
   if(path==='/api/notifications/inbox')return json({items:[],popups:[],total:0,unread:0,as_of:new Date().toISOString()});
   if(path==='/api/notifications/config')return json({enabled:false,vapid_public_key:''});
   if(path==='/api/auth/bots/guard')return json({owner:true,mfa_enabled:mfa,unlocked,runner_ready:runner,server_time:new Date().toISOString(),access_expires_at:expiry,limits:{max_bots:3,memory_mb:192,cpus:0.35}});
   if(path==='/api/auth/bots/unlock'){
    if(req.postDataJSON().code!=='123456')return json({detail:'mfa_invalid'},401);
    csrf='elevated-csrf';unlocked=true;expiry=new Date(Date.now()+accessMs).toISOString();return json({csrf,expires_in:600});
   }
   if(path==='/api/auth/bots/lock'){unlocked=false;expiry=null;return json({ok:true});}
   if(path.startsWith('/api/auth/bots')){
    if(!unlocked)return json({detail:'bot_mfa_step_up_required'},403);
    if(method==='GET')return json(bots);
    assert.equal(req.headers()['x-csrf-token'],csrf,'mutations must use current CSRF');
    const data=req.postData()?req.postDataJSON():null;mutations.push({path,method,data});
    if(method==='POST'&&path==='/api/auth/bots'){
     if(failSave)return json({detail:'invalid_bot_token'},422);
     bots.push({...sample('bot-'+bots.length,data.name,data.platform),image:data.image});return json(bots.at(-1),201);
    }
    const bot=bots.find(b=>b.id===path.split('/')[4]);assert(bot,'unknown fixture bot');
    if(method==='DELETE'){bots=bots.filter(b=>b!==bot);return json({ok:true});}
    if(method==='PUT'){Object.assign(bot,{name:data.name,image:data.image,platform:data.platform,revision:bot.revision+1});return json(bot);}
    if(path.endsWith('/state')){bot.desired_state=data.desired_state;bot.revision++;return json(bot);}
    if(path.endsWith('/restart')){bot.desired_state='running';bot.revision++;return json(bot);}
   }
   throw new Error('Unexpected UI request: '+method+' '+path);
  });
  await page.addInitScript(()=>{localStorage.setItem('cf-language','ru');localStorage.setItem('cf-theme','light');});
  await page.goto(base+'/#admin');
  const openBots=async()=>{await page.getByRole('tab',{name:/^(Боты|Bots)$/}).click();await page.locator('.bot-hero').waitFor();};
  await openBots();
  await page.getByRole('heading',{name:'Сначала защитите аккаунт'}).waitFor();
  assert.equal(await page.getByRole('link',{name:'Настроить 2FA'}).getAttribute('href'),'#preferences');
  mfa=true;await page.reload();await openBots();
  await page.getByLabel('Код из приложения',{exact:true}).fill('000000');
  await page.getByRole('button',{name:'Открыть панель',exact:true}).click();
  await page.getByRole('alert').waitFor();
  await page.getByLabel('Код из приложения',{exact:true}).fill('123456');
  await page.getByRole('button',{name:'Открыть панель',exact:true}).click();
  await page.locator('.bot-empty').waitFor();
  await page.screenshot({path:'test-results/bots-empty-desktop.png',fullPage:true});

  await page.getByRole('button',{name:'Добавить бота',exact:true}).click();
  let dialog=page.getByRole('dialog');
  await dialog.getByRole('radio',{name:/Discord/}).check();
  await dialog.getByLabel('Название бота',{exact:true}).fill('Guild helper');
  await dialog.getByLabel('Образ приложения',{exact:true}).fill(image);
  await dialog.getByLabel('Bot Token из Discord',{exact:true}).fill('fixture.discord.'+'x'.repeat(40));
  await page.setViewportSize({width:390,height:844});
  assert.equal(await dialog.evaluate(e=>e.scrollWidth>e.clientWidth),false,'mobile bot form overflow');
  await page.screenshot({path:'test-results/bots-add-discord-mobile.png'});
  failSave=true;await dialog.getByRole('button',{name:'Подключить бота',exact:true}).click();
  await dialog.getByRole('alert').waitFor();assert((await dialog.getByRole('alert').innerText()).includes('токен'));
  failSave=false;await dialog.getByRole('button',{name:'Подключить бота',exact:true}).click();
  await page.getByRole('heading',{name:'Guild helper',exact:true}).waitFor();
  assert.equal(mutations.at(-1).data.platform,'discord');
  assert.equal(await page.getByRole('dialog').count(),0);
  await page.locator('.bot-card').getByRole('button',{name:'Запустить',exact:true}).click();
  await page.locator('.bot-status.starting').waitFor();
  const stop=page.locator('.bot-card').getByRole('button',{name:'Остановить',exact:true});
  assert(await stop.isEnabled(),'can stop a bot while its start is pending');
  await stop.click();
  assert.equal(mutations.at(-1).data.desired_state,'stopped');

  // Editing keeps the encrypted token when the password field is left empty.
  await page.getByRole('button',{name:'Настроить Guild helper',exact:true}).click();
  dialog=page.getByRole('dialog');
  assert.equal(await dialog.getByLabel('Bot Token из Discord',{exact:true}).inputValue(),'');
  await dialog.getByLabel('Название бота',{exact:true}).fill('Community helper');
  await dialog.getByRole('button',{name:'Сохранить изменения',exact:true}).click();
  await page.getByRole('heading',{name:'Community helper',exact:true}).waitFor();
  assert.equal(mutations.at(-1).data.token,'');
  await page.getByRole('button',{name:'Логи Community helper',exact:true}).click();
  await page.locator('.bot-log-output').waitFor();
  assert((await page.locator('.bot-log-output').innerText()).includes('Ready for /ping'));
  await page.getByRole('dialog').getByRole('button',{name:'Закрыть',exact:true}).click();

  bots=[sample('tg','Помощник группы','telegram','running'),sample('dc','Community helper','discord'),{...sample('err','Сломанный бот','discord','running'),observed_state:'error',last_error:'docker operation failed: pull'}];
  await page.getByRole('button',{name:'Обновить статусы',exact:true}).click();
  await page.locator('.bot-card').nth(2).waitFor();
  if(await page.locator('.toast button').count())await page.locator('.toast button').click();
  await page.getByLabel('Найти бота',{exact:true}).fill('несуществующий');
  await page.getByRole('heading',{name:'Ничего не найдено'}).waitFor();
  await page.getByRole('button',{name:'Сбросить фильтры'}).click();
  await page.getByRole('group',{name:'Платформа',exact:true}).getByRole('button',{name:'Discord',exact:true}).click();
  assert.equal(await page.locator('.bot-card').count(),2);
  await page.getByRole('group',{name:'Платформа',exact:true}).getByRole('button',{name:'Все',exact:true}).click();
  assert(await page.getByRole('button',{name:'Добавить бота',exact:true}).isDisabled());
  for(const lang of ['ru','en']){
   if(lang==='en'){await page.locator('.language-button').click();await page.getByRole('heading',{name:'My bots',exact:false}).waitFor();}
   for(const theme of ['light','dark','black','ultra-black']){
    await page.evaluate(t=>{document.documentElement.dataset.theme=t;},theme);
    for(const width of [360,390,768,1360,1700]){
     await page.setViewportSize({width,height:1000});
     assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${lang}/${theme}/${width} page overflow`);
    }
    await page.setViewportSize({width:1360,height:1000});
    await page.evaluate(()=>{document.activeElement?.blur();scrollTo(0,0);});
    if(lang==='ru'&&['light','dark'].includes(theme))await page.screenshot({path:`test-results/bots-${lang}-${theme}-desktop.png`,fullPage:true});
   }
  }
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'test-results/bots-en-black-mobile.png',fullPage:true});
  await page.getByRole('button',{name:'Delete Community helper',exact:true}).click();
  dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'Confirm',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.bot-card').length===2);
  assert.equal(mutations.at(-1).method,'DELETE');
  await page.getByRole('button',{name:'Lock panel',exact:true}).click();
  await page.getByRole('heading',{name:'Verify your identity'}).waitFor();
  assert.equal(await page.locator('.bot-card,.bot-log-output').count(),0);
  assert.equal(await page.locator('body').innerText().then(t=>t.includes('Ready for /ping')),false);
  // Local expiry must remove a form containing an unsaved password too.
  accessMs=1600;
  await page.getByLabel('Authenticator code',{exact:true}).fill('123456');
  await page.getByRole('button',{name:'Unlock panel',exact:true}).click();
  await page.getByRole('button',{name:'Add bot',exact:true}).click();
  await page.getByRole('dialog').getByLabel('BotFather token',{exact:true}).fill('unsaved-private-fixture-token');
  await page.getByRole('heading',{name:'Verify your identity'}).waitFor();
  assert.equal(await page.getByRole('dialog').count(),0,'expiry closes sensitive editor');
  assert.equal(await page.locator('.bot-card,.bot-log-output,input[type=password]').count(),0);
  await context.close();
  assert.deepEqual(errors,[]);
  console.log('Bot Manager UI: MFA, Discord create/edit, validation, pending stop, logs, search, filters, quotas, delete, lock; RU/EN × 4 themes × 5 widths passed.');
 }catch(error){
  if(page&&!page.isClosed()){
   await page.screenshot({path:'test-results/bots-ui-failure.png',fullPage:true}).catch(()=>{});
   console.error('Bot UI diagnostic:',errors,await page.locator('body').innerText().catch(()=>''));
  }
  throw error;
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
