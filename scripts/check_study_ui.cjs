// Run after smoke.py, verify_pr3.py and verify_study.py on disposable data only.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const base=process.env.CAMPUS_BASE_URL||'http://localhost:8080';
const password='Integration-test-password-2026';
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-dev-shm-usage']}: {})});
 const errors=[],retiredRequests=[];
 const contexts=[];
 try{
  await fs.mkdir('test-results',{recursive:true});
  async function account(email,pass=password){
   const context=await browser.newContext({baseURL:base,viewport:{width:1360,height:940}});contexts.push(context);
   context.on('page',p=>{p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/queues'))retiredRequests.push(r.url());});});
   await context.addInitScript(()=>{try{localStorage.setItem('cf-language','en');}catch{}});
   assert((await context.request.post('/api/auth/login',{data:{email,password:pass}})).ok());
   const page=await context.newPage();page.setDefaultTimeout(15000);return {page,context};
  }
  const {page,context}=await account('admin@example.test');
  await page.goto('/');await page.getByRole('heading',{name:'Today',exact:true}).waitFor();
  await page.getByText('Study hub browser assignment',{exact:true}).first().waitFor();
  for(const width of [360,390,768,1360]){
   await page.setViewportSize({width,height:940});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Today overflow '+width);
  }
  await page.screenshot({path:'test-results/study-today-desktop.png',fullPage:true});
  await page.goto('/#assignments');await page.getByRole('heading',{name:'Assignments',exact:true}).waitFor();
  await page.getByRole('button',{name:'Add assignment',exact:true}).first().click();
  const dialog=page.getByRole('dialog');
  await dialog.getByLabel('Assignment number / title',{exact:true}).fill('Browser private progress');
  await dialog.getByLabel('Assignment details',{exact:true}).fill('Created through the study interface.');
  await dialog.getByLabel('Material link (https://)',{exact:true}).fill('https://example.test/browser-material');
  await dialog.getByRole('button',{name:'Save',exact:true}).click();await dialog.waitFor({state:'hidden'});
  const card=page.locator('.assignment-card').filter({has:page.getByRole('heading',{name:'Browser private progress',exact:true})});
  await card.waitFor();
  for(const width of [360,390,768,1360]){
   await page.setViewportSize({width,height:940});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Assignments overflow '+width);
  }
  await card.getByRole('link',{name:'Materials · Browser private progress',exact:true}).waitFor();
  await page.goto('/#subjects');await page.getByRole('heading',{name:'Subjects',exact:true}).waitFor();
  await page.locator('.subject-card').first().click();
  await page.getByRole('heading',{name:'Course requirements',exact:true}).waitFor();
  await page.getByRole('link',{name:'Study integration materials',exact:false}).waitFor();
  await page.getByRole('button',{name:'Edit subject details',exact:true}).click();
  await page.getByRole('dialog').getByLabel('Course requirements',{exact:true}).fill('Browser course requirement');
  await page.getByRole('dialog').getByRole('button',{name:'Save',exact:true}).click();
  await page.getByText('Browser course requirement',{exact:true}).waitFor();
  for(const width of [360,390,768,1360]){
   await page.setViewportSize({width,height:940});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Subject overflow '+width);
  }
  await page.screenshot({path:'test-results/study-subject-desktop.png',fullPage:true});
  const {page:student,context:studentContext}=await account('student0@example.test',password+'new');
  await student.goto('/#assignments');await student.getByRole('heading',{name:'Assignments',exact:true}).waitFor();
  assert.equal(await student.getByRole('button',{name:'Add assignment',exact:true}).count(),0);
  const progressSaved=student.waitForResponse(r=>r.url().endsWith('/progress')&&r.request().method()==='PUT'&&r.ok());
  await student.getByLabel('My progress · Browser private progress',{exact:true}).selectOption('ready');await progressSaved;
  await student.reload();await student.getByLabel('My progress · Browser private progress',{exact:true}).waitFor();
  assert.equal(await student.getByLabel('My progress · Browser private progress',{exact:true}).inputValue(),'ready');
  const adminAssignments=await (await context.request.get('/api/schedule/assignments')).json();
  assert.equal(adminAssignments.find(a=>a.title==='Browser private progress').progress.status,'not_started');
  const studentAssignments=await (await studentContext.request.get('/api/schedule/assignments')).json();
  assert.equal(studentAssignments.find(a=>a.title==='Browser private progress').progress.status,'ready');
  await student.goto('/#schedule');await student.getByRole('heading',{name:'Schedule',exact:true}).waitFor();
  assert.equal(await student.getByLabel('Subgroup',{exact:true}).inputValue(),'1');
  await student.getByRole('button',{name:'Day',exact:true}).click();
  await student.locator('.display-options summary').click();
  await student.getByRole('checkbox',{name:'Compact cards',exact:true}).check();
  await student.reload();await student.getByRole('heading',{name:'Schedule',exact:true}).waitFor();
  assert.equal(await student.getByRole('button',{name:'Day',exact:true}).getAttribute('aria-pressed'),'true');
  await student.locator('.display-options summary').click();
  assert.equal(await student.getByRole('checkbox',{name:'Compact cards',exact:true}).isChecked(),true);
  await student.getByRole('button',{name:'Month',exact:true}).click();
  await student.setViewportSize({width:390,height:844});
  assert.equal(await student.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile month overflow');
  await student.screenshot({path:'test-results/study-month-mobile.png',fullPage:true});
  await student.goto('/#today');await student.getByRole('heading',{name:'Today',exact:true}).waitFor();
  await student.screenshot({path:'test-results/study-today-mobile.png',fullPage:true});
  await student.getByRole('button',{name:'More',exact:true}).click();
  await student.getByRole('link',{name:'Settings',exact:true}).last().click();
  await student.getByRole('heading',{name:'Settings',exact:true}).waitFor();
  assert.deepEqual(retiredRequests,[]);assert.deepEqual(errors,[]);
  console.log('PASS: Today, responsive assignments/subjects, real creation/editing, private progress persistence, saved filters, mobile month/More, no retired API requests or page errors');
 }finally{for(const context of contexts)await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
