// Browser checks for the real native frontend. Every API response below is
// explicitly synthetic; only the local frontend dev server is reachable.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {chromium}=require(process.env.MEZAN_PLAYWRIGHT_MODULE||'playwright');
const f=require('./frontend-fixtures.cjs');
const UI='http://127.0.0.1:4174',API='http://127.0.0.1:4175';
const output=process.env.MEZAN_INVENTORY_BROWSER_OUTPUT||path.join(os.tmpdir(),'mezan-inventory-native-browser');
fs.mkdirSync(output,{recursive:true});
const visited=[],blocked=[],apiCalls=[],pageErrors=[];
let componentFailure=false,stockFailure=false,activeBrowser,activePage;
const cors={'access-control-allow-origin':UI,'access-control-allow-credentials':'true','access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS','access-control-allow-headers':'content-type,x-csrf-token,x-requested-with'};
const product=id=>f.products.find(row=>row.id===decodeURIComponent(id))||f.products[0];
function dataFor(route,request){
  const url=new URL(request.url()),p=url.pathname.replace(/^\/api/,''),method=request.method();
  if(p==='/auth/me')return f.owner;
  if(p==='/auth/csrf')return {csrf_token:'fixture-only'};
  if(p==='/operational-balances/context')return f.context;
  if(p==='/inventory-v2/stocktakes/context')return f.countContext;
  if(p==='/operational-balances/inventory')return f.projection;
  if(p==='/products-v2/workspace/products'||p==='/products-v2'){
    const q=(url.searchParams.get('q')||'').toLocaleLowerCase(),status=url.searchParams.get('status');
    const items=f.products.filter(row=>(!q||(row.name+' '+row.sku).toLocaleLowerCase().includes(q))&&(!status||row.status===status));
    return {items,pagination:{page:1,total:items.length,total_pages:1}};
  }
  if(p==='/products-v2/summary')return {total:f.products.length};
  if(p==='/products-v2/sync-recent'||p==='/products-v2/sync')return {ok:true,created:0,updated:0,seen_products:3};
  if(/^\/products-v2\/[^/]+\/refresh-details$/.test(p))return {product:product(p.split('/')[2])};
  if(/^\/products-v2\/[^/]+\/costs$/.test(p))return method==='GET'?{base_cost:42.5,variant_costs:{'fixture-variant-m':42.5},notes:''}:{ok:true};
  if(/^\/products-v2\/[^/]+\/bundle$/.test(p))return {definition:{revision:1,product_type:product(p.split('/')[2]).product_type==='composite'?'composite':'normal',items:[],component_scopes:{}},limits:{max_items:100,max_depth:5,max_leaves:500},expansion:null};
  if(/^\/products-v2\/[^/]+\/bundle-components$/.test(p))return {components:[]};
  if(/^\/products-v2\/[^/]+\/bundle-cost-policy$/.test(p))return {revision:0,bundle_revision:1,include_parent_base:false};
  if(/^\/products-v2\/[^/]+$/.test(p))return product(p.split('/')[2]);
  if(p==='/components-v2/workspace')return {components:f.resources,categories:f.categories,groups:[],products:f.products,meta:{mode:'production'}};
  if(p==='/components-v2'&&method==='POST')return {ok:true,resource:{id:'fixture-created-component'}};
  if(p==='/warehouse-locations/warehouses')return {items:f.branches};
  if(p==='/warehouse-locations-v2/catalog')return {default_country:'السعودية',default_city:'الرياض',countries:[{name:'السعودية',default_city:'الرياض',cities:['الرياض','جدة']}]};
  if(p==='/warehouse-locations-v2/section-capabilities')return {items:[{value:'cabinets',label:'دواليب وخانات'}],recommended:{storage:['cabinets']}};
  if(p.startsWith('/warehouse-locations-v2/warehouses/')&&p.endsWith('/sections'))return {items:f.sections};
  if(p==='/warehouse-locations/locations')return {items:f.locations};
  if(p==='/inventory-v2/purchase-receiving/catalog')return {products:f.products,purchase_invoices:f.invoices,warehouses:f.branches,locations:f.locations,recent_receipts:[{id:'fixture-receipt',product_name:f.products[0].name,location_code:'د1 / ر2 / خ3',quantity:6}],inventory_alerts:[]};
  if(p==='/purchase-invoices')return {items:f.invoices};
  if(p==='/suppliers-v2/workspace')return {suppliers:[],items:[],summary:{}};
  if(p==='/integrations/qoyod/header-count')return {local_summary:{source:'local_invoice_mirror',count:0,updated_at:'2026-11-20T19:30:00Z'}};
  if(p==='/alerts/unread-count')return {count:0};
  if(p==='/alerts')return {items:[]};
  if(p.includes('/integrations-v2'))return {providers:[]};
  return {items:[],providers:[],count:0,permissions:{},data:[]};
}
async function run(){
  const browser=await chromium.launch({headless:true,channel:process.env.MEZAN_BROWSER_CHANNEL||'chrome'});activeBrowser=browser;
  const context=await browser.newContext({viewport:{width:1440,height:1050},locale:'ar-SA',timezoneId:'Asia/Riyadh',serviceWorkers:'block'});
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.origin===API&&url.pathname==='/fixture-assets/product.svg')return route.fulfill({status:200,contentType:'image/svg+xml',body:f.image,headers:cors});
    if(url.origin===API&&url.pathname.startsWith('/api/')){
      apiCalls.push({path:url.pathname,method:request.method()});
      if(request.method()==='OPTIONS')return route.fulfill({status:204,headers:cors});
      if(componentFailure&&url.pathname==='/api/components-v2'&&request.method()==='POST')return route.fulfill({status:409,contentType:'application/json',headers:cors,body:JSON.stringify({detail:{code:'component_code_exists'}})});
      if(stockFailure&&url.pathname==='/api/operational-balances/inventory')return route.fulfill({status:503,contentType:'application/json',headers:cors,body:JSON.stringify({detail:{message:'تعذر قراءة رصيد الاختبار المحلي'}})});
      return route.fulfill({status:200,contentType:'application/json',headers:cors,body:JSON.stringify(dataFor(route,request))});
    }
    if(url.origin===UI&&['GET','HEAD'].includes(request.method()))return route.continue();
    blocked.push({url:request.url(),method:request.method()});return route.abort('blockedbyclient');
  });
  const page=await context.newPage();activePage=page;
  page.on('pageerror',error=>pageErrors.push(error.message));
  await page.addInitScript(()=>{
    addEventListener('DOMContentLoaded',()=>{if(document.querySelector('[data-native-test-preview]'))return;const ribbon=document.createElement('div');ribbon.dataset.syntheticBrowserTest='true';ribbon.style.cssText='padding:7px 12px;background:#fff7db;color:#655013;font:12px Tahoma;text-align:center;position:relative;z-index:500';ribbon.textContent='اختبار محلي معزول · بيانات اختبار اصطناعية فقط · جميع الاتصالات الخارجية محجوبة';document.body.prepend(ribbon);});
  });
  const checkOverflow=async label=>{
    const size=await page.evaluate(()=>({viewport:innerWidth,page:document.documentElement.scrollWidth}));
    assert(size.page<=size.viewport+1,label+': horizontal overflow '+JSON.stringify(size));
  };
  const shot=async label=>{await page.screenshot({path:path.join(output,label+'.png'),fullPage:true,timeout:15000,animations:'disabled'});await checkOverflow(label);visited.push(label);};
  const open=async(pathname,ready)=>{await page.goto(UI+pathname,{waitUntil:'domcontentloaded'});const target=page.locator(ready).first();
    if(page.viewportSize().width<760&&ready.includes('tbody tr')&&['/products-v2','/components-v2'].includes(pathname.split('?')[0])){
      const back=page.locator('.mz-catalog-mobile-back:visible').first();
      await Promise.race([target.waitFor({timeout:90000}),back.waitFor({timeout:90000})]);
      if(await back.isVisible())await back.click();
    }
    await target.waitFor({timeout:90000});await page.waitForTimeout(300);};
  await open('/products-v2','[data-testid="inventory-catalog-workspace"] tbody tr');
  if(process.argv.includes('--baseline-catalog')){
    await shot('before-catalog-owner-taxonomy');
    fs.writeFileSync(path.join(output,'before-report.json'),JSON.stringify({synthetic_only:true,visited,blocked,apiCalls,pageErrors},null,2));
    await browser.close();process.stdout.write(JSON.stringify({output,visited,pageErrors,blocked},null,2)+'\n');return;
  }
  await shot('desktop-catalog');
  assert.equal(await page.locator('table img').first().getAttribute('src'),f.products[0].main_image);
  await page.getByRole('button',{name:'التكاليف',exact:true}).click();
  await page.getByLabel('تكلفة المنتج في ميزان',{exact:true}).fill('48.75');
  await page.getByRole('button',{name:'حفظ التكاليف',exact:true}).click();
  await page.getByRole('button',{name:'المواصفات',exact:true}).click();
  await open('/components-v2','[data-testid="components-catalog-workspace"] tbody tr');
  await shot('desktop-components');
  await page.getByRole('button',{name:'إضافة مكوّن أو خدمة',exact:true}).click();
  await page.getByRole('dialog').waitFor();
  await page.getByPlaceholder('مثل: تطريز الاسم').fill('خدمة اختبار جديدة');
  await page.getByPlaceholder('EMBROIDERY_NAME').fill('TEST-LETTERS-NEW');
  assert.equal(await page.getByRole('button',{name:'إنشاء',exact:true}).isDisabled(),true);
  await page.getByRole('dialog').getByLabel('ملابس',{exact:true}).check();
  componentFailure=true;
  await page.getByRole('button',{name:'إنشاء',exact:true}).click();
  await page.getByRole('dialog').getByRole('alert').waitFor();
  await shot('desktop-component-form-error');
  assert.equal(await page.getByPlaceholder('EMBROIDERY_NAME').inputValue(),'TEST-LETTERS-NEW');
  componentFailure=false;
  await page.getByRole('button',{name:'إنشاء',exact:true}).click();
  await page.getByRole('dialog').waitFor({state:'detached'});
  await open('/products-v2?inventory_view=bundles','[data-testid="inventory-catalog-workspace"] tbody tr');
  await page.getByRole('button',{name:'عرض '+f.products[1].name,exact:true}).click();
  await page.locator('[data-testid="bundle-editor"]').waitFor();
  await shot('desktop-bundles');
  await open('/inventory-v2','[data-testid="native-inventory-home"]');
  await shot('desktop-home');
  await page.locator('.mz-stock-review-row').first().click();
  await page.locator('.mz-stock-inspector').waitFor();
  await shot('desktop-stock');
  await open('/inventory-v2?tab=reports','[data-testid="native-inventory-reports"]');
  await shot('desktop-reports');
  await open('/components-v2?workspace=warehouse','[data-testid="warehouse-hierarchy-workspace"] .mz-wh-bin');
  await page.locator('.mz-wh-bin').nth(8).click();
  await shot('desktop-warehouse');
  await open('/inventory-receiving-v2','[data-testid="receiving-invoice-fixture-invoice-draft"]');
  await page.getByLabel('بحث فواتير الاستلام').fill('TEST-INV-001');
  assert.equal(await page.locator('.mz-receiving-invoice-table tbody tr').count(),1);
  await page.getByLabel('بحث فواتير الاستلام').fill('');
  await shot('desktop-receiving');
  await page.setViewportSize({width:390,height:844});
  for(const [name,pathname,ready] of [
    ['home','/inventory-v2','[data-testid="native-inventory-home"]'],
    ['stock','/inventory-v2?tab=stock','.mz-stock-detail-table tbody tr'],
    ['catalog','/products-v2','[data-testid="inventory-catalog-workspace"] tbody tr'],
    ['components','/components-v2','[data-testid="components-catalog-workspace"] tbody tr'],
    ['bundles','/products-v2?inventory_view=bundles','[data-testid="bundle-editor"]'],
    ['warehouse','/components-v2?workspace=warehouse','.mz-wh-bin'],
    ['receiving','/inventory-receiving-v2','[data-testid="receiving-invoice-fixture-invoice-draft"]'],
    ['reports','/inventory-v2?tab=reports','[data-testid="native-inventory-reports"]']
  ]){
    await open(pathname,ready);await shot('mobile-'+name);
    if(name==='stock'){await page.locator('[aria-label^="عرض مخزون"]').first().click();await shot('mobile-stock-inspector');await page.getByRole('button',{name:'العودة إلى المخزون',exact:true}).click();}
    if(name==='catalog'){await page.getByRole('button',{name:'عرض '+f.products[0].name,exact:true}).click();await shot('mobile-catalog-inspector');}
    if(name==='components'){await page.getByRole('button',{name:'إضافة مكوّن أو خدمة',exact:true}).click();await page.getByRole('dialog').waitFor();await shot('mobile-component-form');const focused=await page.evaluate(()=>document.activeElement?.placeholder);assert.equal(focused,'مثل: تطريز الاسم');await page.getByRole('button',{name:'إلغاء',exact:true}).click();}
    await page.getByRole('button',{name:'صفحات المخزون',exact:true}).click();await page.getByRole('dialog').waitFor();await page.getByRole('dialog').getByRole('link',{name:'الرئيسية',exact:true}).click();await page.getByRole('dialog').waitFor({state:'detached'});
  }
  await page.setViewportSize({width:1440,height:1050});
  await open('/inventory-v2?tab=stock','.mz-stock-detail-table tbody tr');
  stockFailure=true;await page.getByRole('button',{name:'تحديث المخزون',exact:true}).click();await page.getByRole('alert').first().waitFor();await shot('desktop-stock-error');assert.equal(await page.locator('.mz-stock-detail-table').count(),0);
  stockFailure=false;await page.getByRole('button',{name:'تحديث المخزون',exact:true}).click();await page.locator('.mz-stock-detail-table tbody tr').first().waitFor();
  assert.equal(pageErrors.length,0,'Browser page errors: '+pageErrors.join('; '));
  const report={synthetic_only:true,production_modified:false,visited,blocked,apiCalls,pageErrors};
  fs.writeFileSync(path.join(output,'frontend-report.json'),JSON.stringify(report,null,2));await browser.close();
  process.stdout.write(JSON.stringify({output,screenshots:visited.length,blocked:blocked.length,pageErrors},null,2)+'\n');
}
module.exports={dataFor};
if(require.main===module)run().catch(async error=>{if(activePage){await activePage.screenshot({path:path.join(output,'failure.png'),fullPage:true,timeout:15000,animations:'disabled'}).catch(()=>{});fs.writeFileSync(path.join(output,'failure-report.json'),JSON.stringify({error:error.message,visited,blocked,pageErrors,apiCalls},null,2));}await activeBrowser?.close();process.stderr.write(error.stack+'\n');process.exitCode=1;});
