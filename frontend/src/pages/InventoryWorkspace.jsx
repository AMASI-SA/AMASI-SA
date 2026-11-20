import React, {useEffect, useState} from 'react';
import {Link, useSearchParams} from 'react-router-dom';
import {ArrowsClockwise, WarningCircle} from '@phosphor-icons/react';
import {operationalApi as api, messageFor} from './operational/api';
import InventoryAvailability from './operational/InventoryAvailability';
import StandaloneInventoryEntry from './operational/StandaloneInventoryEntry';
import InventoryPurchases from './operational/InventoryPurchases';
import InventorySuiteFrame, {InventoryPageHeader} from '../components/inventory/suite/InventorySuiteFrame';
import './operational/operational.css';
import '../components/inventory/suite/inventoryProjectionViews.css';

// A web catalog grant reads only the existing scoped warehouse projection.
// Management context and all inventory writers remain outside this lane.
export default function InventoryWorkspace({ accessMode = null }) {
  return accessMode ? <InventoryReadOnlyWorkspace/> : <InventoryManagement/>;
}

function InventoryReadOnlyWorkspace() {
  const [params,setParams]=useSearchParams();
  const view=params.get('tab')==='reports'?'reports':'stock';
  return <InventorySuiteFrame active={view==='reports'?'reports':'catalog'}>
    <InventoryAvailability context={{permissions:{reports:true},session_scope:'mezan-web-stock'}}
      view={view} onNavigate={value=>{if(['stock','reports'].includes(value))setParams({tab:value});}}/>
  </InventorySuiteFrame>;
}

function InventoryManagement() {
  const [params, setParams] = useSearchParams();
  const [context, setContext] = useState(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setContext(null); setError('');
    api.context().then(result => { if (active) setContext(result); })
      .catch(reason => { if (active) setError(messageFor(reason)); });
    return () => { active = false; };
  }, [revision]);
  const permissions = context?.permissions || {};
  const tabs = [
    ...(permissions.reports ? [['home', 'الرئيسية'], ['stock', 'الأصناف والمخزون'], ['reports', 'التقارير'], ['purchases', 'فواتير شراء المخزون']] : []),
    ...(context?.can_manage_inventory_opening === true ? [['opening', 'إضافة المخزون']] : []),
    ...(permissions.move ? [['purchase', 'شراء مخزون']] : []),
  ];
  const requested = params.get('tab');
  const selected = requested || tabs[0]?.[0] || 'home';
  const allowed = tabs.some(([id]) => id === selected);
  const navigate = (value,item) => setParams({tab:value,...(item?{item}:{})});
  const frameActive = ({stock:'catalog',opening:'entry',purchase:'purchases',purchases:'purchases'})[selected] || selected;
  const titles={opening:['إضافة المخزون','سجّل الكميات الموجودة ومواصفاتها وتكلفتها وموقعها.'],purchase:['شراء مخزون','تسجيل شراء مستقل لبضاعة مخزنية للطلبات القادمة.'],purchases:['فواتير شراء المخزون','مراجعة فواتير المخزون المسجلة من المسار الحالي.']};
  return <InventorySuiteFrame active={frameActive}>
    <main className="mz-stock-management" dir="rtl">
      {error ? <div role="alert" className="mz-stock-notice is-error"><WarningCircle size={22}/>{error}<button type="button" onClick={()=>setRevision(value=>value+1)}>إعادة المحاولة</button></div>
        : !context ? <div className="mz-stock-empty" role="status"><ArrowsClockwise size={30} className="animate-spin"/>جارٍ تحميل صلاحيات المخزون…</div>
        : <>
          <nav className="mz-stock-local-tabs" aria-label="أقسام المخزون">{tabs.map(([id,title])=><button type="button" key={id} aria-current={id===selected?'page':undefined} onClick={()=>navigate(id)}>{title}</button>)}</nav>
          {!allowed&&<p role="alert" className="mz-stock-notice is-warning">هذه الصفحة غير متاحة ضمن صلاحيات المخزون الحالية.</p>}
          {allowed&&<div key={context.session_scope+':'+selected}>
            {['home','stock','reports'].includes(selected)&&<InventoryAvailability context={context} view={selected}
              initialSelectedId={params.get('item')||''} onNavigate={navigate}
              onOpeningInventory={context.can_manage_inventory_opening===true?()=>navigate('opening'):undefined}/>}
            {titles[selected]&&<InventoryPageHeader title={titles[selected][0]} description={titles[selected][1]}/>}
            {selected==='opening'&&<div className="mz-stock-native-form"><StandaloneInventoryEntry context={context}/></div>}
            {selected==='purchase'&&<div className="mz-stock-native-form"><p className="mz-stock-notice">تسجّل الفاتورة التزام المورد؛ إثبات الاستلام بالموقع والتقييم عمليتان مستقلتان. هذه الصفحة لا تنشئ شراءً لطلب عميل.</p><InventoryPurchases context={context}/></div>}
            {selected==='purchases'&&<div className="mz-stock-native-form"><InventoryPurchases context={context} mode="reports"/></div>}
          </div>}
          <details className="mz-stock-policy"><summary>ضوابط الكميات والاعتماد</summary><p>ربط هذه المرحلة بحجز وصرف طلبات العملاء مؤجل. إدخال الكميات لا يفعّل جاهزيتها تلقائيًا؛ تبقى ضوابط الاستلام والتقييم والاعتماد سارية.</p></details>
          <footer className="mz-stock-workspace-footer"><Link to="/operational-balances">الأرصدة التشغيلية</Link><Link to="/operational-balances/financial-position">المركز المالي التشغيلي</Link></footer>
        </>}
    </main>
  </InventorySuiteFrame>;
}
