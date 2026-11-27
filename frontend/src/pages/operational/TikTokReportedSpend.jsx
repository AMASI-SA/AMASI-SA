import React, {useEffect, useState} from 'react';
import {operationalApi as api, messageFor} from './api';

const money = value => Number(value).toLocaleString('en-US', {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
});

export default function TikTokReportedSpend({enabled, refreshVersion}) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!enabled) return undefined;
    let active = true;
    setLoading(true);
    setError('');
    api.tiktokReportedSpend().then(value => {
      if (active) setResult(value);
    }).catch(failure => {
      if (active) setError(messageFor(failure));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [enabled, refreshVersion]);

  if (!enabled) return null;
  const rows = Array.isArray(result?.items) ? result.items : [];
  const fullyCovered = result?.coverage_complete === true;
  const total = fullyCovered ? result?.total_source_spend_sar : null;
  return <section className="op-card" aria-label="مراجعة صرف تيك توك لكل حساب" data-testid="operational-tiktok-reported-spend">
    <h3>تيك توك — صرف كل الحسابات الإعلانية</h3>
    <p className="op-muted">أرقام مصدرية للمراجعة فقط، وليست قيدًا ماليًا ولا تضاف مجددًا إلى الرصيد.
      يُعتمد كل حساب ماليًا عبر إعداد التمويل وإثبات الصرف في نظام الأرصدة التشغيلية.</p>
    {loading && <p role="status">جارٍ تحميل صرف حسابات تيك توك…</p>}
    {error && <p role="alert" className="op-error">تعذرت قراءة صرف حسابات تيك توك: {error}</p>}
    {result && <p className="op-muted">الفترة: {result.period?.from_date} إلى {result.period?.to_date} ·
      الحسابات: {rows.length} · إجمالي المصدر: <strong>{total == null ? 'غير مكتمل' : `${money(total)} ر.س`}</strong>
      {!fullyCovered && ` · الإجمالي المؤكد من الحسابات المكتملة فقط: ${money(result.known_subtotal_sar || 0)} ر.س`}
    </p>}
    {rows.length > 0 && <div className="op-table"><table>
      <thead><tr><th>حساب تيك توك</th><th>العملة</th><th>صرف المصدر بالريال</th><th>تغطية الأيام</th><th>الربط المالي</th></tr></thead>
      <tbody>{rows.map(item => <tr key={item.account_id}>
        <td>{item.name} <small dir="ltr">({item.account_id})</small></td>
        <td>{item.currency || 'غير محدد'}</td>
        <td>{item.source_spend_sar == null ? 'غير مكتمل' : `${money(item.source_spend_sar)} ر.س`}</td>
        <td>{item.covered_days}/{item.expected_days}</td>
        <td>{item.binding_state === 'approved' ? 'ربط معتمد' : item.binding_state === 'ambiguous' ? 'تعارض روابط — مراجعة' : 'يحتاج اعتماد الربط المالي'}</td>
      </tr>)}</tbody>
    </table></div>}
    {result && !rows.length && <p>لا توجد حسابات TikTok مرتبطة في مصدر ميزان الحالي.</p>}
  </section>;
}
