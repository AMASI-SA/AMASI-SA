// Local synthetic UI verification; no API request is sent to a backend.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const output=process.argv[2];if(!output)throw Error('Evidence directory required');fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  for(const [name,width,height] of [['desktop',1440,1100],['mobile',390,844]]){
   const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
   const sent=[],external=[];let record=null;
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin==='http://127.0.0.1:5189')return route.continue();
    if(url.origin!=='http://127.0.0.1:8135'){external.push(url.origin);return route.abort();}
    const leaf=url.pathname.split('/').pop();let json={items:[]};
    if(leaf==='context')json={status:'active',session_scope:'a'.repeat(64),permissions:{move:true}};
    if(url.pathname.includes('/entities/'))json={items:[{id:leaf,name:{bank:'الإنماء',courier:'شركة الشحن',supplier:'مورد الاختبار'}[leaf],currency:'SAR'}]};
    if(url.pathname.includes('/customer-exchanges/order/'))json={id:'original1',order_number:'1001',items:[{id:'a',name:'منتج تجريبي',quantity:2,remaining:record?0:2,unit_estimate:'50.00'}]};
    if(url.pathname.includes('/shipping-quote/'))json={amount:'28.75',quote_hash:'q'.repeat(64)};
    if(leaf==='customer-exchanges')json={items:record?[record]:[]};
    if(route.request().method()==='POST'){
     const body=route.request().postDataJSON();sent.push(body);
     if(!body.action)record={id:'case1',order_number:body.order_number,purchase_status:'pending',items:[{id:'a',name:'منتج تجريبي',quantity:2,remaining_to_buy:2}],shipping:{id:'courier',name:'شركة الشحن',amount:'28.75',status:'pending',reference:''},purchases:[],contributions:[{...body.contribution,movement_id:'credit1',bank_name:'الإنماء'}],summary:{expected_products:'100.00',confirmed_products:'0.00',shipping:'28.75',customer_contribution:'30.00',net_cost:'98.75'}};
     if(body.action==='purchase'){record.purchase_status='partial';record.items[0].remaining_to_buy=1;record.purchases=[{...body,id:'invoice1',supplier_name:'مورد الاختبار'}];record.summary={...record.summary,expected_products:'50.00',confirmed_products:'92.00',net_cost:'140.75'};}
     if(body.action==='shipping_completed')record.shipping={...record.shipping,status:'completed',reference:body.shipment_reference};
     json=record;
    }
    await route.fulfill({status:200,contentType:'application/json',headers:{'Access-Control-Allow-Origin':'http://127.0.0.1:5189','Access-Control-Allow-Credentials':'true'},body:JSON.stringify(json)});
   });
   await page.goto('http://127.0.0.1:5189/operational-preview.html?view=customer-exchanges');
   await page.getByLabel('رقم الطلب القديم',{exact:true}).fill('1001');
   await page.getByRole('button',{name:'عرض الطلب والاستبدالات',exact:true}).click();
   await page.getByRole('button',{name:'استبدال الطلب كاملًا — المتاح',exact:true}).click();
   await page.getByLabel(/^شركة شحن البدل/).selectOption('courier');
   await page.getByText('تكلفة شحنة البدل: 28.75 ريال — تقديرية حتى التنفيذ.').waitFor();
   await page.getByLabel(/^هل دفع العميل بالفعل/).selectOption('yes');
   await page.getByLabel('مبلغ مساهمة العميل',{exact:true}).fill('30');
   await page.getByLabel(/^البنك المستلم/).selectOption('bank');
   await page.getByLabel('مرجع دفع العميل',{exact:true}).fill('bank-paid-1');
   assert.equal(await page.locator('main').getAttribute('dir'),'rtl');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:path.join(output,`${name}-exchange.png`),fullPage:true});
   await page.getByRole('button',{name:'حفظ الاستبدال',exact:true}).click();
   await page.getByText('تم حفظ عملية الاستبدال',{exact:true}).waitFor();
   assert.equal(sent[0].order_number,'1001');assert.equal(sent[0].contribution.amount,'30');
   await page.reload();await page.getByRole('button',{name:'شراء بدل',exact:true}).click();
   await page.getByLabel(/^المورد/).selectOption('supplier');
   await page.getByLabel('رقم فاتورة المورد',{exact:true}).fill('supplier-bill-1');
   await page.getByLabel('الكمية المشتراة — منتج تجريبي',{exact:true}).fill('1');
   await page.getByLabel('صافي البند — منتج تجريبي',{exact:true}).fill('80');
   await page.getByLabel('ضريبة البند — منتج تجريبي',{exact:true}).fill('12');
   await page.getByLabel('إجمالي البند شامل الضريبة — منتج تجريبي',{exact:true}).fill('92');
   await page.screenshot({path:path.join(output,`${name}-purchase.png`),fullPage:true});
   await page.getByRole('button',{name:'حفظ العملية',exact:true}).click();
   await page.getByText('شراء جزئي — يوجد متبقٍ',{exact:true}).waitFor();
   assert.equal(sent[1].gross,'92.00');assert.equal(sent[1].lines[0].quantity,1);
   await page.getByRole('button',{name:'تأكيد تنفيذ الشحنة',exact:true}).click();
   await page.getByLabel('مرجع شحنة البدل المنفذة',{exact:true}).fill('awb-exchange');
   await page.getByRole('button',{name:'حفظ العملية',exact:true}).click();
   await page.getByText('شركة الشحن · الشحنة منفذة',{exact:true}).waitFor();
   await page.reload();await page.getByText('شراء جزئي — يوجد متبقٍ',{exact:true}).waitFor();
   assert.equal(sent.length,3);assert.deepEqual(external,[]);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:path.join(output,`${name}-readback.png`),fullPage:true});
   await context.close();console.log(`PASS ${name}: original order / contribution / partial invoice / shipment / reload / RTL / no external network`);
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
