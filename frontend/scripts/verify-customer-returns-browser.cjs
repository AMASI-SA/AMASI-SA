// Synthetic UI fixture only. API requests never reach a server.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const output=process.argv[2];
if(!output)throw Error('Evidence output directory required');
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  for(const [name,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
   const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
   const sent=[],external=[];let rows=[];
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin==='http://127.0.0.1:5189')return route.continue();
    if(url.origin!=='http://127.0.0.1:8135'){external.push(url.origin);return route.abort();}
    const leaf=url.pathname.split('/').pop();let json={items:[]};
    if(leaf==='context')json={status:'active',session_scope:'a'.repeat(64),permissions:{move:true}};
    if(url.pathname.includes('/entities/'))json={items:[{id:leaf==='provider'?'tamara':leaf+'1',name:{bank:'الإنماء',provider:'تمارا',courier:'شركة الشحن',store_driver:'مندوب المتجر'}[leaf],currency:'SAR'}]};
    if(url.pathname.includes('/customer-returns/order/'))json={id:'order1',order_number:'1001',total:'300.00',refunded:rows.some(r=>r.status==='refunded')?'100.00':'0.00',provider:'tamara',items:[{id:'a',name:'منتج تجريبي',quantity:2,remaining:rows.length?1:2}]};
    if(url.pathname.includes('/shipping-quote/'))json={amount:'28.75',quote_hash:'q'.repeat(64)};
    if(leaf==='customer-returns')json={items:rows};
    if(route.request().method()==='POST'){
     const body=route.request().postDataJSON();sent.push(body);
     rows=[{...body,id:'case1',refund_source_name:'الإنماء',shipping:{kind:'courier',id:'courier1',name:'شركة الشحن',amount:'28.75',reference:'test-awb',status:body.shipment_completed?'completed':'pending'}}];json=rows[0];
    }
    await route.fulfill({status:200,contentType:'application/json',headers:{'Access-Control-Allow-Origin':'http://127.0.0.1:5189','Access-Control-Allow-Credentials':'true'},body:JSON.stringify(json)});
   });
   await page.goto('http://127.0.0.1:5189/operational-preview.html?view=customer-returns');
   await page.getByLabel('رقم الطلب',{exact:true}).fill('1001');
   await page.getByRole('button',{name:'عرض الطلب',exact:true}).click();
   await page.getByLabel('كمية منتج تجريبي',{exact:true}).fill('1');
   await page.getByLabel(/^شحنة الاسترجاع/).selectOption('courier');
   await page.getByLabel(/^منفذ الاسترجاع/).selectOption('courier1');
   await page.getByText('تكلفة الاسترجاع: 28.75 ريال',{exact:false}).waitFor();
   await page.getByLabel('رقم البوليصة أو مرجع الاسترجاع',{exact:true}).fill('test-awb');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow');
   assert.equal(await page.locator('main').getAttribute('dir'),'rtl');
   await page.screenshot({path:path.join(output,`${name}-pending.png`),fullPage:true});
   await page.getByRole('button',{name:'حفظ المرتجع',exact:true}).click();
   await page.getByText('تم حفظ المرتجع',{exact:true}).waitFor();assert.equal(sent[0].status,'pending');
   await page.reload();await page.getByRole('button',{name:'عرض / تأكيد',exact:true}).click();
   await page.getByLabel(/^حالة رد المبلغ/).selectOption('refunded');
   await page.getByLabel('المبلغ المردود فعليًا',{exact:true}).fill('100');
   await page.getByLabel(/^البنك أو منصة الدفع/).selectOption('bank1');
   await page.getByLabel('مرجع عملية رد المبلغ',{exact:true}).fill('refund-fixture');
   await page.getByLabel('وقت رد المبلغ',{exact:true}).fill('2026-10-06T15:00');
   await page.screenshot({path:path.join(output,`${name}-confirm.png`),fullPage:true});
   await page.getByRole('button',{name:'حفظ التأكيد',exact:true}).click();
   await page.getByText('تم رد 100 ريال من الإنماء',{exact:true}).waitFor();
   assert.equal(sent.length,2);assert.equal(sent[1].status,'refunded');
   await page.reload();await page.getByText('تم رد 100 ريال من الإنماء',{exact:true}).waitFor();
   assert.equal(sent.length,2);assert.deepEqual(external,[]);
   await context.close();console.log(`PASS ${name}: pending / confirm same case / reload / RTL / no external network`);
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
