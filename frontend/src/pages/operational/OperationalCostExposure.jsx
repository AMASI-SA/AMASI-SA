import React, { useEffect, useState } from 'react';
import { operationalApi as api, messageFor } from './api';

const periods = {prestart: 'طلبات سابقة لبدء التشغيل', poststart: 'طلبات بعد بدء التشغيل'};
const stages = {
  awaiting_review: 'انتظار المراجعة — لم تُستلم',
  under_fulfillment: 'قيد التنفيذ — لم تُستلم',
  unreceived: 'لم تُستلم من المورد',
  received_without_approved_invoice: 'استُلمت من المورد — الفاتورة غير معتمدة',
  not_delivered: 'شحنة لم يتم توصيلها',
  confirmed_in_financial_balances: 'تكلفة مؤكدة في الأرصدة المالية'
};
const kinds = {supplier: 'المنتجات', shipping: 'الشحن', courier: 'شركات الشحن', store_driver: 'المناديب'};
const cents = amount => {
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(String(amount ?? '0'));
  return m ? BigInt(m[1]) * 100n + BigInt((m[2] || '').padEnd(2, '0')) : 0n;
};
const sum = (...values) => values.reduce((total, amount) => total + cents(amount), 0n);
const fmt = total => (total / 100n).toLocaleString('en-US') + '.' + (total % 100n).toString().padStart(2, '0');
const value = (data, key) => data?.[key] || '0.00';

export default function OperationalCostExposure({enabled = true}) {
  const [state, setState] = useState({loading: true, data: null, error: ''});
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    setState(old => ({...old, loading: true, error: ''}));
    api.costExposure().then(data => {
      if (live) setState({loading: false, data, error: ''});
    }).catch(error => {
      if (live) setState(old => ({...old, loading: false, error: messageFor(error)}));
    });
    return () => { live = false; };
  }, [enabled, revision]);
  if (!enabled) return null;
  const {loading, data, error} = state;
  const entries = Object.entries(data?.currencies || {});
  const gaps = data?.issues || [];
  const incomplete = gaps.filter(item => /incomplete|ambiguous|missing|unverified|requires_review/.test(item.code || ''));
  return <section className="op-cost-exposure" aria-label="تكاليف المنتجات والشحن المفتوحة">
    <div className="op-cost-header">
      <div>
        <h3>تكاليف المنتجات والشحن المفتوحة</h3>
        <p className="op-muted">تشمل الطلبات القديمة التي بقيت مفتوحة عند بدء التشغيل والطلبات الجديدة، دون إدخال دين افتتاحي جديد.</p>
      </div>
      <button type="button" disabled={loading} onClick={() => setRevision(old => old + 1)}>تحديث التكاليف</button>
    </div>
    {loading && <p role="status">جاري قراءة التكاليف المفتوحة…</p>}
    {error && <p role="alert" className="op-error">{error}</p>}
    {!loading && !error && data?.status === 'not_started' && <p>يبدأ احتساب التكاليف بعد تفعيل الأرصدة التشغيلية.</p>}
    {!loading && !error && data?.status === 'preview' && <>
      <p className="op-muted">آخر قراءة للطلبات الجديدة: {data.engine_as_of ? new Date(data.engine_as_of).toLocaleString('ar-SA') : 'غير مكتمل'} · معاينة القديمة: {data.as_of ? new Date(data.as_of).toLocaleString('ar-SA') : 'غير مكتمل'}</p>
      {!entries.length && <p>لا توجد تكاليف مكتملة المصدر للعرض. افحص التنبيهات أدناه؛ عدم اكتمال التكلفة لا يعني أن قيمتها صفر.</p>}
      {entries.map(([currency, amounts]) => {
        const unreceived = sum(value(amounts, 'prestart_supplier_unreceived'), value(amounts, 'poststart_supplier_unreceived'));
        const received = sum(value(amounts, 'prestart_supplier_received_uninvoiced'), value(amounts, 'poststart_supplier_received_uninvoiced'));
        const shipping = sum(value(amounts, 'prestart_shipping_expected'), value(amounts, 'current_shipping_expected'));
        return <div className="op-cost-currency" key={currency}>
          <h4>التكاليف بعملة {currency}</h4>
          <div className="op-cost-cards">
            <article><span>منتجات لم تُستلم من المورد — قديمة وجديدة</span><strong>{fmt(unreceived)} {currency}</strong>
              <small>تشمل المسندة للمورد ما دامت لم تُستلم فعليًا</small></article>
            <article><span>منتجات استُلمت دون فاتورة معتمدة</span><strong>{fmt(received)} {currency}</strong>
              <small>تُعرض باسم المورد فقط بعد إثبات الاستلام</small></article>
            <article><span>تكاليف شحن لم يتم توصيله</span><strong>{fmt(shipping)} {currency}</strong>
              <small>تقدير بحسب عقد شركة الشحن أو المندوب المعتمد</small></article>
          </div>
          <p className="op-muted">المنتجات التي لم تُستلم: من الطلبات القديمة {value(amounts, 'prestart_supplier_unreceived')} {currency}، ومن الطلبات الجديدة {value(amounts, 'poststart_supplier_unreceived')} {currency}.</p>
        </div>;
      })}
      {!!incomplete.length && <p role="status" className="op-cost-caution">يوجد {incomplete.reduce((n, x) => n + Number(x.count || 0), 0).toLocaleString('en-US')} تنبيهًا عن بيانات تكلفة أو هوية ناقصة؛ لذلك قد يكون الإجمالي جزئيًا.</p>}
      <details className="op-cost-detail"><summary>تفصيل التكاليف حسب الحالة والجهة ({(data.groups || []).length} صف)</summary>
        <div className="op-table"><table>
          <thead><tr><th>الفترة</th><th>النوع</th><th>الحالة</th><th>الجهة / التجميع</th><th>القطع أو البنود</th><th>الطلبات</th><th>التكلفة</th></tr></thead>
          <tbody>{(data.groups || []).map((item, i) => <tr key={[i, item.period, item.kind, item.stage, item.party_name].join(':')}>
            <td>{periods[item.period] || item.period}</td><td>{kinds[item.kind] || item.kind}</td>
            <td>{stages[item.stage] || item.stage}</td><td>{item.party_name}</td><td>{item.units}</td><td>{item.orders}</td>
            <td>{item.amount} {item.currency}</td></tr>)}</tbody>
        </table>{!(data.groups || []).length && <p>لا توجد بنود تكلفة مكتملة ضمن النطاق الحالي.</p>}</div>
      </details>
      <details className="op-cost-detail"><summary>تنبيهات جودة التكاليف ({gaps.reduce((n, x) => n + Number(x.count || 0), 0)})</summary>
        {gaps.map(item => <p key={item.code}>{item.code}: {item.count}</p>)}
        {!gaps.length && <p>لا توجد تنبيهات في معاينة التكاليف.</p>}
      </details>
      <p className="op-muted">هذه معاينة تشغيلية فقط؛ لا تُضاف تكاليف الطلبات القديمة تلقائيًا إلى «المتبقي علينا» أو الأرصدة الافتتاحية، منعًا لتكرار فواتير أو دفعات سابقة.</p>
    </>}
  </section>;
}
