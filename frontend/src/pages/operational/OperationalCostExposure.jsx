import React, { useEffect, useState } from 'react';
import { operationalApi as api, messageFor } from './api';
import PaginatedReportTable from './PaginatedReportTable';

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
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(String(amount));
  return match ? BigInt(match[1]) * 100n + BigInt((match[2] || '').padEnd(2, '0')) : null;
};
const total = (data, keys, partial) => {
  const supplied = keys.filter(key => Object.prototype.hasOwnProperty.call(data, key));
  if (!supplied.length && partial) return null;
  const parsed = supplied.map(key => cents(data[key]));
  return parsed.some(value => value === null) ? null : parsed.reduce((sum, value) => sum + value, 0n);
};
const fmt = amount => amount === null ? 'غير مكتمل' :
  (amount / 100n).toLocaleString('en-US') + '.' + (amount % 100n).toString().padStart(2, '0');
const value = (data, key, partial) => Object.prototype.hasOwnProperty.call(data, key) ?
  fmt(cents(data[key])) : partial ? 'غير مكتمل' : '0.00';

export default function OperationalCostExposure({enabled = true, scopeKey = 'default'}) {
  const [state, setState] = useState({loading: true, data: null, error: '', scopeKey});
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    setState({loading: true, data: null, error: '', scopeKey});
    Promise.resolve().then(() => api.costExposure()).then(data => {
      if (live) setState({loading: false, data, error: '', scopeKey});
    }).catch(error => {
      if (live) setState({loading: false, data: null, error: messageFor(error), scopeKey});
    });
    return () => { live = false; };
  }, [enabled, scopeKey, revision]);
  if (!enabled) return null;
  const current = state.scopeKey === scopeKey ? state : {loading: true, data: null, error: ''};
  const {loading, data, error} = current;
  const entries = Object.entries(data?.currencies || {});
  const gaps = data?.issues || [];
  const incomplete = gaps.filter(item => item.code !== 'historical_current_catalog_estimate_used');
  const partial = data?.complete === false || incomplete.length > 0;
  const catalogEstimates = gaps.find(item => item.code === 'historical_current_catalog_estimate_used')?.count || 0;
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
    {!loading && !error && data?.status === 'frozen' && <p>النظام مجمد؛ لا نعرض بيانات التكاليف الحالية على أنها جزء من لقطة الإغلاق المحفوظة.</p>}
    {!loading && !error && data?.status === 'preview' && <>
      <p className="op-muted">آخر قراءة للطلبات الجديدة: {data.engine_as_of ? new Date(data.engine_as_of).toLocaleString('ar-SA') : 'غير مكتمل'} · معاينة القديمة: {data.as_of ? new Date(data.as_of).toLocaleString('ar-SA') : 'غير مكتمل'}</p>
      {!entries.length && <p>لا توجد تكاليف مكتملة المصدر للعرض. افحص التنبيهات أدناه؛ عدم اكتمال التكلفة لا يعني أن قيمتها صفر.</p>}
      {entries.map(([currency, amounts]) => {
        const unreceived = total(amounts, ['prestart_supplier_unreceived', 'poststart_supplier_unreceived'], partial);
        const received = total(amounts, ['prestart_supplier_received_uninvoiced', 'poststart_supplier_received_uninvoiced'], partial);
        const shipping = total(amounts, ['prestart_shipping_expected', 'current_shipping_expected'], partial);
        const totalOpen = [unreceived, received, shipping].some(value => value === null) ? null : unreceived + received + shipping;
        return <div className="op-cost-currency" key={currency}>
          <h4>التكاليف بعملة {currency}{partial ? ' — المعروف من التكاليف، وقد يكون جزئيًا' : ''}</h4>
          <div className="op-cost-cards">
            <article><span>منتجات لم تُستلم من المورد — قديمة وجديدة</span><strong>{fmt(unreceived)} {currency}</strong>
              <small>تشمل المسندة للمورد ما دامت لم تُستلم فعليًا</small></article>
            <article><span>منتجات استُلمت دون فاتورة معتمدة</span><strong>{fmt(received)} {currency}</strong>
              <small>تُعرض باسم المورد فقط بعد إثبات الاستلام</small></article>
            <article><span>تكاليف شحن لم يتم توصيله</span><strong>{fmt(shipping)} {currency}</strong>
              <small>تقدير بحسب عقد شركة الشحن أو المندوب المعتمد</small></article>
            <article><span>إجمالي التكاليف التشغيلية المفتوحة</span><strong>{fmt(totalOpen)} {currency}</strong>
              <small>منتجات لم تُستلم + استُلمت دون فاتورة + شحنات لم تُسلّم. هذا إجمالي تقديري وليس دينًا مؤكدًا.</small></article>
          </div>
          <p className="op-muted">المنتجات التي لم تُستلم: من الطلبات القديمة {value(amounts, 'prestart_supplier_unreceived', partial)} {currency}، ومن الطلبات الجديدة {value(amounts, 'poststart_supplier_unreceived', partial)} {currency}.</p>
        </div>;
      })}
      {catalogEstimates > 0 && <p role="status" className="op-cost-caution">توجد {catalogEstimates} تكلفة لطلب قديم قُدرت اعتمادًا على سعر التكلفة الحالي في كتالوج سلة، وليس سعرًا تاريخيًا معتمدًا وقت الطلب؛ تحقق منها قبل اعتماد قرار مالي.</p>}
      {partial && <p role="status" className="op-cost-caution">يوجد {incomplete.reduce((n, item) => n + Number(item.count || 0), 0).toLocaleString('en-US')} تنبيهًا عن بيانات تكلفة أو هوية ناقصة؛ لذلك قد يكون الإجمالي جزئيًا.</p>}
      <details className="op-cost-detail"><summary>تفصيل التكاليف حسب الحالة والجهة ({(data.groups || []).length} صف)</summary>
        <PaginatedReportTable rows={data.groups || []} label="تفصيل التكاليف حسب الحالة والجهة" resetKey={scopeKey}>{rows => <div className="op-table"><table>
          <thead><tr><th>الفترة</th><th>النوع</th><th>الحالة</th><th>الجهة / التجميع</th><th>القطع أو البنود</th><th>الطلبات</th><th>التكلفة</th></tr></thead>
          <tbody>{rows.map((item, i) => <tr key={[i, item.period, item.kind, item.stage, item.party_id || item.party_name].join(':')}>
            <td>{periods[item.period] || item.period}</td><td>{kinds[item.kind] || item.kind}</td>
            <td>{stages[item.stage] || item.stage}</td><td>{item.party_name}</td><td>{item.units}</td><td>{item.orders}</td>
            <td>{item.amount} {item.currency}</td></tr>)}</tbody>
        </table>{!(data.groups || []).length && <p>لا توجد بنود تكلفة مكتملة ضمن النطاق الحالي.</p>}</div>}</PaginatedReportTable>
      </details>
      <details className="op-cost-detail"><summary>تنبيهات جودة التكاليف ({gaps.reduce((n, item) => n + Number(item.count || 0), 0)})</summary>
        {gaps.map(item => <p key={item.code}>{item.code}: {item.count}</p>)}
        {!gaps.length && <p>لا توجد تنبيهات في معاينة التكاليف.</p>}
      </details>
      <p className="op-muted">هذه معاينة تشغيلية فقط؛ لا تُضاف تكاليف الطلبات القديمة تلقائيًا إلى «المتبقي علينا» أو الأرصدة الافتتاحية، منعًا لتكرار فواتير أو دفعات سابقة.</p>
    </>}
  </section>;
}
