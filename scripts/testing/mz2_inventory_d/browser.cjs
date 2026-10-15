const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.MZ2_D_ORIGIN || 'http://127.0.0.1:18764';
if (new URL(origin).hostname !== '127.0.0.1') throw new Error('Loopback required');
const out = process.env.MZ2_D_OUTPUT;
fs.mkdirSync(out, {recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.MZ2_BROWSER_CHANNEL ? {channel:process.env.MZ2_BROWSER_CHANNEL} : {})});
 const page=await browser.newPage({viewport:{width:1440,height:1100}}), errors=[], writes=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes.push(new URL(r.url()).pathname);});
 await page.route('**/*',r=>new URL(r.request().url()).origin===origin ? r.continue() : r.abort());
 const button=name=>page.getByRole('button',{name,exact:true});
 const field=name=>page.getByLabel(name,{exact:true});
 try {
  await page.goto(origin);
  await field('لحظة القطع للجلسة الجديدة').fill('2026-10-01T00:00');
  await button('إنشاء جلسة').click(); await page.locator('nav button').nth(9).click();
  await page.getByText('الكتالوج: 1 منتج · 1 مكوّن · 1 خانة',{exact:true}).waitFor();
  await button('إضافة منتج أو مكوّن').click();
  for (const q of ['عباية','ABAYA-54','628001']) { await field('بحث المنتج 1').fill(q); await page.getByRole('button').filter({hasText:'SKU: ABAYA-54'}).waitFor(); }
  await page.getByRole('button').filter({hasText:'SKU: ABAYA-54'}).click();
  await field('خيار المنتج 1').selectOption('black-54'); await field('الكمية 1').fill('2'); await field('تكلفة الوحدة 1').fill('50');
  assert.equal(await field('الإجمالي 1').inputValue(),'100.00');
  await button('إضافة منتج أو مكوّن').click(); await field('بحث المنتج 2').fill('عباية'); await page.getByRole('button').filter({hasText:'SKU: ABAYA-54'}).click();
  await field('خيار المنتج 2').selectOption('blue-54'); await field('الكمية 2').fill('3'); await field('تكلفة الوحدة 2').fill('60');
  await button('حفظ مسودة المخزون الآن').click(); await page.getByText('مسودة المخزون محفوظة على الخادم؛ يمكن استعادتها بعد التحديث.',{exact:true}).waitFor();
  await button('التالي').click(); await button('السابق').click(); await page.reload();
  await field('خيار المنتج 1').waitFor(); assert.equal(await field('خيار المنتج 1').inputValue(),'black-54'); assert.equal(await field('خيار المنتج 2').inputValue(),'blue-54'); assert.equal(await field('الإجمالي 2').inputValue(),'180.00');
  assert.equal(await page.locator('img').first().evaluate(img=>img.complete && img.naturalWidth>0),true);
  await button('إضافة منتج أو مكوّن').click(); await field('نوع البند 3').selectOption('STOCK_COMPONENT'); await field('التصنيف 3').selectOption('fabric-cat'); await field('المكوّن 3').selectOption('fabric'); await field('الكمية 3').fill('1.5'); await field('تكلفة الوحدة 3').fill('10');
  await page.getByRole('button',{name:'إضافة توزيع اختياري',exact:true}).first().click(); await field('الخانة 1-1').selectOption('location'); await field('كمية الخانة 1-1').fill('2');
  assert((await field('الخانة 1-1').textContent()).includes('AMBIGUOUS'));
  await button('حفظ مسودة المخزون الآن').click(); await page.getByText('مسودة المخزون محفوظة على الخادم؛ يمكن استعادتها بعد التحديث.',{exact:true}).waitFor();
  await page.screenshot({path:out+'/stage10-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844}); await page.screenshot({path:out+'/stage10-mobile.png',fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  assert.equal(errors.length,0,JSON.stringify(errors));
  assert(writes.every(p=>p.endsWith('/sessions') || p.endsWith('/inventory-draft')),JSON.stringify(writes));
  const proof=await (await fetch(origin+'/__test/proof')).json(); assert.equal(proof.non_session_collections_unchanged,true);
  fs.writeFileSync(out+'/browser-results.json',JSON.stringify({status:'PASS',checks:['auto-load','name/SKU/barcode search','catalog image','variant identity separation','calculation','optional location','components','Next/Previous/Refresh persistence','RTL desktop/mobile','no non-session writes'],errors,writes,proof},null,2)); console.log('PASS Stage10 real API/Mongo browser proof');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
