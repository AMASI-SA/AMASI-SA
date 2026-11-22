import React, {lazy, Suspense, useEffect, useState} from 'react';
import {Link, useSearchParams} from 'react-router-dom';
import {ArrowsClockwise, WarningCircle} from '@phosphor-icons/react';
import {operationalApi as api, messageFor} from './operational/api';
import InventoryAvailability from './operational/InventoryAvailability';
import StandaloneInventoryEntry from './operational/StandaloneInventoryEntry';
import InventoryPurchases from './operational/InventoryPurchases';
import InventoryMovementsView from '../components/inventory/suite/InventoryMovementsView';
import {loadInventoryStocktakeContext,loadInventoryStocktakeMovements} from '../services/inventoryStocktakes';
import InventorySuiteFrame, {InventoryPageHeader} from '../components/inventory/suite/InventorySuiteFrame';
import './operational/operational.css';
import '../components/inventory/suite/inventoryProjectionViews.css';

const StocktakeWorkbench=lazy(()=>import('./inventory/StocktakeWorkbench'));

// A web catalog grant reads only the existing scoped warehouse projection.
// Management context and all inventory writers remain outside this lane.
export default function InventoryWorkspace({ accessMode = null, embedded = false }) {
  return accessMode ? <InventoryReadOnlyWorkspace embedded={embedded}/> : <InventoryManagement embedded={embedded}/>;
}

function WorkspaceFrame({ embedded, active, children }) {
  return embedded ? <div className="mz-inventory-ui mz-inventory-embedded" dir="rtl">{children}</div>
    : <InventorySuiteFrame active={active}>{children}</InventorySuiteFrame>;
}
function tabQuery(params, tab, item) {
  const from = params.getAll('from');
  return { ...(from.length === 1 && from[0] === 'operational-tools' ? { from: from[0] } : {}), tab, ...(item ? { item } : {}) };
}

function InventoryReadOnlyWorkspace({ embedded }) {
  const [params,setParams]=useSearchParams();
  const view=params.get('tab')==='reports'?'reports':'stock';
  return <WorkspaceFrame embedded={embedded} active={view==='reports'?'reports':'catalog'}>
    <InventoryAvailability context={{permissions:{reports:true},session_scope:'mezan-web-stock'}}
      view={view} onNavigate={value=>{if(['stock','reports'].includes(value))setParams(tabQuery(params,value));}}/>
  </WorkspaceFrame>;
}

function InventoryManagement({ embedded }) {
  const [params, setParams] = useSearchParams();
  const [context, setContext] = useState(null);
  const [stocktakeContext,setStocktakeContext]=useState(null);
  const [error, setError] = useState('');
  const [stocktakeError,setStocktakeError]=useState('');
  const [loading,setLoading]=useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setContext(null);setStocktakeContext(null);setError('');setStocktakeError('');setLoading(true);
    Promise.allSettled([api.context(),loadInventoryStocktakeContext()]).then(([legacy,native])=>{
      if(!active)return;
      if(legacy.status==='fulfilled')setContext(legacy.value);
      else setError(messageFor(legacy.reason));
      if(native.status==='fulfilled'){
        if(native.value?.schema_version==='g47-stocktake-v1'&&native.value.capabilities&&typeof native.value.capabilities.view==='boolean')setStocktakeContext(native.value);
        else setStocktakeError('تعذر التحقق من صلاحيات الجرد والتسويات.');
      }else if(native.reason?.response?.status!==403)setStocktakeError(messageFor(native.reason));
      setLoading(false);
    });
    return () => { active = false; };
  }, [revision]);
  const permissions = context?.permissions || {};
  const countCapabilities=stocktakeContext?.capabilities||{};
  const tabs = [
    ...(permissions.reports ? [['home', 'الرئيسية'], ['stock', 'الأصناف والمخزون'], ['reports', 'التقارير'], ['purchases', 'فواتير شراء المخزون']] : []),
    ...(context?.can_manage_inventory_opening === true ? [['opening', 'إضافة المخزون']] : []),
    ...(permissions.move ? [['purchase', 'شراء مخزون']] : []),
    ...(countCapabilities.view===true ? [['stocktake','الجرد'],...((countCapabilities.review===true||countCapabilities.post===true)?[['adjustments','التسويات']]:[]),['movements','حركة المخزون']] : []),
  ];
  const requested = params.get('tab');
  const selected = requested || tabs[0]?.[0] || 'home';
  const allowed = tabs.some(([id]) => id === selected);
  const navigate = (value,item) => setParams(tabQuery(params,value,item));
  const frameActive = ({stock:'catalog',opening:'entry',purchase:'purchases',purchases:'purchases'})[selected] || selected;
  const titles={opening:['إضافة المخزون','سجّل الكميات الموجودة ومواصفاتها وتكلفتها وموقعها.'],purchase:['شراء مخزون','تسجيل شراء مستقل لبضاعة مخزنية للطلبات القادمة.'],purchases:['فواتير شراء المخزون','مراجعة فواتير المخزون المسجلة من المسار الحالي.']};
  const capabilityError=!allowed?(['stocktake','adjustments','movements'].includes(selected)?stocktakeError:error):'';
  return <WorkspaceFrame embedded={embedded} active={frameActive}>
    <main className="mz-stock-management" dir="rtl">
      {loading ? <div className="mz-stock-empty" role="status"><ArrowsClockwise size={30} className="animate-spin"/>جارٍ تحميل صلاحيات المخزون…</div> : <>
        {capabilityError&&<div role="alert" className="mz-stock-notice is-error"><WarningCircle size={22}/>{capabilityError}<button type="button" onClick={()=>setRevision(value=>value+1)}>إعادة المحاولة</button></div>}
        <nav className="mz-stock-local-tabs" aria-label="أقسام المخزون">{tabs.map(([id,title])=><button type="button" key={id} aria-current={id===selected?'page':undefined} onClick={()=>navigate(id)}>{title}</button>)}</nav>
        {!allowed&&<p role="alert" className="mz-stock-notice is-warning">هذه الصفحة غير متاحة ضمن صلاحيات المخزون الحالية.</p>}
        {allowed&&<div key={(context?.session_scope||'native-inventory')+':'+selected}>
          {['home','stock','reports'].includes(selected)&&<InventoryAvailability context={context} view={selected}
            initialSelectedId={params.get('item')||''} onNavigate={navigate}
            onOpeningInventory={context.can_manage_inventory_opening===true?()=>navigate('opening'):undefined}/>}
          {titles[selected]&&<InventoryPageHeader title={titles[selected][0]} description={titles[selected][1]}/>}
          {selected==='opening'&&<div className="mz-stock-native-form"><StandaloneInventoryEntry key={"opening:"+context.session_scope} context={context}/></div>}
          {selected==='purchase'&&<div className="mz-stock-native-form"><p className="mz-stock-notice">تسجّل الفاتورة التزام المورد؛ إثبات الاستلام بالموقع والتقييم عمليتان مستقلتان. هذه الصفحة لا تنشئ شراءً لطلب عميل.</p><InventoryPurchases key={"purchase:"+context.session_scope} context={context}/></div>}
          {selected==='purchases'&&<div className="mz-stock-native-form"><InventoryPurchases key={"purchase-reports:"+context.session_scope} context={context} mode="reports"/></div>}
          {['stocktake','adjustments'].includes(selected)&&<Suspense fallback={<div className="mz-stock-empty" role="status">جارٍ فتح مساحة الجرد…</div>}><StocktakeWorkbench mode={selected==='adjustments'?'adjustments':'stocktake'}/></Suspense>}
          {selected==='movements'&&<InventoryMovementsView allowed={countCapabilities.view===true} scope={stocktakeContext} loadMovements={loadInventoryStocktakeMovements}/>}
        </div>}
        {context&&<><details className="mz-stock-policy"><summary>ضوابط الكميات والاعتماد</summary><p>ربط هذه المرحلة بحجز وصرف طلبات العملاء مؤجل. إدخال الكميات لا يفعّل جاهزيتها تلقائيًا؛ تبقى ضوابط الاستلام والتقييم والاعتماد سارية.</p></details>
          <footer className="mz-stock-workspace-footer"><Link to="/operational-balances">الأرصدة التشغيلية</Link><Link to="/operational-balances/financial-position">المركز المالي التشغيلي</Link></footer></>}
      </>}
    </main>
  </WorkspaceFrame>;
}
