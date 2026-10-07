// Run after smoke.py, verify_pr3.py and verify_study.py on disposable data only.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {randomBytes}=require('node:crypto');
const {deflateSync}=require('node:zlib');
const base=process.env.CAMPUS_BASE_URL||'http://localhost:8080';
const password='Integration-test-password-2026';

function crc32(buffer){let crc=0xffffffff;for(const byte of buffer){crc^=byte;for(let bit=0;bit<8;bit++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}return (crc^0xffffffff)>>>0;}
function chunk(kind,data){const name=Buffer.from(kind),length=Buffer.alloc(4),checksum=Buffer.alloc(4);length.writeUInt32BE(data.length);checksum.writeUInt32BE(crc32(Buffer.concat([name,data])));return Buffer.concat([length,name,data,checksum]);}
function largePng(){
 const width=256,height=256,header=Buffer.alloc(13);header.writeUInt32BE(width,0);header.writeUInt32BE(height,4);header[8]=8;header[9]=2;
 const raw=Buffer.alloc(height*(width*3+1));for(let row=0;row<height;row++)randomBytes(width*3).copy(raw,row*(width*3+1)+1);
 return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]);
}
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-dev-shm-usage']}: {})});
 const errors=[],contexts=[],created=[],uploadsInFlight=new Set();let adminContext,maxConcurrentUploads=0;
 try{
  await fs.mkdir('test-results',{recursive:true});
  async function account(email,pass=password){
   const context=await browser.newContext({baseURL:base,viewport:{width:1360,height:940},acceptDownloads:true});contexts.push(context);
   context.on('page',page=>{
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(request.method()==='POST'&&/^\/api\/schedule\/subjects\/[^/]+\/materials$/.test(new URL(request.url()).pathname)){uploadsInFlight.add(request);maxConcurrentUploads=Math.max(maxConcurrentUploads,uploadsInFlight.size);}});
    // CDP can report requestfinished after XHR.onload has started the next file.
    // Response arrival gives a stable boundary while still catching parallel POSTs.
    const finished=request=>uploadsInFlight.delete(request);page.on('response',response=>finished(response.request()));page.on('requestfailed',finished);
   });
   await context.addInitScript(()=>{try{localStorage.setItem('cf-language','en');}catch{}});
   const login=await context.request.post('/api/auth/login',{data:{email,password:pass}});assert(login.ok(),'Cannot sign in '+email);
   const page=await context.newPage();page.setDefaultTimeout(20000);return {context,page};
  }
  const {context,page}=await account('admin@example.test');adminContext=context;
  const subjects=await (await context.request.get('/api/schedule/subjects')).json();assert(subjects.length,'Study verification must create a subject');
  const subject=subjects[0],route='/#subject/'+encodeURIComponent(subject.key),list='/api/schedule/subjects/'+encodeURIComponent(subject.key)+'/materials';
  await page.goto(route);await page.getByRole('heading',{name:/^Subject materials/}).waitFor();
  await page.getByRole('button',{name:'Upload files',exact:true}).click();
  const dialog=page.getByRole('dialog'),png=largePng(),text=Buffer.from('Browser materials notes\n'+'Read these course notes.\n'.repeat(3200)),stamp=Date.now();
  assert(png.length>65536,'PNG fixture must exercise uploads beyond the old body limit');
  const imageName='Лекция & схема '+stamp+'.png',notesName='Конспект '+stamp+'.txt',title='Browser lecture '+stamp,notesTitle='Browser notes '+stamp;
  await dialog.getByLabel('Files',{exact:true}).setInputFiles([{name:imageName,mimeType:'image/png',buffer:png},{name:notesName,mimeType:'text/plain',buffer:text}]);
  await dialog.getByLabel('Material title · '+imageName,{exact:true}).fill(title);
  await dialog.getByLabel('Description · '+imageName,{exact:true}).fill('Lecture image uploaded from the browser.');
  await dialog.getByLabel('Category · '+imageName,{exact:true}).selectOption('lecture');
  await dialog.getByLabel('Material title · '+notesName,{exact:true}).fill(notesTitle);
  await dialog.getByLabel('Category · '+notesName,{exact:true}).selectOption('notes');
  await dialog.getByRole('button',{name:'Upload',exact:true}).click();await dialog.waitFor({state:'hidden'});
  const getRows=async()=>{const response=await context.request.get(list+'?limit=100');assert(response.ok());return (await response.json()).items;};
  let rows=await getRows();const image=rows.find(item=>item.title===title),notes=rows.find(item=>item.title===notesTitle);assert(image&&notes,'Both selected files must be uploaded');created.push(image.id,notes.id);
  assert.equal(maxConcurrentUploads,1,'Selected files must be sent sequentially');
  assert.equal(image.size_bytes,png.length);assert.equal(notes.size_bytes,text.length);assert.equal(image.original_filename,imageName);
  // Forward a real successful write, then replace its response: a 500 does not prove rollback.
  const committedName='committed-response-'+stamp+'.txt',committedTitle='Browser committed response '+stamp,committedBytes=Buffer.from('Committed upload response regression\n');
  const committedRoute=url=>url.pathname===list&&url.searchParams.get('filename')===committedName;let committed;
  await page.route(committedRoute,async intercepted=>{
   const response=await intercepted.fetch();assert.equal(response.status(),201,'Fault fixture must first commit a real upload');
   committed=await response.json();created.push(committed.id);
   await intercepted.fulfill({status:500,contentType:'text/plain',body:'Internal Server Error'});
  });
  await page.getByRole('button',{name:'Upload files',exact:true}).click();
  await dialog.getByLabel('Files',{exact:true}).setInputFiles({name:committedName,mimeType:'text/plain',buffer:committedBytes});
  await dialog.getByLabel('Material title · '+committedName,{exact:true}).fill(committedTitle);
  await dialog.getByRole('button',{name:'Upload',exact:true}).click();await dialog.getByRole('alert').waitFor();
  assert(committed&&((await getRows()).some(item=>item.id===committed.id)),'The committed row must remain visible despite the 500 response');
  const committedFile=await context.request.get('/api/schedule/materials/'+committed.id+'/file');assert(committedFile.ok());assert.deepEqual(await committedFile.body(),committedBytes);
  try{await dialog.locator('.material-upload-item.unknown').waitFor({timeout:3000});}
  catch(error){console.error('Committed-response diagnostic:',JSON.stringify({id:committed.id,text:await dialog.innerText()}));throw error;}
  await dialog.getByText('Check the materials list before uploading this file again.',{exact:true}).waitFor();
  assert(!/action not completed/i.test(await dialog.innerText()),'An uncertain response must not claim the write failed');
  assert.equal(await dialog.getByRole('button',{name:'Upload',exact:true}).isDisabled(),true,'The uncertain file must not be offered for retry');
  await dialog.locator('.form-actions').getByRole('button',{name:'Close',exact:true}).click();await dialog.waitFor({state:'hidden'});await page.unroute(committedRoute);
  const imageCard=page.locator('.material-card').filter({has:page.getByRole('heading',{name:title,exact:true})});await imageCard.waitFor();
  await imageCard.getByRole('button',{name:'Preview · '+title,exact:true}).click();
  await page.getByRole('dialog').getByRole('img',{name:title,exact:true}).waitFor();
  await page.waitForFunction(()=>{const image=document.querySelector('dialog .material-preview img');return image&&image.complete&&image.naturalWidth===256;});
  await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
  // A corrupt response exercises the browser decoder failure after a real successful preview.
  const previewRoute='**/api/schedule/materials/'+image.id+'/file?inline=true';
  let previewFaultHits=0;
  await page.route(previewRoute,route=>{previewFaultHits++;return route.fulfill({status:200,contentType:'image/png',body:Buffer.from([137,80,78,71,13,10,26,10])});});
  await imageCard.getByRole('button',{name:'Preview · '+title,exact:true}).click();
  try{await page.getByRole('dialog').getByRole('alert').filter({hasText:'Unable to display this image. Try downloading the file.'}).waitFor({timeout:3000});}
  catch(error){console.error('Preview fault diagnostic:',JSON.stringify({hits:previewFaultHits,text:await page.getByRole('dialog').innerText()}));throw error;}
  await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();await page.unroute(previewRoute);
  const [download]=await Promise.all([page.waitForEvent('download'),imageCard.getByRole('button',{name:'Download · '+title,exact:true}).click()]);
  assert.equal(download.suggestedFilename(),imageName);assert.deepEqual(await fs.readFile(await download.path()),png);
  await imageCard.getByRole('button',{name:'Edit material · '+title,exact:true}).click();
  const editedTitle=title+' edited';await page.getByRole('dialog').getByLabel('Material title',{exact:true}).fill(editedTitle);
  await page.getByRole('dialog').getByLabel('Description',{exact:true}).fill('Edited description visible to classmates.');
  await page.getByRole('dialog').getByLabel('Category',{exact:true}).selectOption('notes');
  await page.getByRole('dialog').getByRole('button',{name:'Save',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
  await page.getByRole('heading',{name:editedTitle,exact:true}).waitFor();
  await page.getByLabel('Find material',{exact:true}).fill(editedTitle);
  await page.getByLabel('Material category',{exact:true}).selectOption('lecture');await page.getByText('No materials match your filters',{exact:true}).waitFor();
  await page.getByLabel('Material category',{exact:true}).selectOption('notes');
  await page.getByLabel('Find material',{exact:true}).fill(editedTitle);await page.getByRole('heading',{name:editedTitle,exact:true}).waitFor();
  const managerSession=await (await context.request.get('/api/auth/me')).json(),pagingTitle='Browser paging '+stamp;
  for(let index=0;index<21;index++){
   const parameters=new URLSearchParams({filename:'page-'+index+'.txt',title:pagingTitle+' '+index,category:'other'});
   const response=await context.request.post(list+'?'+parameters,{data:Buffer.from('Pagination fixture '+index),headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':managerSession.csrf}});assert(response.ok(),'Pagination fixture upload');created.push((await response.json()).id);
  }
  await page.getByLabel('Find material',{exact:true}).fill(pagingTitle);await page.getByLabel('Material category',{exact:true}).selectOption('other');
  const pagination=page.locator('.material-pagination');await pagination.getByText('Page 1 of 2',{exact:true}).waitFor();
  assert.equal(await page.locator('.material-card').count(),20);await pagination.getByRole('button',{name:'Next',exact:true}).click();
  await pagination.getByText('Page 2 of 2',{exact:true}).waitFor();await page.waitForFunction(()=>document.querySelectorAll('.material-card').length===1);assert.equal(await page.locator('.material-card').count(),1);
  await pagination.getByRole('button',{name:'Previous',exact:true}).click();await pagination.getByText('Page 1 of 2',{exact:true}).waitFor();await page.waitForFunction(()=>document.querySelectorAll('.material-card').length===20);
  await page.getByLabel('Find material',{exact:true}).fill(editedTitle);await page.getByLabel('Material category',{exact:true}).selectOption('notes');await page.getByRole('heading',{name:editedTitle,exact:true}).waitFor();
  const {page:student,context:studentContext}=await account('student0@example.test',password+'new');
  await student.goto(route);await student.getByRole('heading',{name:/^Subject materials/}).waitFor();
  await student.getByLabel('Find material',{exact:true}).fill(editedTitle);await student.getByRole('heading',{name:editedTitle,exact:true}).waitFor();
  assert.equal(await student.getByRole('button',{name:'Upload files',exact:true}).count(),0);
  assert.equal(await student.getByRole('button',{name:/^Edit material · /}).count(),0);assert.equal(await student.getByRole('button',{name:/^Delete material · /}).count(),0);
  const [studentDownload]=await Promise.all([student.waitForEvent('download'),student.getByRole('button',{name:'Download · '+editedTitle,exact:true}).click()]);assert.deepEqual(await fs.readFile(await studentDownload.path()),png);
  const studentSession=await (await studentContext.request.get('/api/auth/me')).json();
  assert.equal((await studentContext.request.post(list+'?filename=denied.txt&title=Denied&category=notes',{data:'student write',headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':studentSession.csrf}})).status(),403);
  for(const lang of ['en','ru']){
   if(lang==='ru'){
    const translated=student.waitForResponse(response=>new URL(response.url()).pathname===list&&response.request().method()==='GET'&&response.ok());
    await student.getByRole('button',{name:'Language',exact:true}).click();await translated;
    await student.getByLabel('Найти материал',{exact:true}).fill(editedTitle);
    await student.getByRole('button',{name:'Скачать · '+editedTitle,exact:true}).waitFor();
   }
   for(const theme of ['light','dark','black','ultra-black']){
    await student.evaluate(value=>document.documentElement.dataset.theme=value,theme);
    for(const width of [360,390,768,1360]){await student.setViewportSize({width,height:940});assert.equal(await student.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Materials overflow '+lang+' '+theme+' '+width);}
   }
  }
  await student.setViewportSize({width:390,height:844});await student.screenshot({path:'test-results/materials-student-mobile.png',fullPage:true});
  await page.setViewportSize({width:1360,height:940});await page.screenshot({path:'test-results/materials-manager-desktop.png',fullPage:true});
  const guest=await browser.newContext({baseURL:base});contexts.push(guest);
  await guest.addInitScript(()=>{try{localStorage.setItem('cf-language','en');}catch{}});
  assert.equal((await guest.request.get(list)).status(),401);assert.equal((await guest.request.get('/api/schedule/materials/'+image.id+'/file')).status(),401);assert.equal((await guest.request.get('/api/schedule/materials/'+image.id+'/file?inline=true')).status(),401);
  const guestPage=await guest.newPage();await guestPage.goto(route);await guestPage.getByRole('button',{name:'Sign in',exact:true}).waitFor();assert.equal(await guestPage.getByRole('heading',{name:/^Subject materials/}).count(),0);
  const editedCard=page.locator('.material-card').filter({has:page.getByRole('heading',{name:editedTitle,exact:true})});
  await editedCard.getByRole('button',{name:'Delete material · '+editedTitle,exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Cancel',exact:true}).click();assert((await getRows()).some(item=>item.id===image.id),'Cancelling deletion must preserve the file');
  await editedCard.getByRole('button',{name:'Delete material · '+editedTitle,exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Confirm',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
  assert.equal((await context.request.get('/api/schedule/materials/'+image.id+'/file')).status(),404);
  assert.equal((await studentContext.request.get('/api/schedule/materials/'+image.id+'/file?inline=true')).status(),404);
  assert.deepEqual(errors,[]);
  console.log('PASS: sequential uploads >64KiB, committed-write/500 uncertainty without retry, authenticated raster preview/decoder errors, byte-exact downloads, search/category/pagination, manager edit/confirmed delete, student read/no writes, guest denied, RU/EN all-theme mobile layouts');
 }finally{
  if(adminContext){try{const me=await (await adminContext.request.get('/api/auth/me')).json(),subjects=await (await adminContext.request.get('/api/schedule/subjects')).json();for(const subject of subjects){const response=await adminContext.request.get('/api/schedule/subjects/'+encodeURIComponent(subject.key)+'/materials?limit=100');if(!response.ok())continue;for(const item of (await response.json()).items.filter(item=>created.includes(item.id)))await adminContext.request.delete('/api/schedule/materials/'+item.id+'?revision='+item.revision,{headers:{'X-CSRF-Token':me.csrf}});}}catch{}}
  for(const context of contexts)await context.close();await browser.close();
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
