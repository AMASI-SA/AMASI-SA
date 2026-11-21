// Synthetic review only. Serves the actual compiled App and a loopback fixture
// gateway. Native count/purchase requests go only to the disposable ASGI fixture.
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
if(!process.argv.includes('--isolated-test-data'))throw Error('Explicit --isolated-test-data flag required');
const {dataFor}=require('./frontend-browser-check.cjs'),f=require('./frontend-fixtures.cjs');
const UI='http://127.0.0.1:4174',API='http://127.0.0.1:4175';
const build=path.resolve(__dirname,'../../../frontend/build');
const csp="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data: blob: http://127.0.0.1:4175; connect-src 'self' http://127.0.0.1:4175; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
const ribbon='<div data-native-test-preview style="padding:9px 12px;background:#fff4ce;color:#6b520d;text-align:center;font:13px Tahoma;direction:rtl">معاينة الكود الفعلي · بيانات اختبار محلية فقط · المشتريات والجرد والإدخال في قاعدة معزولة · لا اتصال بالإنتاج أو نشر إلى سلة</div>';
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json','.svg':'image/svg+xml','.ttf':'font/ttf','.woff2':'font/woff2','.png':'image/png','.webp':'image/webp','.jpg':'image/jpeg'};
http.createServer((req,res)=>{
  if(req.headers.host!=='127.0.0.1:4174'){res.writeHead(403);return res.end();}
  const url=new URL(req.url,UI),candidate=path.resolve(build,'.'+decodeURIComponent(url.pathname));
  if(!candidate.startsWith(build+path.sep)&&candidate!==build){res.writeHead(403);return res.end();}
  const file=fs.existsSync(candidate)&&fs.statSync(candidate).isFile()?candidate:path.join(build,'index.html');
  const ext=path.extname(file);res.writeHead(200,{'content-type':types[ext]||'application/octet-stream','content-security-policy':csp,'cache-control':'no-store','x-mezan-local-preview':'synthetic-only'});
  // The response uses the stricter loopback-only CSP above; the production
  // meta CSP disallows a local HTTP fixture and must not coexist with it.
  if(ext==='.html')res.end(fs.readFileSync(file,'utf8').replace(/<meta http-equiv="Content-Security-Policy"[^>]*>/,'').replace('<body>','<body>'+ribbon));else fs.createReadStream(file).pipe(res);
}).listen(4174,'127.0.0.1');
http.createServer(async(req,res)=>{
  const cors={'access-control-allow-origin':UI,'access-control-allow-credentials':'true','access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS','access-control-allow-headers':'content-type,x-csrf-token,x-requested-with'};
  if(req.headers.host!=='127.0.0.1:4175'||(req.headers.origin&&req.headers.origin!==UI)){res.writeHead(403);return res.end();}
  if(req.method==='OPTIONS'){res.writeHead(204,cors);return res.end();}
  const url=new URL(req.url,API);
  try{
    if(url.pathname==='/fixture-assets/product.svg'){res.writeHead(200,{...cors,'content-type':'image/svg+xml'});return res.end(f.image);}
    const native=['/api/inventory-v2/stocktakes','/api/purchase-invoices','/api/operational-balances/standalone-inventory','/api/products-v2/creation-drafts'].some(prefix=>url.pathname===prefix||url.pathname.startsWith(prefix+'/'))||url.pathname==='/api/operational-balances/inventory'||url.pathname==='/api/counterparties';
    if(native){
      const chunks=[];for await(const chunk of req)chunks.push(chunk);
      const response=await fetch('http://127.0.0.1:4176'+url.pathname+url.search,{method:req.method,headers:{'content-type':'application/json'},body:['GET','HEAD'].includes(req.method)?undefined:Buffer.concat(chunks)});
      res.writeHead(response.status,{...cors,'content-type':'application/json'});return res.end(Buffer.from(await response.arrayBuffer()));
    }
    const value=dataFor(null,{url:()=>url.href,method:()=>req.method});
    if(url.pathname==='/api/auth/me')value.id='owner';
    res.writeHead(200,{...cors,'content-type':'application/json'});res.end(JSON.stringify(value));
  }catch{res.writeHead(503,{...cors,'content-type':'application/json'});res.end(JSON.stringify({detail:{message:'تعذرت خدمة المعاينة المحلية؛ لم تُرسل أي طلبات للإنتاج.'}}));}
}).listen(4175,'127.0.0.1');
process.stdout.write('Native compiled preview: '+UI+' — synthetic data only; production blocked\n');
