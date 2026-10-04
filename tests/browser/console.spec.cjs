const {test,expect}=require('../../apps/web/node_modules/@playwright/test');
async function keyboardFocus(page,locator){for(let i=0;i<80;i++){if(await locator.evaluate(el=>el===document.activeElement))return;await page.keyboard.press('Tab')}throw Error('Control is unreachable by Tab')}
const id='11111111-1111-4111-8111-111111111111',version='22222222-2222-4222-8222-222222222222',source='33333333-3333-4333-8333-333333333333';
for(const scenario of ['lifecycle','error recovery'])test(scenario,async({page})=>{
 let content='I prefer tea',suppressed=false,changed=false,fail=scenario==='error recovery';
 const memory=()=>({id,version_id:changed?id:version,content,kind:'profile',origin:'observed',confidence:.9,importance:.5,indexing_state:'ready',source_episode_ids:[source]});
 await page.route('**/api/proxy',async route=>{const req=route.request().postDataJSON();expect(route.request().headers().authorization).toBe('Bearer test-token');let data={};let status=200;
 if(fail){fail=false;status=503;data={error:{code:'service_unavailable'}}}
 else if(req.path.includes('/history'))data={versions:[{id:version,content:'I prefer tea',origin:'observed',valid_from:'2026-01-01T00:00:00Z',valid_to:changed?'2026-01-02T00:00:00Z':null},...(changed?[{id,content,origin:'explicit',valid_from:'2026-01-02T00:00:00Z'}]:[])]};
 else if(req.path.includes('/audit'))data={events:[{id,action:'memory.read',outcome:'succeeded',created_at:'2026-01-01T00:00:00Z',request_id:source}]};
 else if(req.path.includes('/sources'))data={episodes:[{id:source,messages:[{role:'user',content:'Original supporting evidence'}]}]};
 else if(req.method==='PATCH'){expect(req.body.expected_version_id).toBe(version);content=req.body.content;changed=true;data={memory:memory()}}
 else if(req.path.endsWith('/forget')){expect(req.body.confirm).toBe(true);suppressed=true;data={erasure:{state:'pending',retained_shared_episodes:0}}}
 else if(req.path.endsWith('/erasure'))data={erasure:{state:'completed',retained_shared_episodes:0}};
 else if(req.path.includes('/jobs/'))data={job:{state:'succeeded',attempts:1}};
 else data={items:suppressed?[]:[memory()],next_cursor:null};
 await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('/');await keyboardFocus(page,page.getByLabel('Access token'));await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText('test-token');await keyboardFocus(page,page.getByLabel('Subject ID',{exact:true}));await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText(id);await keyboardFocus(page,page.getByRole('button',{name:'Search memories',exact:true}));await page.keyboard.press('Enter');
 if(scenario==='error recovery'){await expect(page.getByRole('status')).toContainText('service_unavailable');await keyboardFocus(page,page.getByRole('button',{name:'Search memories',exact:true}));await page.keyboard.press('Enter')}
 await expect(page.getByRole('button',{name:/profile · observed/})).toBeVisible();await keyboardFocus(page,page.getByRole('button',{name:/profile · observed/}));await page.keyboard.press('Enter');await expect(page.getByText('Memory detail loaded.')).toBeVisible();
 await keyboardFocus(page,page.getByRole('button',{name:'Inspect source evidence'}));await page.keyboard.press('Enter');await expect(page.getByText('Original supporting evidence')).toBeVisible();
 await keyboardFocus(page,page.getByLabel('Corrected content'));await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText('I prefer coffee');await keyboardFocus(page,page.getByRole('button',{name:'Save correction'}));await page.keyboard.press('Enter');await expect(page.getByRole('status')).toContainText('Correction saved');await expect(page.getByText('Superseded',{exact:false})).toBeVisible();
 await keyboardFocus(page,page.getByRole('button',{name:'Forget memory',exact:true}));await page.keyboard.press('Enter');await expect(page.getByRole('dialog')).toBeVisible();await expect(page.getByRole('button',{name:'Cancel'})).toBeFocused();await page.keyboard.press('Escape');await expect(page.getByRole('button',{name:'Forget memory',exact:true})).toBeFocused();await page.keyboard.press('Enter');await keyboardFocus(page,page.getByRole('button',{name:'Confirm forget'}));await page.keyboard.press('Enter');await expect(page.getByRole('status')).toContainText('Memory suppressed');await expect(page.getByText('Original supporting evidence')).toHaveCount(0);await expect(page.getByRole('button',{name:/profile · observed/})).toHaveCount(0);
 await keyboardFocus(page,page.getByRole('button',{name:'Refresh erasure status'}));await page.keyboard.press('Enter');await expect(page.getByText('completed · Shared episodes retained: 0')).toBeVisible();
 await keyboardFocus(page,page.getByLabel('Job ID',{exact:true}));await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText(source);await keyboardFocus(page,page.getByRole('button',{name:'Check job'}));await page.keyboard.press('Enter');await expect(page.getByText('succeeded · 1 attempts')).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(await page.evaluate(()=>localStorage.length)).toBe(0);await page.screenshot({path:`test-results/${test.info().project.name}-${scenario.replace(' ','-')}.png`,fullPage:true});
});

test('proxy denies arbitrary upstream paths',async({request})=>{
 for(const path of ['https://example.com','//example.com','/v1/administration','/v1/memories/../admin']){
  const response=await request.post('/api/proxy',{data:{path,method:'GET'},headers:{authorization:'Bearer test-token'}});expect(response.status()).toBe(400);
 }
});

test('overlapping selection cannot unlock identity or restore stale data',async({page})=>{
 let historyCalls=0;
 const memory={id,version_id:version,content:'I prefer tea',kind:'profile',origin:'observed',confidence:1,importance:.5,indexing_state:'ready',source_episode_ids:[source]};
 await page.route('**/api/proxy',async route=>{const {path}=route.request().postDataJSON();let data;
 if(path.includes('/history')){historyCalls++;await new Promise(r=>setTimeout(r,250));data={versions:[]}}
 else if(path.includes('/audit')){await new Promise(r=>setTimeout(r,250));data={events:[]}}
 else data={items:[memory,{...memory,id:source,content:'Other memory'}],next_cursor:null};
 await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('/');await page.getByLabel('Access token').fill('token');await page.getByLabel('Subject ID',{exact:true}).fill(id);await page.getByRole('button',{name:'Search memories',exact:true}).click();await expect(page.locator('.card')).toHaveCount(2);
 await page.locator('.collection .cards').evaluate(el=>{el.children[0].click();el.children[1].click()});
 await expect(page.getByLabel('Access token')).toBeDisabled();await expect(page.getByLabel('Subject ID',{exact:true})).toBeDisabled();await expect(page.locator('.card').nth(1)).toBeDisabled();
 await expect(page.getByRole('status')).toContainText('Memory detail loaded');expect(historyCalls).toBe(1);
 await page.getByLabel('Subject ID',{exact:true}).fill(source);await expect(page.locator('.card')).toHaveCount(0);await expect(page.getByText('I prefer tea',{exact:true})).toHaveCount(0);
});

test('supervised review cancellation, interrupted approval and repeated decisions',async({page})=>{
 let approved=false,failed=false,approvalCalls=0,sourceCalls=0;
 const suggestion={id,content:'I prefer tea',episode_id:source,confidence:.8,expires_at:'2027-01-01T00:00:00Z'};
 await page.route('**/api/proxy',async route=>{
  const req=route.request().postDataJSON();let status=200,data={};
  if(req.path.endsWith('/approve')){
   approvalCalls++;approved=true;
   if(!failed){failed=true;status=503;data={error:{code:'service_unavailable'}}}
   else {await new Promise(r=>setTimeout(r,200));data={state:'approved',suggestion_id:id,memory_id:version}}
  }else if(req.path.endsWith('/sources')){sourceCalls++;data={episodes:[{id:source,messages:[{role:'user',content:'I prefer tea, original evidence'}]}]}}
  else if(req.path.startsWith('/v1/suggestions?'))data={items:approved?[]:[suggestion]};
  else data={items:[],next_cursor:null};
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('/');await page.getByLabel('Access token').fill('token');await page.getByLabel('Subject ID',{exact:true}).fill(id);
 await page.getByRole('button',{name:'Load suggestions'}).click();await page.getByRole('button',{name:/I prefer tea Review by/}).click();await expect(page.getByText('I prefer tea, original evidence')).toBeVisible();await expect(page.getByText(/Extraction confidence:.*Uncalibrated/)).toBeVisible();
 await keyboardFocus(page,page.getByRole('button',{name:'Approve suggestion',exact:true}));await page.keyboard.press('Enter');await expect(page.getByRole('button',{name:'Cancel review'})).toBeFocused();await page.keyboard.press('Escape');await expect(page.getByRole('button',{name:'Approve suggestion',exact:true})).toBeFocused();expect(approvalCalls).toBe(0);
 await page.keyboard.press('Enter');await page.getByRole('button',{name:'Confirm approval'}).click();await expect(page.getByRole('status')).toContainText('service_unavailable');await expect(page.getByRole('dialog')).toBeVisible();
 await page.getByRole('button',{name:'Confirm approval'}).evaluate(el=>{el.click();el.click()});await expect(page.getByLabel('Access token')).toBeDisabled();await expect(page.getByRole('status')).toContainText('Suggestion approved');expect(approvalCalls).toBe(2);expect(sourceCalls).toBe(1);await expect(page.getByText('I prefer tea, original evidence')).toHaveCount(0);await expect(page.getByRole('dialog')).not.toBeVisible();
 await page.getByLabel('Subject ID',{exact:true}).fill(source);await expect(page.getByText('I prefer tea',{exact:true})).toHaveCount(0);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.reload();await expect(page.getByLabel('Access token')).toHaveValue('');
});

test('supervised rejection conflict permits refresh without stale evidence',async({page})=>{
 let pending=true;
 await page.route('**/api/proxy',async route=>{
  const req=route.request().postDataJSON();let status=200,data;
  if(req.path.endsWith('/reject')){pending=false;status=409;data={error:{code:'version_conflict'}}}
  else if(req.path.endsWith('/sources'))data={episodes:[{id:source,messages:[{role:'user',content:'Review evidence'}]}]};
  else data={items:pending?[{id,content:'I prefer tea',episode_id:source,confidence:.8,expires_at:'2027-01-01T00:00:00Z'}]:[]};
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('/');await page.getByLabel('Access token').fill('token');await page.getByLabel('Subject ID',{exact:true}).fill(id);await page.getByRole('button',{name:'Load suggestions'}).click();await page.getByRole('button',{name:/I prefer tea Review by/}).click();await page.getByRole('button',{name:'Reject suggestion',exact:true}).click();await page.getByRole('button',{name:'Confirm rejection'}).click();await expect(page.getByRole('status')).toContainText('version_conflict');await page.getByRole('button',{name:'Cancel review'}).click();await page.getByRole('button',{name:'Load suggestions'}).click();await expect(page.getByText('Review evidence')).toHaveCount(0);
});
