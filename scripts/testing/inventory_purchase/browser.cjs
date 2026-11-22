/* Actual React/CSS and adapters; synthetic loopback HTTP boundary only. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const f = require('./fixtures.cjs');
const root = path.resolve(__dirname,'../../..');
const out = process.env.INVENTORY_PURCHASE_UAT_OUTPUT;
if (!out || !path.isAbsolute(out) || path.resolve(out) === root || path.resolve(out).startsWith(root+path.sep)) throw Error('Absolute repo-external synthetic output required');
assert.equal(f.synthetic_only,true);
const manifest = JSON.parse(fs.readFileSync(path.join(out,'source-manifest.json')));
assert.equal(manifest.synthetic_only,true);
for (const [file,hash] of Object.entries(manifest.sha256)) {
  assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex'),hash,file);
}
const calls = [], errors = [], results = [], screenshots = [], measurements = [];
let scenario = '', savedInvoice = null, physicalQuantity = 0;
const invoiceRequests = new Map(), previews = new Map(), receipts = new Map();
const base = '/api/operational-balances';
const clone = value=>structuredClone(value);
const hash = value=>crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const dist = path.join(out,'dist');
const server = http.createServer(async (request,response)=>{
  const url = new URL(request.url,'http://127.0.0.1');
  const send = (status,value)=>{response.writeHead(status,{'content-type':'application/json','cache-control':'no-store'});response.end(JSON.stringify(value));};
  if (!url.pathname.startsWith('/api/')) {
    const requested = path.resolve(dist,url.pathname.slice(1));
    const file = requested.startsWith(dist+path.sep) && fs.existsSync(requested) && fs.statSync(requested).isFile() ? requested : path.join(dist,'index.html');
    const type = {'.js':'text/javascript','.css':'text/css','.ttf':'font/ttf','.png':'image/png','.svg':'image/svg+xml'}[path.extname(file)] || 'text/html';
    response.writeHead(200,{'content-type':type});fs.createReadStream(file).pipe(response);return;
  }
  const call = {method:request.method,path:url.pathname,params:Object.fromEntries(url.searchParams),scenario};
  calls.push(call);
  try {
    let body;
    if (request.method !== 'GET') {
      let raw='';for await (const chunk of request) raw+=chunk;
      body=JSON.parse(raw);call.body=clone(body);
      assert.equal(body.expected_session_scope,f.scope);
    }
    if (request.method==='GET' && url.pathname==='/api/auth/me') return send(200,f.owner);
    if (request.method==='GET' && url.pathname==='/api/inventory-v2/stocktakes/context') return send(200,{schema_version:'g47-stocktake-v1',capabilities:{view:false}});
    if (request.method==='GET' && url.pathname===base+'/context') return send(200,f.context);
    if (request.method==='GET' && url.pathname===base+'/inventory-catalog') {
      assert.equal(url.searchParams.get('configured'),'true');
      return send(200,{products:[f.product,f.necklace],items:[f.component],locations:f.locations});
    }
    if (request.method==='GET' && url.pathname===base+'/entities/supplier') return send(200,{items:f.suppliers});
    if (url.pathname===base+'/inventory-purchases') {
      if (request.method==='GET') return send(200,{items:savedInvoice?[savedInvoice]:[],stock:[]});
      if (request.method==='POST') {
        assert.equal(body.receipt_tracking_version,1);assert.equal(body.supplier_id,f.suppliers[0].id);
        const previous=invoiceRequests.get(body.request_id);
        if (previous) {assert.deepEqual(previous.body,body);return send(200,previous.result);}
        assert.equal(savedInvoice,null,'The fixture expects one approved invoice');
        savedInvoice=f.invoice(body);invoiceRequests.set(body.request_id,{body:clone(body),result:clone(savedInvoice)});
        return send(200,savedInvoice);
      }
    }
    if (request.method==='GET' && url.pathname===base+'/inventory-purchases/demo-invoice/receiving-context') {
      assert(savedInvoice);return send(200,{invoice:savedInvoice,locations:f.locations,writer_disabled:false,operational_active:true,financial_effect:'none',fulfillment_eligible:false});
    }
    if (request.method==='POST' && url.pathname===base+'/inventory-purchases/demo-invoice/receipts/preview') {
      assert(savedInvoice);assert(body.lines.length>0);
      const ids=new Set();
      for (const line of body.lines) {
        const source=savedInvoice.lines.find(item=>item.line_id===line.line_id);
        assert(source && Number.isSafeInteger(line.quantity) && line.quantity>0 && line.quantity<=source.pending_receipt_quantity);
        assert.equal(line.location_id,f.locations[0].id);assert(!ids.has(line.line_id));ids.add(line.line_id);
      }
      const preview_hash=hash(body);previews.set(preview_hash,clone(body));
      return send(200,{preview_hash,writer_disabled:false,fulfillment_eligible:false,financial_effect:'none',lines:body.lines});
    }
    if (request.method==='POST' && url.pathname===base+'/inventory-purchases/demo-invoice/receipts/save') {
      const {preview_hash,...intent}=body;
      assert.deepEqual(intent,previews.get(preview_hash),'Saved receiving command must equal its reviewed preview');
      const previous=receipts.get(body.request_id);
      if (previous) {assert.deepEqual(previous.body,body);return send(200,previous.result);}
      const receipt_id='demo-receipt-'+(receipts.size+1);
      const history={id:receipt_id,receipt_date:body.receipt_date,recorded_at:body.receipt_date+'T12:00:00+00:00',note:body.note,lines:[]};
      for (const line of body.lines) {
        const source=savedInvoice.lines.find(item=>item.line_id===line.line_id);
        assert(source && line.quantity<=source.pending_receipt_quantity);
        source.received_quantity+=line.quantity;source.pending_receipt_quantity-=line.quantity;
        source.physical_stock_status=source.pending_receipt_quantity?'partial':'received';physicalQuantity+=line.quantity;
        history.lines.push({...line,name:source.name,unit:source.unit,location_name:'A-01 · المخزن التوضيحي'});
      }
      savedInvoice.can_receive=savedInvoice.lines.some(line=>line.pending_receipt_quantity>0);
      savedInvoice.receipt_status=savedInvoice.can_receive?'partial':'received';
      savedInvoice.physical_stock_status=savedInvoice.receipt_status;
      savedInvoice.receipt_remaining_lines=savedInvoice.lines.filter(line=>line.pending_receipt_quantity>0).length;
      savedInvoice.receipt_history.unshift(history);
      const result={status:'approved',receipt_id,invoice_id:savedInvoice.id,financial_effect:'none',fulfillment_eligible:false,invoice:clone(savedInvoice)};
      receipts.set(body.request_id,{body:clone(body),result:clone(result)});return send(200,result);
    }
    throw Error('Unexpected fixture API request: '+request.method+' '+url.pathname);
  } catch (error) {
    errors.push(error.message);send(500,{detail:{message:error.message}});
  }
});
async function check(name,action) {
  scenario=name;const before=calls.length;await action();
  results.push({name,status:'PASS',request_count:calls.length-before});console.log('PASS '+name);
}
async function capture(page,name) {
  await page.evaluate(()=>document.fonts.ready);
  const size=await page.evaluate(()=>({viewport:innerWidth,html:document.documentElement.scrollWidth,body:document.body.scrollWidth}));
  assert(size.html<=size.viewport+1 && size.body<=size.viewport+1,'Horizontal page overflow '+name+': '+JSON.stringify(size));
  const draftLayout=await page.evaluate(()=>{
    const draft=document.querySelector('.mz-stock-native-form .mz-operational-purchase > .mz-invoice-draft');
    if(!draft)return null;
    const panel=draft.parentElement,panelBox=panel.getBoundingClientRect(),panelStyle=getComputedStyle(panel),box=draft.getBoundingClientRect();
    const number=value=>parseFloat(value)||0;
    const left=panelBox.left+number(panelStyle.borderLeftWidth)+number(panelStyle.paddingLeft);
    const right=panelBox.right-number(panelStyle.borderRightWidth)-number(panelStyle.paddingRight);
    return {width:box.width,contentWidth:right-left,leftGap:box.left-left,rightGap:right-box.right,
      results:[...draft.querySelectorAll('.mz-product-results > button')].map(button=>{
        const row=button.getBoundingClientRect(),style=getComputedStyle(button);
        const photo=button.querySelector('.mz-product-photo').getBoundingClientRect(),copy=button.querySelector('.mz-product-result-copy').getBoundingClientRect();
        return {direction:style.flexDirection,height:row.height,photoLeft:photo.left,copyRight:copy.right,
          centerDifference:Math.abs(photo.top+photo.height/2-copy.top-copy.height/2),
          contentHeight:Math.max(...[...button.children].map(child=>child.getBoundingClientRect().height)),
          verticalPaddingAndBorder:number(style.paddingTop)+number(style.paddingBottom)+number(style.borderTopWidth)+number(style.borderBottomWidth)};
      })};
  });
  if(draftLayout){
    const detail=name+': '+JSON.stringify(draftLayout);
    assert(Math.abs(draftLayout.width-draftLayout.contentWidth)<=2,'Inner invoice draft does not fill its panel '+detail);
    assert(Math.abs(draftLayout.leftGap)<=2&&Math.abs(draftLayout.rightGap)<=2,'Inner invoice draft is not centered in its panel '+detail);
    for(const row of draftLayout.results){
      assert.equal(row.direction,'row','Product result is a vertical tile '+detail);
      assert(row.copyRight<=row.photoLeft+2&&row.centerDifference<=2,'Product photo and text are not aligned in one RTL row '+detail);
      assert(row.height<=row.contentHeight+row.verticalPaddingAndBorder+2,'Product result has excessive vertical space '+detail);
    }
  }
  measurements.push({name,...size,...(draftLayout?{draft:draftLayout}:{})});
  const filename=name+'.png';await page.screenshot({path:path.join(out,filename),fullPage:true});screenshots.push(filename);
}
const paymentCalls=()=>calls.filter(call=>call.path.endsWith('/movements') || call.path.endsWith('/allocate-payment'));
const writes=()=>calls.filter(call=>call.method!=='GET');
const evidence=()=>({synthetic_only:true,actual_components:true,chromium_sandbox:true,scenario_count:results.length,
  results,measurements,errors,screenshots,calls,physical_quantity:physicalQuantity,payment_call_count:paymentCalls().length});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser=await chromium.launch({headless:true,chromiumSandbox:true,
    ...(process.env.INVENTORY_PURCHASE_UAT_CHROMIUM?{executablePath:process.env.INVENTORY_PURCHASE_UAT_CHROMIUM}:{})});
  try {
    const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'ar-SA',timezoneId:'Asia/Riyadh',reducedMotion:'reduce'});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
    // Field renders a wrapping label. A select's option text can become part
    // of that accessible name, so match its visible caption and then its own
    // control. Product radios/text fields instead have explicit aria-labels.
    const field=name=>{
      const exact=new RegExp('^'+name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'$');
      const wrapped=page.locator('label.op-field > span:visible').filter({hasText:exact})
        .locator('..').locator('input,select,textarea');
      // Keep explicit aria-label controls and the simple receipt/payment radio
      // labels, whose accessible names do not contain select option text.
      return wrapped.or(page.getByLabel(name,{exact:true}));
    };
    const button=name=>page.getByRole('button',{name,exact:true});
    const pick=product=>page.getByRole('option',{name:'اختيار '+product.name,exact:true}).click();
    const draft=()=>page.evaluate(scope=>JSON.parse(localStorage.getItem('mezan.operational.inventory.v1:'+scope+':draft')),f.scope);
    const names=()=>page.locator('.mz-invoice-row-details > strong').allTextContents();
    const saveLine=async()=>{await button('حفظ الصنف في المسودة').click();await page.getByRole('combobox',{name:'بحث بالاسم أو الرمز',exact:true}).waitFor();};
    await check('Desktop form is centered at 70% with adjacent supplier metadata',async()=>{
      await page.goto(origin+'/inventory-v2?tab=purchase');
      await page.getByRole('option',{name:'اختيار '+f.product.name,exact:true}).waitFor();
      await page.evaluate(()=>document.fonts.ready);
      const layout=await page.locator('.mz-stock-native-form').evaluate(form=>{
        const box=form.getBoundingClientRect(),parent=form.parentElement.getBoundingClientRect();
        return {width:box.width,parent:parent.width,left:box.left-parent.left,right:parent.right-box.right,
          fields:[...form.querySelectorAll('.mz-invoice-draft > .mz-invoice-meta > label')].map(label=>{const r=label.getBoundingClientRect();return {label:label.querySelector('span').textContent,top:r.top,left:r.left,right:r.right};})};
      });
      assert(Math.abs(layout.width/layout.parent-.7)<.01,JSON.stringify(layout));
      assert(Math.abs(layout.left-layout.right)<=2,JSON.stringify(layout));
      assert.deepEqual(layout.fields.map(item=>item.label),['المورد','رقم فاتورة المورد','تاريخ الفاتورة']);
      assert(Math.max(...layout.fields.map(item=>item.top))-Math.min(...layout.fields.map(item=>item.top))<=2);
      assert(layout.fields[0].left>=layout.fields[1].right && layout.fields[1].left>=layout.fields[2].right);
      measurements.push({name:'desktop-form',...layout});
      await field('المورد').selectOption(f.suppliers[0].id);await field('رقم فاتورة المورد').fill('DEMO-INV-001');
      await capture(page,'purchase-desktop');
    });
    await check('Product and component choices show an explicit selected mark in one editor',async()=>{
      assert.equal(await button('منتج').getAttribute('aria-pressed'),'true');assert.equal(await button('منتج').locator('svg').count(),1);
      await pick(f.product);assert.match(await page.locator('.mz-product-selected').innerText(),/مختار/);
      assert.equal(await page.locator('.mz-invoice-editor').count(),1);
      await button('مكوّن').click();assert.equal(await button('مكوّن').getAttribute('aria-pressed'),'true');
      assert.equal(await button('مكوّن').locator('svg').count(),1);assert.equal(await button('منتج').getAttribute('aria-pressed'),'false');
      await pick(f.component);assert.match(await page.locator('.mz-product-selected').innerText(),/خيط تطريز توضيحي.*مختار/s);
      await button('منتج').click();assert.equal(await page.locator('.mz-purchase-configuration').count(),0);
    });
    await check('Conditional yes, literal no and deferred parent keep only visible option values',async()=>{
      await pick(f.product);await field('المقاس: سنتان').check();await field('تطريز الاسم: نعم').check();
      await field('شكل التطريز: قلب').check();await field('الاسم على الفروة · البند 1').fill('فاطمة');
      await field('تطريز الاسم: لا').check();
      assert.equal(await field('الاسم على الفروة · البند 1').count(),0);assert.equal(await field('شكل التطريز: قلب').count(),0);
      const fixed=(await draft()).editor.purchase_configuration;
      assert.equal(fixed.state,'ready');assert.deepEqual(fixed.inputs,[]);assert.deepEqual(fixed.deferred_option_ids,[]);
      assert(fixed.selections.some(value=>value.option_id==='emb'&&value.value_id==='no'));
      await field('تطريز الاسم: يُحدد عند طلب العميل').check();
      assert.equal(await field('الاسم على الفروة · البند 1').count(),0);
      await field('الكمية — '+f.product.name).fill('2');await field('سعر الوحدة قبل الضريبة — '+f.product.name).fill('85');
      await saveLine();const saved=(await draft()).form.lines[0].purchase_configuration;
      assert.equal(saved.state,'raw');assert.deepEqual(saved.deferred_option_ids,['emb']);assert.deepEqual(saved.inputs,[]);
      assert.deepEqual(saved.selections,[{option_id:'size',value_id:'two'}]);
      assert.equal(await page.locator('.mz-purchase-configuration').count(),0);assert.equal(writes().length,0);
    });
    await check('Newest compact row is first; editing preserves its place and optional omissions stay ready',async()=>{
      await pick(f.necklace);await field('اللون: ذهبي').check();
      await field('الكمية — '+f.necklace.name).fill('3');await field('سعر الوحدة قبل الضريبة — '+f.necklace.name).fill('25');await saveLine();
      assert.deepEqual(await names(),[f.necklace.name,f.product.name]);
      const identities=(await draft()).form.lines.map(line=>line.draft_key);
      await button('تعديل '+f.product.name+' 2').click();assert.equal(await field('تطريز الاسم: يُحدد عند طلب العميل').isChecked(),true);
      await field('تطريز الاسم: نعم').check();await field('شكل التطريز: قلب').check();await field('الاسم على الفروة · البند 1').fill('فاطمة');
      await field('الكمية — '+f.product.name).fill('4');await saveLine();
      const current=await draft();assert.deepEqual(current.form.lines.map(line=>line.draft_key),identities);
      assert.deepEqual(await names(),[f.necklace.name,f.product.name]);assert.equal(current.form.lines[1].quantity,'4');
      assert.equal(current.form.lines[1].purchase_configuration.state,'ready');assert.deepEqual(current.form.lines[1].purchase_configuration.deferred_option_ids,[]);
      assert(!current.form.lines[1].purchase_configuration.inputs.some(input=>input.option_id==='note'));
      assert(!current.form.lines[1].purchase_configuration.selections.some(input=>input.option_id==='extra'));
      assert.match(await page.locator('.mz-invoice-row').nth(1).innerText(),/الاسم على الفروة: فاطمة/);
      assert.equal(await page.locator('.mz-purchase-configuration').count(),0);assert.equal(await page.locator('.mz-invoice-row input').count(),0);
      assert.equal(writes().length,0);await capture(page,'draft-desktop');
    });
    await check('Mobile single editor and compact draft fit the viewport without horizontal scrolling',async()=>{
      await page.setViewportSize({width:390,height:844});
      const width=await page.locator('.mz-stock-native-form').evaluate(form=>({form:form.getBoundingClientRect().width,parent:form.parentElement.getBoundingClientRect().width}));
      assert(Math.abs(width.form-width.parent)<=2);measurements.push({name:'mobile-form',...width});
      await button('تعديل '+f.product.name+' 2').click();
      assert.equal(await field('الاسم على الفروة · البند 1').inputValue(),'فاطمة');
      await capture(page,'editor-mobile');await saveLine();await capture(page,'draft-mobile');
    });
    await check('Approving an unreceived credit invoice does not call receiving or payment',async()=>{
      assert.equal(await field('لم أستلم بعد').isChecked(),true);assert.equal(await field('آجل — لم أسدد').isChecked(),true);
      await button('مراجعة الفاتورة').click();await page.getByRole('region',{name:'مراجعة الفاتورة قبل الاعتماد'}).waitFor();
      assert.equal(writes().length,0);await capture(page,'invoice-review-mobile');
      const response=page.waitForResponse(value=>value.request().method()==='POST'&&value.url()===origin+base+'/inventory-purchases');
      await button('اعتماد الفاتورة').click();assert.equal((await response).status(),200);
      await page.getByRole('region',{name:'الفاتورة المحفوظة'}).waitFor();
      assert.equal(savedInvoice.receipt_status,'not_received');assert.equal(savedInvoice.settled,'0.00');
      assert.equal(savedInvoice.outstanding,'415.00');assert.equal(physicalQuantity,0);
      assert.equal(writes().length,1);assert.equal(paymentCalls().length,0);assert.equal(invoiceRequests.size,1);
    });
    await check('Awaiting queue receives exactly two of four units and preserves the unpaid balance on reload',async()=>{
      await page.getByRole('navigation',{name:'أقسام المخزون'}).getByRole('button',{name:'استلام مشتريات المخزون',exact:true}).click();
      await page.locator('.mz-receipt-list article').waitFor();
      assert.match(await page.locator('.mz-receipt-list').innerText(),/DEMO-INV-001/);
      await page.locator('.mz-receipt-list').getByRole('button',{name:'تسجيل استلام',exact:true}).click();
      await field('المستلم الآن — '+f.product.name).fill('2');await field('موقع الاستلام — '+f.product.name).selectOption(f.locations[0].id);
      const response=page.waitForResponse(value=>value.request().method()==='POST'&&value.url().endsWith('/receipts/save'));
      await button('حفظ الاستلام وإضافة الكمية').click();assert.equal((await response).status(),200);
      await page.locator('.mz-receipt-item').filter({hasText:f.product.name}).getByText('المشتراة 4 · المستلمة 2 · المتبقية للاستلام 2',{exact:true}).waitFor();
      const fur=savedInvoice.lines.find(line=>line.item_id===f.product.id),necklace=savedInvoice.lines.find(line=>line.item_id===f.necklace.id);
      const actual=[...receipts.values()][0].body.lines;
      assert.deepEqual(actual,[{line_id:fur.line_id,quantity:2,location_id:f.locations[0].id}]);
      assert.equal(fur.received_quantity,2);assert.equal(fur.pending_receipt_quantity,2);assert.equal(necklace.received_quantity,0);
      assert.equal(savedInvoice.receipt_status,'partial');assert.equal(savedInvoice.settled,'0.00');assert.equal(savedInvoice.outstanding,'415.00');
      assert.equal(physicalQuantity,2);assert.equal(receipts.size,1);assert.equal(paymentCalls().length,0);
      await capture(page,'receipt-partial-mobile');await page.setViewportSize({width:1440,height:1000});await capture(page,'receipt-partial-desktop');
      const previousWrites=writes().length;await page.reload();await page.locator('.mz-receipt-list article').waitFor();
      assert.match(await page.locator('.mz-receipt-list').innerText(),/المشتراة 4 · المستلمة 2 · المتبقية للاستلام 2/);
      assert.equal(writes().length,previousWrites);assert.equal(paymentCalls().length,0);assert.equal(physicalQuantity,2);
    });
    assert.deepEqual(errors,[]);assert.equal(results.length,7);assert.equal(screenshots.length,7);
    await context.close();
  } finally {
    fs.writeFileSync(path.join(out,'browser-evidence.json'),JSON.stringify(evidence(),null,2));
    await browser.close();server.close();
  }
})().catch(error=>{
  errors.push(error.message);fs.writeFileSync(path.join(out,'browser-evidence.json'),JSON.stringify(evidence(),null,2));
  console.error(error);server.close();process.exitCode=1;
});
