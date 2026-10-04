// Capture the real local demo; no network interception or mocked responses.
const {chromium}=require('../apps/web/node_modules/playwright');
const fs=require('node:fs');const path=require('node:path');
(async()=>{
 const connection=JSON.parse(fs.readFileSync(process.env.MEMORY_DEMO_FILE,'utf8'));
 if(!connection.url.startsWith('http://127.0.0.1:'))throw Error('Local synthetic demo required');
 const browser=await chromium.launch();
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100},deviceScaleFactor:1});
  await page.goto(connection.url);await page.getByLabel('Access token').fill(connection.token);await page.getByLabel('Subject ID',{exact:true}).fill(connection.subject_id);
  await page.getByRole('button',{name:'Load suggestions'}).click();
  await page.getByRole('button',{name:/I prefer jasmine tea.*Review by/}).click();
  await page.getByText('Review the source before deciding. Model scores are uncalibrated.').waitFor();
  const directory=path.join(__dirname,'screenshots');fs.mkdirSync(directory,{recursive:true});
  await page.locator('section[aria-label="Supervised memory review"]').screenshot({path:path.join(directory,'review.png'),animations:'disabled',style:'nextjs-portal { visibility: hidden; }'});
  await page.getByRole('button',{name:'Approve suggestion',exact:true}).click();await page.getByRole('button',{name:'Confirm approval'}).click();
  await page.getByText('Suggestion approved. Memory saved.').waitFor();
  await page.getByRole('button',{name:'Search memories',exact:true}).click();await page.getByRole('button',{name:/profile · explicit/}).click();
  await page.getByRole('button',{name:'Inspect source evidence'}).click();await page.getByText('Source evidence loaded.').waitFor();
  await page.locator('section.workspace').screenshot({path:path.join(directory,'memory.png'),animations:'disabled',style:'nextjs-portal { visibility: hidden; }'});
  // Actual API smoke alongside capture: reject remaining suggestion, correct, forget, confirm suppression.
  async function api(route,method='GET',body){
   const response=await fetch(connection.api_url+route,{method,headers:{Authorization:'Bearer '+connection.token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
   if(!response.ok)throw Error('Demo API returned '+response.status);return response.json();
  }
  const suggestions=(await api('/v1/suggestions?subject_id='+connection.subject_id)).items;
  if(suggestions.length!==1)throw Error('Expected remaining synthetic suggestion');
  await api('/v1/suggestions/'+suggestions[0].id+'/reject','POST',{confirm:true});
  const memories=(await api('/v1/memories?subject_id='+connection.subject_id)).items;const memory=memories[0];
  const corrected=(await api('/v1/memories/'+memory.id,'PATCH',{expected_version_id:memory.version_id,content:'I prefer jasmine tea',source:{episode_id:memory.source_episode_ids[0]},confidence:memory.confidence,importance:memory.importance})).memory;
  await api('/v1/memories/'+memory.id+'/forget','POST',{confirm:true,expected_version_id:corrected.version_id});
  if((await api('/v1/search?subject_id='+connection.subject_id+'&q=tea')).items.length)throw Error('Forgotten memory remained searchable');
  console.log('Genuine offline screenshots saved; real API approve/reject/correct/forget smoke passed.');
 }finally{await browser.close()}
})().catch(()=>{console.error('Synthetic screenshot capture failed; inspect local setup, not credentials.');process.exit(1)});
