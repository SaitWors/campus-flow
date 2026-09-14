// Browser checks on the disposable CI instance only.
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const context=await browser.newContext({baseURL:'http://localhost:8080',viewport:{width:1360,height:900}});
  const errors=[];context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
  const login=await context.request.post('/api/auth/login',{data:{email:'admin@example.test',password:'Integration-test-password-2026'}});
  if(!login.ok())throw new Error('CI sign-in failed');
  await context.addInitScript(()=>{if(location.origin==='http://localhost:8080')localStorage.setItem('cf-language','en');});
  const page=await context.newPage();page.setDefaultTimeout(20000);
  await page.goto('/#preferences');
  await fs.mkdir('test-results',{recursive:true});
  for(const [label,id,color] of [['Black','black','rgb(16, 16, 16)'],['Ultra black','ultra-black','rgb(0, 0, 0)']]){
   await page.getByRole('button',{name:label,exact:true}).click();
   await page.waitForFunction(({id,color})=>document.documentElement.dataset.theme===id&&getComputedStyle(document.body).backgroundColor===color,{id,color});
   await page.reload();
   await page.getByRole('button',{name:label,exact:true}).waitFor();
   if(await page.evaluate(()=>localStorage.getItem('cf-theme'))!==id)throw new Error('Theme persistence');
   await page.emulateMedia({colorScheme:'light'});
   if(await page.locator('html').getAttribute('data-theme')!==id)throw new Error('Explicit theme followed OS');
   await page.screenshot({path:'test-results/'+id+'.png',fullPage:true});
  }
  await page.getByRole('button',{name:'System',exact:true}).click();
  await page.emulateMedia({colorScheme:'dark'});
  await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
  await page.emulateMedia({colorScheme:'light'});
  await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Ultra black',exact:true}).click();
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw new Error('Horizontal overflow');
  await page.screenshot({path:'test-results/ultra-black-mobile.png',fullPage:true});
  await page.setViewportSize({width:1360,height:900});
  await page.goto('/#schedule');
  await page.getByRole('button',{name:'Add class',exact:true}).click();
  await page.getByLabel('Subject',{exact:true}).fill('Технологии баз данных');
  const english=page.getByLabel(/English subject name/);
  await page.waitForFunction(()=>[...document.querySelectorAll('input')].some(i=>/data/i.test(i.placeholder)),null,{timeout:20000});
  if(await english.inputValue()!=='')throw new Error('Automatic text became a manual override');
  await english.fill('Custom English name');
  await page.getByLabel('Subject',{exact:true}).fill('Распределённые системы');
  await page.waitForTimeout(1000);
  if(await english.inputValue()!=='Custom English name')throw new Error('Manual wording was overwritten');
  await page.screenshot({path:'test-results/translation-form.png',fullPage:true});
  if(errors.length)throw new Error(errors.join('\n'));
  console.log('PASS: black themes, persistence, OS switching, mobile layout and translation form');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
