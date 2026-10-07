// Isolated visual/interaction fixture. All API responses are synthetic;
// this is not Android UAT or a production/API acceptance result.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const output=process.argv[2];
if(!output)throw Error('Evidence output directory required');
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {
  for(const [name,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
   const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
   const sent=[],external=[];
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin==='http://127.0.0.1:5189')return route.continue();
    if(url.origin!=='http://127.0.0.1:8135'){external.push(url.origin);return route.abort();}
    const leaf=url.pathname.split('/').pop();let json={items:[]};
    if(leaf==='context')json={status:'active',session_scope:'synthetic-owner:actor',can_create_cash:true,permissions:{move:true,reports:false,manage:false}};
    if(url.pathname.includes('/entities/'))json={items:[{id:leaf+'1',name:{bank:'الإنماء',cash:'صندوق الفرع',supplier:'المورد التجريبي',employee:'شهاب',provider:'سلة'}[leaf]||'جهة تجريبية',kind:leaf,currency:'SAR'}]};
    if(leaf==='movements'&&route.request().method()==='POST'){sent.push(route.request().postDataJSON());json={id:'synthetic-movement'};}
    await route.fulfill({status:200,contentType:'application/json',headers:{'Access-Control-Allow-Origin':'http://127.0.0.1:5189','Access-Control-Allow-Credentials':'true'},body:JSON.stringify(json)});
   });
   await page.goto('http://127.0.0.1:5189/operational-preview.html');
   await page.locator('.op-tile').first().waitFor();
   assert.equal(await page.locator('.op-tile').count(),6);
   const boxes=await page.locator('.op-tile').evaluateAll(rows=>rows.slice(0,3).map(r=>r.getBoundingClientRect().top));
   assert.ok(boxes.every(y=>y===boxes[0]),'Three cards must share a row');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal page overflow');
   await page.screenshot({path:path.join(output,`${name}-home.png`),fullPage:true});
   await page.locator('.op-tile').filter({hasText:'الصناديق'}).click();
   await page.locator('.op-tile').filter({hasText:'صندوق الفرع'}).click();
   await page.getByRole('combobox',{name:/^البنك/}).selectOption('bank1');
   await page.getByLabel('المبلغ',{exact:true}).fill('200');
   await page.screenshot({path:path.join(output,`${name}-cash.png`),fullPage:true});
   await page.getByRole('button',{name:'حفظ الحركة',exact:true}).click();
   await page.getByText('تم حفظ الحركة',{exact:true}).waitFor();
   assert.equal(sent.length,1);assert.equal(sent[0].kind,'transfer');assert.equal(sent[0].direction,'outgoing');assert.equal(sent[0].receipt_id,null);
   assert.deepEqual(external,[]);
   await context.close();console.log(`PASS ${name}: cards/layout/cash form/save/no external network`);
  }
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
