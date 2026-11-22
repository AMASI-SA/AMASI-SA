import React, { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import { grantedWebLink, managedWebPages } from '../../lib/webPageAccess';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { authIdentity, useBalanceRead } from './simpleBalancesHooks';
import { movementDate, movementRecorder, movementTitle, validContext } from './simpleBalancesModel';
import { Amount, Heading, Icon, IconBox, isAsset } from './ApprovedFinanceUI';
import ApprovedHome from './ApprovedHome';
import ApprovedDirectory from './ApprovedDirectory';
import './approvedFinance.css';
import './approvedCashBoxes.css';

let ledgerModule;
let ledgerRequest;
function loadLedger() {
  if (!ledgerRequest) ledgerRequest = import('./ApprovedLedger').then(module => {
    ledgerModule = module.default;
    return module;
  }).catch(error => { ledgerRequest = undefined; throw error; });
  return ledgerRequest;
}
const LazyLedger = lazy(loadLedger);
const Entry = lazy(()=>import('./ApprovedEntry'));
const AddOperationalEntity = lazy(()=>import('./AddOperationalEntity'));
const SupplierCreditPurchase = lazy(()=>import('./SupplierCreditPurchase'));
const Refund = lazy(()=>import('./ApprovedRefund'));
const Issues = lazy(()=>import('./ApprovedOrderIssues'));
const Tools = lazy(()=>import('./ApprovedOperations'));
const Position = lazy(()=>import('./ApprovedOperations').then(m=>({default:m.ApprovedPosition})));
const ReviewCollections = lazy(()=>import('./SimpleBalanceReviews'));
const SpecializedEntry = lazy(()=>import('./SimpleBalances').then(m=>({default:m.EntryForm})));
const History = lazy(()=>import('./SimpleBalances').then(m=>({default:m.AutomaticHistory})));
const Reports = lazy(()=>import('./ApprovedReports'));

const navigation=[
  ['home','الرئيسية','layout-dashboard'],['assets','الأصول والحسابات','wallet'],['log','سجل الحركات','receipt-text'],
  ['parties','الأشخاص والجهات','users'],['reviews','تحتاج مراجعة','list-checks'],['position','المركز المالي','chart-no-axes-column'],
  ['reports','التقارير','chart-no-axes-column'],['tools','العمليات والكشوف','layers'],
];
const titles = Object.fromEntries(navigation.map(([key,label])=>[key,label]));
// Bound the complete context attempt, including queue and session-refresh waits.
// An inactive page exposes retry instead of deferring an indefinite loader.
const contextReadPolicy = { singleAttemptTimeoutMs: 30000, deferUntilVisible: false };
function routeTo(path, view, resource, filters) {
  const params=new URLSearchParams({view});
  if(resource){params.set('kind',resource.kind);params.set('id',resource.id);}
  for(const [key,val] of Object.entries(filters||{})) if(val)params.set(key,val);
  return path+'?'+params;
}
export function ApprovedFinanceShell({context,view,title,active,to,user,children}) {
  const [drawer,setDrawer]=useState(false);
  const {pathname,search}=useLocation();
  useEffect(()=>{setDrawer(false);window.scrollTo(0,0)},[pathname,search]);
  useEffect(()=>{if(!drawer)return; const hide=e=>{if(e.key==='Escape')setDrawer(false)};document.addEventListener('keydown',hide);return()=>document.removeEventListener('keydown',hide)},[drawer]);
  const pages=managedWebPages(user);
  const firstFinance=pages?.find(p=>p.section==='balances')?.to || to('home');
  const backTo=pages?.find(p=>!p.to.startsWith('/operational-balances'))?.to || (managedWebPages(user)?'/':'/dashboard-v2');
  const destination=key=>key==='position' && pages && !grantedWebLink(pages,to(key)) && grantedWebLink(pages,'/operational-balances/financial-position')
    ? '/operational-balances/financial-position' : to(key);
  return <div className="mz-finance" dir="rtl"><div className={'shell'+(drawer?' mobile-nav-open':'')}>
    {drawer&&<button className="mobile-mask" aria-label="إغلاق القائمة" onClick={()=>setDrawer(false)}/>}
    <aside className="side"><Link className="brand" to={firstFinance}><span className="brand-mark"><Icon name="scale"/></span><div><b>ميزان ٢</b><small>{user?.store_name||user?.company_name||'الأرصدة التشغيلية'}</small></div></Link>
    <nav className="nav" aria-label="صفحات الأرصدة">{navigation.filter(([key])=>(key!=='position'||context.actor.is_owner||pages) && (key!=='reports'||context.actor.is_owner) && grantedWebLink(pages,destination(key))).map(([key,label,icon])=>
      <Link key={key} to={destination(key)} className={active===key?'on':''} aria-current={active===key?'page':undefined}><Icon name={icon}/>{label}</Link>)}</nav>
    <div className="side-foot"><div className="profile"><span className="avatar">{context.actor.name?.slice(0,1)||'م'}</span><div><b>{context.actor.name}</b><small>{context.actor.is_owner?'مالك المؤسسة':'موظف العمليات'}</small></div></div><Link to={backTo} className="textbtn finance-return"><Icon name="chevron-left"/>العودة إلى ميزان</Link></div></aside>
    <div className="workspace"><header className="bar"><div className="trail"><button className="desktop-hide glyph-btn" aria-label="القائمة" aria-expanded={drawer} onClick={()=>setDrawer(!drawer)}><Icon name="menu"/></button><Link to={firstFinance}>ميزان ٢</Link><span className="sep">/</span><b>{title}</b></div>
      {view!=='tools'&&grantedWebLink(pages,to('tools'))&&<Link className="textbtn" to={to('tools')}><Icon name="layers"/>العمليات والكشوف</Link>}</header>
    <main className="content" id="finance-main">{children}</main></div></div></div>;
}
function Dialog({title,onClose,children,locked=false}) {
  const ref=useRef(null);
  useEffect(()=>{const node=ref.current;if(node&&!node.open)node.showModal();return()=>{if(node?.open)node.close()}},[]);
  return <dialog ref={ref} aria-label={title} onCancel={event=>{if(locked)event.preventDefault();else onClose();}}><div className="between"><h2>{title}</h2><button type="button" className="glyph-btn" disabled={locked} onClick={onClose} aria-label="إغلاق"><Icon name="x"/></button></div>{children}</dialog>;
}
function FinanceWorkspace({context,identity,user,view,to,params,reload}) {
  const navigate=useNavigate(), location=useLocation();
  const [modal,setModal]=useState(null), [historyPage,setHistoryPage]=useState(1);
  const [creatingCash,setCreatingCash]=useState(false);
  const deferredRefresh=useRef(false);
  useEffect(()=>{
    if(deferredRefresh.current && view!=='refund' && view!=='collections'){
      deferredRefresh.current=false;
      reload();
    }
  },[reload,view]);
  const refreshAfterReview=()=>{deferredRefresh.current=true;};
  const scoped=params.has('id') || params.has('kind');
  const selected=scoped?context.resources.find(r=>r.kind===params.get('kind')&&r.id===params.get('id')):null;
  const pages=managedWebPages(user);
  const allowedTo=(viewName,resource)=>grantedWebLink(pages,to(viewName,resource));
  const writePage=id=>!pages||pages.some(p=>p.id===id&&p.mode==='write');
  const go=(name,r,more)=>{
    const dest=to(name,r,more);
    navigate(grantedWebLink(pages,dest)?dest:pages?.find(page=>page.section==='balances')?.to||'/');
  };
  const home=()=>go('home'), open=r=>go('ledger',r), issues=r=>go('issues',r);
  const granted=new Set(context.operations.map(o=>typeof o==='string'?o:o.key));
  const canRefund=allowedTo('refund')&&(granted.has('customer.refund_request')||granted.has('customer.refund_execute'));
  const canCollect=allowedTo('collections')&&granted.has('courier.review')&&context.resources.some(r=>r.kind==='store_driver'&&r.access.movements==='all');
  const addAvailable=allowedTo('entry')&&writePage('balances.entry')&&context.status==='active'&&context.capabilities?.create_entries===true;
  const canAdd=addAvailable && (!selected||selected.access?.use&&selected.operations?.length>0);
  const createCash=context.actor.is_owner && ['draft','active'].includes(context.status) ? ()=>setModal({cash:true}) : null;
  const purchaseSuppliers=addAvailable && granted.has('supplier.accrual') ? context.resources.filter(r=>
    r.kind==='supplier' && r.access?.use && r.operations?.includes('supplier.accrual')) : [];
  const canPurchase=selected ? purchaseSuppliers.includes(selected) : purchaseSuppliers.length>0;
  const supplierNeedsOtherEntry=selected?.kind==='supplier' && !selected.operations?.includes('supplier.payment');
  const purchaseMode=params.get('mode')==='purchase';
  const purchaseLink=r=>to('special-entry',r,{mode:'purchase'});
  const add=r=>{
    const mode=r?.prepaid_shipping_cash?'wallet':r?.kind==='ad_account'?'ads':r?.kind==='courier'?'choose':
      ['supplier','employee','employee_custody','operating_expense','external_person'].includes(r?.kind)?'out':
      ['provider','pos_receivable','courier','store_driver'].includes(r?.kind)?'in':'choose';
    go('entry',r,{mode});
  };
  const collections=(r,status)=>go('collections',r,{review_status:status,review_driver:r?.kind==='store_driver'?r.id:'',review_bank:r?.kind==='bank'?r.id:''});
  const statementTarget=(r,section='upload')=>'/operational-balances/provider-statements?provider='+encodeURIComponent(r.id)+'#'+section;
  const canStatements=selected?.kind==='provider' && grantedWebLink(pages,statementTarget(selected,'history'));
  const canHistory=context.actor.is_owner && selected?.kind==='provider' && grantedWebLink(pages,'/operational-balances/provider-history');
  const openSpecialist=target=>navigate(target,{state:{financeReturnTo:location.pathname+location.search}});
  const returnDirectory=()=>selected?go(isAsset(selected.kind)?'assets':'parties'):home();
  const back=()=>selected?open(selected):home();
  const saved=(result,recovery)=>{
    const r=context.resources.find(item=>item.kind===result?.party_type&&item.id===result?.party_id);
    if(result?.invoice_number && r){
      const route=r.access?.movements!=='none' && allowedTo('ledger',r)?to('ledger',r):to('entry');
      navigate(route,{state:{supplierInvoice:{id:r.id,number:result.invoice_number,date:result.business_date}}});
    }else if(recovery?.recovered && r && result.movement_id){
      const route=r.access?.movements!=='none' && allowedTo('ledger',r)?to('ledger',r):to('entry');
      navigate(route,{state:{recoveredMovement:{kind:r.kind,id:r.id,movement:result.movement_id,
        date:recovery.business_date,scope:context.session_scope}}});
    }else if(r)open(r);else go('log');
    reload();
  };
  const savedInvoice=location.state?.supplierInvoice;
  const recovered=location.state?.recoveredMovement?.scope===context.session_scope?location.state.recoveredMovement:null;
  const unavailable=<p className="note" role="alert">هذه الصفحة غير متاحة ضمن صلاحياتك.</p>;
  const title=titles[view]||({ledger:selected&&isAsset(selected.kind)?'كشف الحساب':'كشف الطرف',entry:'حركة مالية جديدة','special-entry':canPurchase && (purchaseMode || selected && params.get('mode')!=='movement')?'فاتورة شراء آجل':'عمليات مالية أخرى',issues:'طلبات تحتاج مراجعة',collections:'مراجعة التحصيلات',refund:'إرجاع مبلغ لعميل'})[view]||'الأرصدة التشغيلية';
  const active=view==='ledger'?(isAsset(selected?.kind)?'assets':'parties'):view==='issues'||view==='collections'?'reviews':view==='entry'||view==='refund'?'home':view;
  const shared={context,identity};
  useEffect(()=>{setModal(null);setHistoryPage(1)},[location.key]);
  useEffect(()=>{const previous=document.title;document.title=title+' · ميزان ٢';return()=>{document.title=previous}},[title]);
  const Ledger=ledgerModule || LazyLedger;
  let content;
  if(scoped&&!selected)content=unavailable;
  else switch(view){
    case 'assets':case 'parties':content=<ApprovedDirectory key={view} context={context} people={view==='parties'} to={to} onAdd={canAdd?()=>add(null):null} onCreateCash={view==='assets'?createCash:null}/>;break;
    case 'log':case 'ledger':content=view==='ledger'&&!selected?unavailable:<Ledger key={JSON.stringify([view,selected?.kind,selected?.id])} {...shared} resource={selected} initialMovement={selected?.id===recovered?.id && selected?.kind===recovered?.kind?recovered:null} initialInvoice={selected && savedInvoice?.id===selected.id?savedInvoice:null} onAdd={canAdd && (!supplierNeedsOtherEntry || selected.operations.some(key=>key!=='supplier.accrual')) ? (supplierNeedsOtherEntry?r=>go('special-entry',r,{mode:'movement'}):add) : null} onPurchase={selected && canPurchase?()=>navigate(purchaseLink(selected)):null} onBack={returnDirectory} onReviewOrders={allowedTo('issues')?issues:undefined}
      canOpenOrderDetails={Boolean(context.actor?.is_owner || pages?.some(page=>page.id==='detail.order' && !page.owner_only))}
      onStatements={canStatements?(r,section)=>openSpecialist(statementTarget(r,section)):undefined}
      canUploadStatements={Boolean(canStatements && writePage('balances.statements'))}
      onProviderHistory={canHistory?r=>openSpecialist('/operational-balances/provider-history?provider='+encodeURIComponent(r.id)):undefined}
      onHistory={row=>{setHistoryPage(1);setModal({history:context.resources.find(r=>r.kind===row.party_type&&r.id===row.party_id)})}}
      onExpected={(r,summary)=>setModal({expected:summary,resource:r})} onReviewCollections={canCollect?collections:undefined}/>;break;
    case 'special-entry': {
      if(!canAdd || (purchaseMode && !canPurchase)) { content=unavailable; break; }
      if(selected){
        const buy=canPurchase && params.get('mode')!=='movement';
        content=<>{buy?<SupplierCreditPurchase key={selected.id} context={context} resource={selected} onSaved={saved}/>:
          <><Heading title={'عمليات '+selected.name}/><SpecializedEntry context={context} resource={selected} onCancel={back} onSaved={saved}/></>}
          {canPurchase && <p className="footnote"><Link className="textbtn" to={buy?to('special-entry',selected,{mode:'movement'}):purchaseLink(selected)}>{buy?'عمليات المورد الأخرى':'شراء آجل من المورد'}</Link></p>}</>;
      }else{
        const choices=purchaseMode?purchaseSuppliers:context.resources.filter(r=>r.access?.use && r.operations?.length);
        content=<><Heading title={purchaseMode?'شراء آجل من مورد':'عمليات مالية أخرى'} subtitle={purchaseMode?'اختر المورد الذي استلمت منه المنتجات أو الخدمات.':'اختر الحساب أو الجهة لتظهر العمليات المتاحة لك.'}/>
          <div className="panel sheet">{choices.map(r=><Link key={r.kind+':'+r.id} className="review-item" to={purchaseMode?purchaseLink(r):to('special-entry',r)}>{r.name}<Icon name="chevron-left" className="end"/></Link>)}</div></>;
      }
      break;
    }
    case 'entry':content=!writePage('balances.entry')?<><Heading title="حركة مالية جديدة"/><p className="note">صلاحيتك للعرض. تسجيل حركة يتطلب صلاحية الكتابة على الصفحة والحساب.</p></>:<>
      <Entry {...shared} resource={selected} mode={params.get('mode')||'choose'} onBack={back} onSaved={saved}
        onPurchase={canPurchase?()=>navigate(purchaseLink(selected)):null} onRefund={canRefund?()=>go('refund'):null}/>
      {canAdd && <p className="footnote"><Link className="textbtn" to={to('special-entry',selected,{mode:'movement'})}><Icon name="layers"/>عمليات مالية أخرى</Link></p>}</>;break;
    case 'refund':content=canRefund?<Refund {...shared} readOnly={!writePage('balances.refund')} onBack={()=>go('entry')} onSaved={refreshAfterReview}/>:unavailable;break;
    case 'issues':content=<Issues {...shared} resource={selected} onBack={()=>selected?open(selected):go('reviews')} onReviewCollections={canCollect?collections:undefined}/>;break;
    case 'collections':content=canCollect?<ReviewCollections {...shared} readOnly={!writePage('balances.collections')} driverId={selected?.kind==='store_driver'?selected.id:''} onBack={()=>go('reviews')} onChanged={refreshAfterReview}/>:unavailable;break;
    case 'reviews':content=<><Heading title="تحتاج مراجعة" subtitle="افتح الطلب أو الحركة لمعرفة السبب ومراجعة المصدر."/>
      {allowedTo('issues')&&context.resources.some(r=>Object.hasOwn(r,'order_issues_count'))&&<Link to={to('issues')} className="issue-notice"><IconBox name="clipboard-list" tone="c-wait"/><span><strong>طلبات تحتاج مراجعة</strong><small>اعرض أرقام الطلبات وسبب المراجعة لكل طلب.</small></span><b>عرض الطلبات <Icon name="chevron-left"/></b></Link>}
      {canCollect?<section className="panel sheet"><div className="section-head"><h2>حركات مالية تنتظر المراجعة</h2></div><Link className="review-item" to={to('collections')}><IconBox name="receipt-text" tone="provider"/><span><b>مراجعة تحصيلات الموصلين</b><small className="under">تحويلات العملاء وإيصالات الشبكة</small></span><Icon name="chevron-left" className="end"/></Link></section>:
      <p className="under">تظهر لك المراجعات المتاحة لحسابك.</p>}
      {context.actor.is_owner && <section className="panel sheet"><div className="section-head"><h2>مراجعات الأرصدة والمصادر</h2></div>
        <Link className="review-item" to="/operational-balances/shipping-review"><IconBox name="truck" tone="party"/><span>مراجعة أجور الشحن</span><Icon name="chevron-left" className="end"/></Link>
        <Link className="review-item" to="/operational-balances/provider-history"><IconBox name="history" tone="party"/><span>مراجعة الطلبات السابقة</span><Icon name="chevron-left" className="end"/></Link>
      </section>}</>;break;
    case 'position':content=context.actor.is_owner||pages?<Position {...shared} scoped={!context.actor.is_owner} onBack={home} onOpen={r=>{if(allowedTo('ledger',r))open(r)}} canOpen={r=>allowedTo('ledger',r)}/>:unavailable;break;
    case 'tools':content=<Tools {...shared} pages={pages} onBack={home}/>;break;
    case 'reports':content=context.actor.is_owner?<Reports {...shared}/>:unavailable;break;
    default:content=<ApprovedHome context={context} to={to} allowedTo={allowedTo} canAdd={canAdd} onCreateCash={createCash} onDetail={row=>setModal({row})}/>;
  }
  const row=modal?.row;
  return <ApprovedFinanceShell context={context} user={user} view={view} title={title} active={active} to={to}>{recovered && <p className="note" role="status">سبق تسجيل الحركة. رقم الإيصال <bdi>{recovered.movement}</bdi></p>}{savedInvoice && <p className="note" role="status">حُفظت فاتورة الشراء الآجل <bdi>{savedInvoice.number}</bdi> على حساب المورد.</p>}{view==='ledger' && selected?.kind==='cash' && selected.id===location.state?.createdCashId && <p className="note" role="status">تمت إضافة {selected.name}.</p>}<Suspense fallback={<p className="empty" role="status">جارٍ فتح الصفحة…</p>}>{content}</Suspense>
    {modal?.cash && createCash && <Dialog title="إضافة صندوق نقدي" locked={creatingCash} onClose={()=>setModal(null)}>
      <div className="cash-create"><Suspense fallback={<p role="status">جارٍ فتح النموذج…</p>}>
        <AddOperationalEntity kind="cash" onBusyChange={setCreatingCash} onCancel={()=>setModal(null)} onAdded={account=>{
          setModal(null);
          navigate(to('ledger',account),{state:{createdCashId:account.id}});
          reload();
        }}/>
      </Suspense></div></Dialog>}
    {modal&&!modal.cash&&<Dialog title={row?'تفاصيل الحركة':modal.history?'سجل الحركات التلقائية':'متوقع تحصيله'} onClose={()=>setModal(null)}>
      {row?<><h3>{movementTitle(row)}</h3><p className="bigamt"><Amount value={row.amount} currency={row.currency}/></p><dl className="detail-list">
        {Object.entries({'الحساب':row.party_name,'سجّلها':movementRecorder(row),'تاريخ الحركة':movementDate(row,true),'رقم الطلب':row.order_number,'المرجع':row.reference,'المصدر':row.source_label,'الملاحظة':row.note}).filter(([,v])=>v).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl></>:
      modal.history?<Suspense fallback={<p>جارٍ فتح السجل…</p>}><History resource={modal.history} {...shared} page={historyPage} onPage={setHistoryPage} onDetail={r=>setModal({row:r})}/></Suspense>:
      modal.expected&&<><p>{modal.expected.total} طلب · <Amount value={modal.expected.amount} currency={modal.resource?.currency}/></p>
        <ul>{modal.expected.items?.map(r=><li key={r.id}>{r.order_number||movementTitle(r)} · <Amount value={r.amount} currency={r.currency}/></li>)}</ul></>}</Dialog>}</ApprovedFinanceShell>;
}

export default function ApprovedFinance({defaultView='home'}) {
  const auth=useOptionalAuth(), location=useLocation(), identity=authIdentity(auth);
  const params=new URLSearchParams(location.search), view=params.get('view')||defaultView;
  // The selected page's code can load during the authorized context request.
  useEffect(()=>{if(view==='log'||view==='ledger')loadLedger().catch(()=>{});},[view]);
  const [homeRequested,setHomeRequested]=useState(view==='home');
  useEffect(()=>{if(view==='home')setHomeRequested(true)},[view]);
  const pages=managedWebPages(auth?.user);
  const requestPreview=(homeRequested||view==='home') && (!pages||pages.some(p=>p.id==='balances.home'));
  // Reports read their own bound snapshot; their context needs only the directory.
  const directoryOnly=!requestPreview && ['entry','ledger','log','reports'].includes(view);
  const read=useBalanceRead(async config=>{
    const value=await api.context(config,{homePreview:requestPreview,...(directoryOnly && {directoryOnly:true})});
    if(!validContext(value)||auth?.user?.id&&auth.user.id!==value.actor.id)throw new Error('invalid finance context');
    return directoryOnly ? {...value,directoryOnly:true} : value;
  },JSON.stringify([identity,requestPreview,directoryOnly,pages?.map(p=>[p.id,p.mode])]),!auth||Boolean(auth.user?.id)&&!auth.loading,contextReadPolicy);
  const to=(name,resource,extra)=>routeTo(location.pathname,name,resource,extra);
  const sessionUnavailable=auth&&!auth.loading&&!auth.user?.id;
  const error=sessionUnavailable?'تعذر التحقق من جلسة الدخول. أعد التحقق لعرض الأرصدة.':read.error;
  const retry=sessionUnavailable?(auth.retryAuth||(()=>window.location.reload())):read.reload;
  if(!read.data)return <div className="mz-finance" dir="rtl"><main className="content"><Heading title="الأرصدة التشغيلية"/>{error?
    <><p role="alert" className="note">{error}</p><button className="button" onClick={retry}>{sessionUnavailable?'إعادة التحقق من الجلسة':'إعادة المحاولة'}</button>
    {!sessionUnavailable&&grantedWebLink(pages,'/operational-balances?view=specialized')&&<Link className="textbtn" to="/operational-balances?view=specialized">الإعدادات والعمليات المتخصصة</Link>}</>:<p role="status">{auth?.loading?'جارٍ التحقق من الجلسة…':'جارٍ تحميل الأرصدة…'}</p>}</main></div>;
  return <FinanceWorkspace key={JSON.stringify([identity,read.data.session_scope,read.data.permission_version])} identity={identity} user={auth?.user} context={read.data} view={view} params={params} to={to} reload={read.reload}/>;
}
