import React from 'react';

export default function CustomerPaymentTotals({report}) {
  const tenderCoverage = report.details?.customer_tender_coverage;
  const sar = tenderCoverage?.sar;
  const paidTotals = Object.entries(report.details?.provider_paid_totals || {});
  if (!tenderCoverage && !paidTotals.length) return null;
  return <section className="op-card" aria-label="مدفوعات الطلبات منذ بداية التشغيل">
      <h2>مدفوعات الطلبات منذ بداية التشغيل</h2>
      {tenderCoverage && <p>عدد طلبات سلة في الفترة: {tenderCoverage.source_order_count}</p>}
      {tenderCoverage?.uninspected_source_orders > 0 && <p className="op-muted">طلبات لم يكتمل فحص بيانات الدفع فيها: {tenderCoverage.uninspected_source_orders}</p>}
      {tenderCoverage?.unknown_currency_orders > 0 && <p className="op-muted">طلبات تحتاج إلى تحديد العملة: {tenderCoverage.unknown_currency_orders}</p>}
      {sar ? <>
        <h3>{sar.paid == null ? 'المدفوع المثبت حتى الآن' : 'إجمالي ما دفعه العملاء'} · {sar.paid ?? sar.known_paid ?? 'غير متاح'} ر.س.</h3>
        {sar.missing_orders > 0 && <p role="status">طلبات تحتاج إلى إثبات قيمة الدفع بالريال: {sar.missing_orders}</p>}
        {sar.foreign_order_fx_count > 0 && <p className="op-muted">استُخدم سعر الصرف المحفوظ في طلب سلة لعدد {sar.foreign_order_fx_count} من الطلبات حتى تكتمل مطابقة مبلغ الدفعة بالريال.</p>}
        <div className="op-table"><table><caption>مدفوعات طرق الدفع بالريال السعودي</caption>
          <thead><tr><th>طريقة الدفع</th><th>الطلبات</th><th>دفعه العملاء (ر.س.)</th><th>متبقٍ على العملاء (ر.س.)</th><th>التغطية</th></tr></thead>
          <tbody>{sar.by_method.map(row => <tr key={row.method}>
            <td>{row.name}</td><td>{row.orders}</td><td>{row.paid ?? row.known_paid ?? 'غير متاح'}</td>
            <td>{row.remaining ?? 'غير متاح'}</td><td>{row.paid == null ? 'جزئي' : 'مكتمل'}</td>
          </tr>)}</tbody>
        </table></div>
      </> : <p className="op-muted">ينتظر إجمالي المدفوعات بالريال احتساب لقطة تشمل مبالغ الدفع المحوّلة.</p>}
      <details><summary>تفصيل الإثبات بعملة الطلب الأصلية</summary>
      {paidTotals.map(([currency, rows]) => <div key={currency}>
        <h3>{currency}</h3>
        {rows.map(row => <p key={row.provider}>{row.name} · {row.paid} {currency} · عدد الطلبات المدفوعة {row.orders}</p>)}
      </div>)}
      {Object.entries(tenderCoverage?.by_currency || {}).map(([currency, rows]) => <div className="op-table" key={currency}>
        <table><caption>طرق الدفع الأخرى · {currency}</caption>
          <thead><tr><th>طريقة الدفع</th><th>الطلبات</th><th>المدفوع المثبت حسب سلة</th><th>المتبقي المثبت على العملاء</th><th>طلبات تحتاج مطابقة</th></tr></thead>
          <tbody>{rows.map(row => <tr key={row.method}>
            <td>{row.name}</td><td>{row.orders}</td><td>{row.paid ?? 'غير متاح'}</td>
            <td>{row.remaining ?? 'غير متاح'}</td><td>{row.incomplete_orders}</td>
          </tr>)}</tbody>
        </table>
      </div>)}
      </details>
      <p className="op-muted">يظهر كل طلب مرة في عدد طلبات الفترة. عند تقسيم الدفع، تُحتسب حصة كل طريقة وحدها. وتشمل مدفوعات العملاء المبالغ التي تنتظر التحويل إلى البنك.</p>
    </section>;
}
