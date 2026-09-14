// Disposable CI only: real guest and two-account private conversation flow.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const base='http://localhost:8080';
(async()=>{
 const browser=await chromium.launch({headless:true});
 const errors=[];
 try{
  const guest=await browser.newContext({baseURL:base,viewport:{width:390,height:844}});
  guest.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
  await guest.addInitScript(()=>{localStorage.setItem('cf-language','en');localStorage.setItem('cf-theme','dark');});
  const page=await guest.newPage();page.setDefaultTimeout(20000);
  await page.goto('/');
  await page.getByRole('button',{name:'View timetable as a guest'}).click();
  await page.getByText('Guest mode',{exact:true}).waitFor();
  await page.locator('.schedule-board').waitFor();
  assert.equal(await page.getByRole('button',{name:'Add class',exact:true}).count(),0);
  assert.equal(await page.getByRole('button',{name:/^Notifications/}).count(),0);
  for(const route of ['guest','guest-calendar']){
   await page.goto('/#'+route);await page.locator('.schedule-board').waitFor();
   for(const width of [360,390,768]){
    await page.setViewportSize({width,height:844});
    await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'guest overflow '+width);
   }
  }
  await fs.mkdir('test-results',{recursive:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'test-results/guest-mobile.png',fullPage:true});
  await guest.close();

  const admin=await browser.newContext({baseURL:base,viewport:{width:1360,height:900}});
  const student=await browser.newContext({baseURL:base,viewport:{width:390,height:844}});
  for(const [context,email,password] of [[admin,'admin@example.test','Integration-test-password-2026'],[student,'student0@example.test','Integration-test-password-2026new']]){
   context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
   await context.addInitScript(()=>{localStorage.setItem('cf-language','en');localStorage.setItem('cf-theme','dark');});
   assert((await context.request.post('/api/auth/login',{data:{email,password}})).ok());
  }
  const a=await admin.newPage(),s=await student.newPage();a.setDefaultTimeout(20000);s.setDefaultTimeout(20000);
  await a.goto('/#admin');await a.getByRole('tab',{name:'Members',exact:true}).click();
  const row=a.locator('.member-row').filter({hasText:'admin@example.test'});
  await row.getByRole('button',{name:'Edit',exact:true}).click();
  let dialog=a.getByRole('dialog');
  await dialog.getByLabel('Additional group role',{exact:true}).selectOption('deputy');
  await dialog.getByRole('button',{name:'Save',exact:true}).click();await dialog.waitFor({state:'hidden'});
  assert.equal((await (await admin.request.get('/api/auth/me')).json()).user.group_role,'deputy');
  await row.getByRole('button',{name:'Edit',exact:true}).click();
  dialog=a.getByRole('dialog');await dialog.getByLabel('Additional group role',{exact:true}).selectOption('head');
  await dialog.getByRole('button',{name:'Save',exact:true}).click();await dialog.waitFor({state:'hidden'});

  await s.goto('/#questions');await s.getByRole('button',{name:'Ask a question',exact:true}).click();
  dialog=s.getByRole('dialog');
  const me=await (await admin.request.get('/api/auth/me')).json();
  await dialog.getByLabel('Recipient',{exact:true}).selectOption(me.user.id);
  await dialog.getByLabel('Subject',{exact:true}).fill('Mobile private question');
  await dialog.getByLabel('Your question',{exact:true}).fill('Could you clarify the lab deadline?');
  await dialog.getByRole('button',{name:'Send',exact:true}).click();
  await dialog.getByRole('heading',{name:'Mobile private question',exact:true}).waitFor();
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  await a.goto('/#questions');
  await a.getByRole('button').filter({has:a.getByRole('heading',{name:'Mobile private question',exact:true})}).click();
  const thread=a.getByRole('dialog');
  await thread.getByLabel('Message',{exact:true}).fill('The deadline is Friday. Thank you for checking.');
  await thread.getByRole('button',{name:'Reply',exact:true}).click();
  await thread.getByText('The deadline is Friday. Thank you for checking.',{exact:true}).waitFor();
  await s.getByRole('button').filter({has:s.getByRole('heading',{name:'Mobile private question',exact:true})}).click();
  const mobile=s.getByRole('dialog');
  await mobile.getByText('The deadline is Friday. Thank you for checking.',{exact:true}).waitFor();
  for(const width of [360,390,768]){
   await s.setViewportSize({width,height:844});
   assert.equal(await s.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'conversation overflow '+width);
  }
  await s.setViewportSize({width:390,height:844});
  await s.screenshot({path:'test-results/questions-mobile.png',fullPage:true});
  console.log('VISUAL_QUESTIONS_JPEG:'+(await s.screenshot({type:'jpeg',quality:60})).toString('base64'));
  await mobile.getByRole('button',{name:'Close question',exact:true}).click();
  await mobile.getByRole('button',{name:'Reopen',exact:true}).waitFor();
  await mobile.getByRole('button',{name:'Close',exact:true}).click();
  await s.getByRole('button',{name:'Closed',exact:true}).click();
  await s.getByRole('heading',{name:'Mobile private question',exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('PASS: mobile guest mode, administrator group-role editing, private question creation, staff reply and closure');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
