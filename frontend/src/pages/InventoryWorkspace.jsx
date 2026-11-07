import React, {useEffect, useState} from 'react';
import {Link, useSearchParams} from 'react-router-dom';
import {operationalApi as api, messageFor} from './operational/api';
import InventoryAvailability from './operational/InventoryAvailability';
import StandaloneInventoryEntry from './operational/StandaloneInventoryEntry';
import InventoryPurchases from './operational/InventoryPurchases';
import './operational/operational.css';

// This workspace reuses existing inventory permissions and data. It never
// activates writers, reserves order units or invokes fulfillment transitions.
export default function InventoryWorkspace() {
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
    ...(permissions.reports ? [['stock', 'المخزون الحالي'], ['purchases', 'فواتير شراء المخزون']] : []),
    ...(context?.can_manage_inventory_opening === true ? [['opening', 'إضافة المخزون']] : []),
    ...(permissions.move ? [['purchase', 'شراء مخزون']] : []),
  ];
  const requested = params.get('tab');
  const selected = requested || tabs[0]?.[0];
  const allowed = tabs.some(([id]) => id === selected);
  const navigate = value => setParams({tab: value});
  return <main className="op-page" dir="rtl">
    <header><span className="op-eyebrow">ميزان · المنتجات</span><h1>المخزون</h1>
      <p>بضاعة مخزنية للطلبات المستقبلية، مستقلة عن مشتريات تجهيز طلبات العملاء.</p>
      <p role="note">ربط هذه المرحلة بحجز وصرف طلبات العملاء مؤجل. إدخال الكميات لا يفعّل جاهزيتها تلقائيًا؛ تبقى ضوابط الاستلام والتقييم والاعتماد سارية.</p>
    </header>
    {error ? <div role="alert">{error}<button onClick={() => setRevision(v => v + 1)}>إعادة المحاولة</button></div>
      : !context ? <p role="status">جارٍ تحميل صلاحيات المخزون…</p>
      : <>
        <nav aria-label="صفحات المخزون">{tabs.map(([id, title]) => <button key={id}
          aria-current={id === selected ? 'page' : undefined} onClick={() => navigate(id)}>{title}</button>)}</nav>
        {!allowed && <p role="alert">هذه الصفحة غير متاحة ضمن صلاحيات المخزون الحالية.</p>}
        {allowed && <div key={`${context.session_scope}:${selected}`}>
          {selected === 'stock' && <InventoryAvailability context={context}
            onOpeningInventory={context.can_manage_inventory_opening === true ? () => navigate('opening') : undefined} />}
          {selected === 'opening' && <StandaloneInventoryEntry context={context} />}
          {selected === 'purchase' && <><p>تسجّل الفاتورة التزام المورد؛ إثبات الاستلام بالموقع والتقييم عمليتان مستقلتان. هذه الصفحة لا تنشئ شراءً لطلب عميل.</p><InventoryPurchases context={context} /></>}
          {selected === 'purchases' && <InventoryPurchases context={context} mode="reports" />}
        </div>}
        <footer><Link to="/operational-balances">الأرصدة التشغيلية</Link>{' · '}
          <Link to="/operational-balances/financial-position">المركز المالي التشغيلي</Link></footer>
      </>}
  </main>;
}
