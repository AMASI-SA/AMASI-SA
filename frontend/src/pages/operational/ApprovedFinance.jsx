import React, { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import { managedWebPages } from '../../lib/webPageAccess';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { authIdentity, useBalanceRead } from './simpleBalancesHooks';
import { movementDate, movementRecorder, movementTitle, validContext } from './simpleBalancesModel';
import { Amount, Heading, Icon, IconBox, isAsset } from './ApprovedFinanceUI';
import ApprovedHome from './ApprovedHome';
import ApprovedDirectory from './ApprovedDirectory';
import './approvedFinance.css';

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
const Refund = lazy(()=>import('./ApprovedRefund'));
const Issues = lazy(()=>import('./ApprovedOrderIssues'));
const Tools = lazy(()=>import('./ApprovedOperations'));
const Position = lazy(()=>import('./ApprovedOperations').then(m=>({default:m.ApprovedPosition})));
const ReviewCollections = lazy(()=>import('./SimpleBalanceReviews'));
const SpecializedEntry = lazy(()=>import('./SimpleBalances').then(m=>({default:m.EntryForm})));
const History = lazy(()=>import('./SimpleBalances').then(m=>({default:m.AutomaticHistory})));

const navigation=[
  ['home','الرئيسية','layout-dashboard'],['assets','الأصول والحسابات','wallet'],['log','سجل الحركات','receipt-text'],
  ['parties','الأشخاص والجهات','users'],['reviews','تحتاج مراجعة','list-checks'],['position','المركز المالي','chart-no-axes-column'],
  ['tools','العمليات والكشوف','layers'],
];
const titles = Object.fromEntries(navigation.map(([key,label])=>[key,label]));
function routeTo(path, view, resource, filters) {
  const params=new URLSearchParams({view});
  if(resource){params.set('kind',resource.kind);params.set('id',resource.id);}
  for(const [key,val] of Object.entries(filters||{})) if(val)params.set(key,val);
  return path+'?'+params;
}
function Shell({context,view,title,active,to,user,children}) {
  const [drawer,setDrawer]=useState(false);
  const {search}=useLocation();
  useEffect(()=>{setDrawer(false);window.scrollTo(0,0)},[search]);
  useEffect(()=>{if(!drawer)return; const hide=e=>{if(e.key==='Escape')setDrawer(false)};document.addEventListener('keydown',hide);return()=>document.removeEventListener('keydown',hide)},[drawer]);
  const backTo=managedWebPages(user)?.find(p=>!p.to.startsWith('/operational-balances'))?.to || (managedWebPages(user)?'/':'/dashboard-v2');
  return <div className="mz-finance" dir="rtl"><div className={'shell'+(drawer?' mobile-nav-open':'')}>
    {drawer&&<button className="mobile-mask" aria-label="إغلاق القائمة" onClick={()=>setDrawer(false)}/>}
    <aside className="side"><Link className="brand" to={to('home')}><span className="brand-mark"><Icon name="scale"/></span><div><b>ميزان ٢</b><small>{user?.store_name||user?.company_name||'الأرصدة التشغيلية'}</small></div></Link>
    <nav className="nav" aria-label="صفحات الأرصدة">{navigation.filter(([key])=>key!=='position'||context.actor.is_owner).map(([key,label,icon])=>
      <Link key={key} to={to(key)} className={active===key?'on':''} aria-current={active===key?'page':undefined}><Icon name={icon}/>{label}</Link>)}</nav>
    <div className="side-foot"><div className="profile"><span className="avatar">{context.actor.name?.slice(0,1)||'م'}</span><div><b>{context.actor.name}</b><small>{context.actor.is_owner?'مالك المؤسسة':'موظف العمليات'}</small></div></div><Link to={backTo} className="textbtn finance-return"><Icon name="chevron-left"/>العودة إلى ميزان</Link></div></aside>
    <div className="workspace"><header className="bar"><div className="trail"><button className="desktop-hide glyph-btn" aria-label="القائمة" aria-expanded={drawer} onClick={()=>setDrawer(!drawer)}><Icon name="menu"/></button><Link to={to('home')}>ميزان ٢</Link><span className="sep">/</span><b>{title}</b></div>
      {view!=='tools'&&<Link className="textbtn" to={to('tools')}><Icon name="layers"/>العمليات والكشوف</Link>}</header>
    <main className="content" id="finance-main">{children}</main></div></div></div>;
}
function Dialog({title,onClose,children}) {
  const ref=useRef(null);
  useEffect(()=>{const node=ref.current;if(node&&!node.open)node.showModal();return()=>{if(node?.open)node.close()}},[]);
  return <dialog ref={ref} aria-label={title} onCancel={onClose}><div className="between"><h2>{title}</h2><button type="button" className="glyph-btn" onClick={onClose} aria-label="إغلاق"><Icon name="x"/></button></div>{children}</dialog>;
}
function FinanceWorkspace({context,identity,user,view,to,params,reload}) {
  const navigate=useNavigate(), location=useLocation();
  const [modal,setModal]=useState(null), [historyPage,setHistoryPage]=useState(1);
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
  const go=(name,r,more)=>navigate(to(name,r,more));
  const home=()=>go('home'), open=r=>go('ledger',r), issues=r=>go('issues',r);
  const granted=new Set(context.operations.map(o=>typeof o==='string'?o:o.key));
  const canRefund=granted.has('customer.refund_request')||granted.has('customer.refund_execute');
  const canCollect=granted.has('courier.review')&&context.resources.some(r=>r.kind==='store_driver'&&r.access.movements==='all');
  const addAvailable=context.status==='active'&&context.capabilities?.create_entries===true;
  const canAdd=addAvailable && (!selected||selected.access?.use&&selected.operations?.length>0);
  const add=r=>{
    const mode=r?.prepaid_shipping_cash?'wallet':r?.kind==='ad_account'?'ads':
      ['supplier','employee','employee_custody','operating_expense','external_person'].includes(r?.kind)?'out':
      ['provider','pos_receivable','courier','store_driver'].includes(r?.kind)?'in':'choose';
    go('entry',r,{mode});
  };
  const collections=(r,status)=>go('collections',r,{review_status:status,review_driver:r?.kind==='store_driver'?r.id:'',review_bank:r?.kind==='bank'?r.id:''});
  const returnDirectory=()=>selected?go(isAsset(selected.kind)?'assets':'parties'):home();
  const back=()=>selected?open(selected):home();
  const saved=result=>{
    const r=context.resources.find(item=>item.kind===result?.party_type&&item.id===result?.party_id);
    if(r)open(r);else go('log');reload();
  };
  const unavailable=<p className="note" role="alert">هذه الصفحة غير متاحة ضمن صلاحياتك.</p>;
  const title=titles[view]||({ledger:selected&&isAsset(selected.kind)?'كشف الحساب':'كشف الطرف',entry:'حركة مالية جديدة','special-entry':'عمليات مالية أخرى',issues:'طلبات تحتاج مراجعة',collections:'مراجعة التحصيلات',refund:'إرجاع مبلغ لعميل'})[view]||'الأرصدة التشغيلية';
  const active=view==='ledger'?(isAsset(selected?.kind)?'assets':'parties'):view==='issues'||view==='collections'?'reviews':view==='entry'||view==='refund'?'home':view;
  const shared={context,identity};
  useEffect(()=>{setModal(null);setHistoryPage(1)},[location.key]);
  useEffect(()=>{const previous=document.title;document.title=title+' · ميزان ٢';return()=>{document.title=previous}},[title]);
  const Ledger=ledgerModule || LazyLedger;
  let content;
  if(scoped&&!selected)content=unavailable;
  else switch(view){
    case 'assets':case 'parties':content=<ApprovedDirectory key={view} context={context} people={view==='parties'} to={to} onAdd={canAdd?()=>add(null):null}/>;break;
    case 'log':case 'ledger':content=view==='ledger'&&!selected?unavailable:<Ledger key={JSON.stringify([view,selected?.kind,selected?.id])} {...shared} resource={selected} onAdd={canAdd?add:null} onBack={returnDirectory} onReviewOrders={issues}
      onHistory={row=>{setHistoryPage(1);setModal({history:context.resources.find(r=>r.kind===row.party_type&&r.id===row.party_id)})}}
      onExpected={(r,summary)=>setModal({expected:summary,resource:r})} onReviewCollections={canCollect?collections:undefined}/>;break;
    case 'special-entry':content=!canAdd?unavailable:selected?<><Heading title={'عمليات '+selected.name}/><SpecializedEntry context={context} resource={selected} onCancel={back} onSaved={saved}/></>:
      <><Heading title="عمليات مالية أخرى" subtitle="اختر الحساب أو الجهة لتظهر العمليات المتاحة لك."/>
      <div className="panel sheet">{context.resources.filter(r=>r.access?.use && r.operations?.length).map(r=>
        <Link key={r.kind+':'+r.id} className="review-item" to={to('special-entry',r)}>{r.name}<Icon name="chevron-left" className="end"/></Link>)}</div></>;break;
    case 'entry':content=<><Entry {...shared} resource={selected} mode={params.get('mode')||'choose'} onBack={back} onSaved={saved} onRefund={canRefund?()=>go('refund'):null}/>
      {canAdd && <p className="footnote"><Link className="textbtn" to={to('special-entry',selected)}><Icon name="layers"/>عمليات مالية أخرى</Link></p>}</>;break;
    case 'refund':content=canRefund?<Refund {...shared} onBack={()=>go('entry')} onSaved={refreshAfterReview}/>:unavailable;break;
    case 'issues':content=<Issues {...shared} resource={selected} onBack={()=>selected?open(selected):go('reviews')} onReviewCollections={canCollect?collections:undefined}/>;break;
    case 'collections':content=canCollect?<ReviewCollections {...shared} driverId={selected?.kind==='store_driver'?selected.id:''} onBack={()=>go('reviews')} onChanged={refreshAfterReview}/>:unavailable;break;
    case 'reviews':content=<><Heading title="تحتاج مراجعة" subtitle="افتح الطلب أو الحركة لمعرفة السبب ومراجعة المصدر."/>
      {context.resources.some(r=>Object.hasOwn(r,'order_issues_count'))&&<Link to={to('issues')} className="issue-notice"><IconBox name="clipboard-list" tone="c-wait"/><span><strong>طلبات تحتاج مراجعة</strong><small>اعرض أرقام الطلبات وسبب المراجعة لكل طلب.</small></span><b>عرض الطلبات <Icon name="chevron-left"/></b></Link>}
      {canCollect?<section className="panel sheet"><div className="section-head"><h2>حركات مالية تنتظر المراجعة</h2></div><Link className="review-item" to={to('collections')}><IconBox name="receipt-text" tone="provider"/><span><b>مراجعة تحصيلات الموصلين</b><small className="under">تحويلات العملاء وإيصالات الشبكة</small></span><Icon name="chevron-left" className="end"/></Link></section>:
      <p className="under">تظهر لك المراجعات المتاحة لحسابك.</p>}</>;break;
    case 'position':content=context.actor.is_owner?<Position {...shared} onBack={home} onOpen={open}/>:unavailable;break;
    case 'tools':content=<Tools {...shared} onBack={home}/>;break;
    default:content=<ApprovedHome context={context} to={to} canAdd={canAdd} onDetail={row=>setModal({row})}/>;
  }
  const row=modal?.row;
  return <Shell context={context} user={user} view={view} title={title} active={active} to={to}><Suspense fallback={<p className="empty" role="status">جارٍ فتح الصفحة…</p>}>{content}</Suspense>
    {modal&&<Dialog title={row?'تفاصيل الحركة':modal.history?'سجل الحركات التلقائية':'متوقع تحصيله'} onClose={()=>setModal(null)}>
      {row?<><h3>{movementTitle(row)}</h3><p className="bigamt"><Amount value={row.amount} currency={row.currency}/></p><dl className="detail-list">
        {Object.entries({'الحساب':row.party_name,'سجّلها':movementRecorder(row),'تاريخ الحركة':movementDate(row,true),'رقم الطلب':row.order_number,'المرجع':row.reference,'المصدر':row.source_label,'الملاحظة':row.note}).filter(([,v])=>v).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl></>:
      modal.history?<Suspense fallback={<p>جارٍ فتح السجل…</p>}><History resource={modal.history} {...shared} page={historyPage} onPage={setHistoryPage} onDetail={r=>setModal({row:r})}/></Suspense>:
      modal.expected&&<><p>{modal.expected.total} طلب · <Amount value={modal.expected.amount} currency={modal.resource?.currency}/></p>
        <ul>{modal.expected.items?.map(r=><li key={r.id}>{r.order_number||movementTitle(r)} · <Amount value={r.amount} currency={r.currency}/></li>)}</ul></>}</Dialog>}</Shell>;
}

export default function ApprovedFinance({defaultView='home'}) {
  const auth=useOptionalAuth(), location=useLocation(), identity=authIdentity(auth);
  const params=new URLSearchParams(location.search), view=params.get('view')||defaultView;
  // The selected page's code can load during the authorized context request.
  useEffect(()=>{if(view==='log'||view==='ledger')loadLedger().catch(()=>{});},[view]);
  const [homeRequested,setHomeRequested]=useState(view==='home');
  useEffect(()=>{if(view==='home')setHomeRequested(true)},[view]);
  const requestPreview=homeRequested||view==='home';
  const read=useBalanceRead(async config=>{
    const value=await api.context(config,{homePreview:requestPreview});
    if(!validContext(value)||auth?.user?.id&&auth.user.id!==value.actor.id)throw new Error('invalid finance context');
    return value;
  },JSON.stringify([identity,requestPreview]),!auth||Boolean(auth.user?.id)&&!auth.loading);
  const to=(name,resource,extra)=>routeTo(location.pathname,name,resource,extra);
  if(!read.data)return <div className="mz-finance" dir="rtl"><main className="content"><Heading title="الأرصدة التشغيلية"/>{read.error?
    <><p role="alert" className="note">{read.error}</p><button className="button" onClick={read.reload}>إعادة المحاولة</button>
    <Link className="textbtn" to="/operational-balances?view=specialized">الإعدادات والعمليات المتخصصة</Link></>:<p role="status">جارٍ تحميل الأرصدة…</p>}</main></div>;
  return <FinanceWorkspace key={JSON.stringify([identity,read.data.session_scope,read.data.permission_version])} identity={identity} user={auth?.user} context={read.data} view={view} params={params} to={to} reload={read.reload}/>;
}
