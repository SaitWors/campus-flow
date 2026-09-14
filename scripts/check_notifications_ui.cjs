// Real HTTP UI checks on the disposable CI database. No real users or devices.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const base='http://localhost:8080';
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const context=await browser.newContext({baseURL:base,viewport:{width:1360,height:900}});
  const errors=[];context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
  const session=await context.request.post('/api/auth/login',{data:{email:'admin@example.test',password:'Integration-test-password-2026'}});
  assert(session.ok());const me=await session.json();
  await context.addInitScript(()=>{if(location.origin==='http://localhost:8080'){
    localStorage.setItem('cf-language','en');
    localStorage.setItem('cf-theme','dark');
  }});
  const page=await context.newPage();page.setDefaultTimeout(20000);
  await page.goto('/#notifications');
  await page.getByRole('heading',{name:'Notifications',exact:true}).waitFor();
  // The important announcement stays visible until explicitly acknowledged.
  const popup=page.getByRole('alert',{name:'Important announcement'});
  await popup.waitFor();await popup.getByRole('button',{name:'Got it'}).click();
  await popup.waitFor({state:'hidden'});
  await page.reload();await page.getByRole('heading',{name:'Notifications',exact:true}).waitFor();
  assert.equal(await popup.count(),0);
  const bell=page.getByRole('button',{name:/^Notifications(?: \(\d+\))?$/});
  await bell.click();await page.getByRole('dialog').waitFor();
  await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
  await page.goto('/#admin');await page.getByRole('tab',{name:'Announcements',exact:true}).click();
  await page.getByLabel('Title',{exact:true}).fill('Tomorrow: remote labs');
  await page.getByLabel('Message',{exact:true}).fill('Use the class link in your timetable. This is a CI preview.');
  await page.getByRole('button',{name:'Preview',exact:true}).click();
  const preview=page.getByRole('dialog');
  await preview.getByRole('heading',{name:'Tomorrow: remote labs'}).waitFor();
  await preview.getByRole('button',{name:'Publish announcement',exact:true}).click();
  await preview.waitFor({state:'hidden'});
  await popup.waitFor();await popup.getByRole('button',{name:'Got it'}).click();
  await popup.waitFor({state:'hidden'});
  await fs.mkdir('test-results',{recursive:true});
  await page.screenshot({path:'test-results/announcements-desktop.png',fullPage:true});
  console.log('VISUAL_DESKTOP_JPEG:'+(await page.screenshot({type:'jpeg',quality:55,fullPage:false})).toString('base64'));
  for(const width of [360,390,768]){
   await page.setViewportSize({width,height:844});
   for(const route of ['schedule','calendar','queues','notifications','preferences','admin']){
    await page.goto('/#'+route);
    await page.locator('main h1').waitFor();
    if(route==='preferences')await page.getByRole('heading',{name:'Notification settings',exact:true}).waitFor();
    if(route==='admin')await page.getByRole('tab',{name:'Announcements',exact:true}).click();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'overflow '+route+' '+width);
    if(width===390&&['notifications','preferences','admin','calendar'].includes(route))await page.screenshot({path:'test-results/mobile-'+route+'.png',fullPage:true});
    if(width===390&&route==='notifications')console.log('VISUAL_MOBILE_JPEG:'+(await page.screenshot({type:'jpeg',quality:60,fullPage:false})).toString('base64'));
   }
  }
  await page.setViewportSize({width:390,height:844});
  await page.goto('/#preferences');
  const settings=page.locator('#notification-settings');
  await settings.getByLabel('Quiet hours',{exact:true}).check();
  await settings.getByLabel('From',{exact:true}).fill('23:00');
  await settings.getByLabel('Until',{exact:true}).fill('07:30');
  await settings.getByLabel('Show message text on the lock screen',{exact:true}).uncheck();
  await settings.getByRole('button',{name:'Save',exact:true}).click();
  await page.getByText('Settings saved',{exact:true}).waitFor();
  await page.reload();await settings.getByLabel('From',{exact:true}).waitFor();
  assert.equal(await settings.getByLabel('From',{exact:true}).inputValue(),'23:00');
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('.t-badge').evaluate(el=>getComputedStyle(el).animationName),'none');
  const manifest=await context.request.get('/manifest.webmanifest');
  assert(manifest.ok());assert.equal((await manifest.json()).display,'standalone');
  for(const file of ['icon-192.png','icon-512.png','apple-touch-icon.png']){
   const icon=await context.request.get('/icons/'+file);assert(icon.ok());assert.equal((await icon.body())[0],137);
  }
  const swResponse=await context.request.get('/sw.js');
  assert.match(swResponse.headers()['cache-control'],/no-cache/);
  assert(swResponse.headers()['content-security-policy']);
  // Browser permission prompt must never be requested automatically.
  assert.equal(await page.evaluate(()=>Notification.permission),'default');
  await context.grantPermissions(['notifications'],{origin:base});
  await page.evaluate(async owner=>{
   await navigator.serviceWorker.register('/sw.js');
   const reg=await navigator.serviceWorker.ready;
   await new Promise(resolve=>{const channel=new MessageChannel();channel.port1.onmessage=resolve;reg.active.postMessage({type:'push-owner',owner},[channel.port2]);});
  },me.user.id);
  // Inject a browser push event through CDP after the app tab is closed.
  // This tests the real SW lifecycle, not an external push-provider delivery.
  const control=await context.newPage(),cdp=await context.newCDPSession(control);
  let registrationId;
  cdp.on('ServiceWorker.workerRegistrationUpdated',({registrations})=>{
   const r=registrations.find(r=>r.scopeURL===base+'/');if(r)registrationId=r.registrationId;
  });
  await cdp.send('ServiceWorker.enable');
  for(let n=0;n<50&&!registrationId;n++)await new Promise(r=>setTimeout(r,100));
  assert(registrationId,'service worker registration');
  await page.close();
  const payload={id:'test-push-after-close',user_id:me.user.id,title:'Background delivery',body:'App tab is closed',route:'#notifications'};
  await cdp.send('ServiceWorker.deliverPushMessage',{origin:base,registrationId,data:JSON.stringify(payload)});
  let shown=[];
  for(let n=0;n<50;n++){
   const worker=context.serviceWorkers().find(w=>w.url()===base+'/sw.js');
   if(worker)shown=await worker.evaluate(async()=> (await self.registration.getNotifications()).map(n=>({title:n.title,body:n.body})));
   if(shown.some(n=>n.title==='Background delivery'))break;
   await new Promise(r=>setTimeout(r,100));
  }
  assert(shown.some(n=>n.title==='Background delivery'&&n.body==='App tab is closed'),'push while app tab is closed');
  assert.deepEqual(errors,[]);
  console.log('PASS: announcement publishing, inbox, read receipts, mobile layouts, preferences, PWA assets, no automatic permission prompt, real service worker push with app tab closed');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
