import React from 'react';
import { Amount, Icon } from './ApprovedFinanceUI';
import { formatRiyadhDateTime } from '../../lib/dates';
import './carrierAccountSummary.css';

const money = (value, currency) => <Amount value={value} currency={currency}/>;
const date = value => typeof value === 'string' && value && Number.isFinite(Date.parse(value))
  ? formatRiyadhDateTime(value) : 'غير متاح';

// The backend owns this snapshot. Never derive account totals from the current
// page of ledger entries, nor assume an unavailable allocation is zero.
export default function CarrierAccountSummary({ summary, resource }) {
  if (resource?.kind !== 'courier' || !resource.access?.balance || resource.access?.movements !== 'all') return null;
  if (!summary) return <p className="note">تفصيل تحصيلات الشركة وأجور الشحن غير متاح في هذه القراءة. لا يُستنتج من الحركات الظاهرة وحدها.</p>;
  if (summary.scope !== 'saved_snapshot_to_date' || summary.currency !== resource.currency) return <p className="note warning" role="alert">تعذر التحقق من نطاق تفصيل حساب شركة الشحن؛ أعد قراءة الكشف.</p>;
  return <section className="panel sheet carrier-summary" aria-label="تحصيلات الشركة وأجور الشحن">
    <div className="section-head"><h2>تحصيلات الشركة وأجور الشحن</h2><Icon name="truck"/></div>
    <p className="subtitle">من بداية الأرصدة إلى آخر احتساب: {date(summary.as_of)}. هذا ملخص الحساب كاملًا، مستقل عن تصفية الفترة والحركات أدناه.</p>
    {!summary.complete && <p className="note warning">بعض الإثباتات أو توزيعات السداد تحتاج مراجعة. المبلغ غير المتاح ليس صفرًا، ولا يُخصم تلقائيًا من التحصيل.</p>}
    <div className="metrics">
      <div className="metric"><small>{summary.complete ? 'إجمالي ما لنا لدى الشركة' : 'المثبت لنا لدى الشركة'}</small><strong>{money(summary.receivable, summary.currency)}</strong></div>
      <div className="metric"><small>{summary.complete ? 'إجمالي ما علينا للشركة' : 'المثبت علينا للشركة'}</small><strong>{money(summary.payable, summary.currency)}</strong></div>
      <div className="metric"><small>فرق الحقوق والالتزامات</small><strong>{money(summary.net_position, summary.currency)}</strong><small>مؤشر عرض؛ لا ينشئ تسوية بين المبلغين.</small></div>
    </div>
    <div className="scroll"><table className="ledger"><caption>أجور الشحن المثبتة والسداد</caption><thead><tr><th>البند</th><th>المبلغ</th></tr></thead><tbody>
      <tr><th scope="row">أجور الشحن الصادر المثبتة</th><td>{money(summary.shipping_confirmed, summary.currency)}</td></tr>
      <tr><th scope="row">أجور بوليصات الراجع المثبتة</th><td>{money(summary.return_confirmed, summary.currency)}</td></tr>
      <tr><th scope="row">مجموع أجور الشحن المثبتة</th><td>{money(summary.fees_confirmed, summary.currency)}</td></tr>
      <tr><th scope="row">المسدد والموزع على الأجور</th><td>{money(summary.fees_paid, summary.currency)}</td></tr>
      <tr><th scope="row">الأجور المتبقية بعد التوزيع</th><td>{money(summary.fees_outstanding, summary.currency)}</td></tr>
    </tbody></table></div>
    <p className="note">الأجرة المسددة من محفظة الشحن لا تُسدد مرة ثانية. أجور بوليصات الراجع تكلفة شحن، وليست مبلغًا مردودًا للعميل أو رصيدًا مستردًا من الشركة.</p>
    <div className="scroll"><table className="ledger"><caption>تحصيلات الدفع عند الاستلام</caption><thead><tr><th>البند</th><th>المبلغ</th></tr></thead><tbody>
      <tr><th scope="row">مبالغ الدفع عند الاستلام المثبتة لنا لدى الشركة</th><td>{money(summary.cod_confirmed, summary.currency)}</td></tr>
      <tr><th scope="row">ما جمعته الشركة من العملاء بدليل تحصيل</th><td>{money(summary.customer_collection_confirmed, summary.currency)}</td></tr>
      <tr><th scope="row">تحصيل مستلم وموزع على الطلبات</th><td>{money(summary.cod_received, summary.currency)}</td></tr>
      <tr><th scope="row">تحصيل مثبت متبقٍ لدى الشركة</th><td>{money(summary.cod_outstanding, summary.currency)}</td></tr>
    </tbody></table></div>
    <p className="note">إسناد المبلغ للشركة لا يثبت أنها قبضته من العميل. دليل التحصيل من العميل مستقل عن مسؤولية الشركة وعن وصول الحوالة إلى حسابك.</p>
    <details className="carrier-summary-more"><summary>تفصيل الحركات النقدية والافتتاحي</summary><div className="scroll"><table className="ledger"><caption>الحركات النقدية والافتتاحي</caption><thead><tr><th>البند</th><th>المبلغ</th></tr></thead><tbody>
      <tr><th scope="row">وصل فعليًا إلى البنك أو الصندوق</th><td>{money(summary.cash_received, summary.currency)}</td></tr>
      <tr><th scope="row">خرج فعليًا من البنك أو الصندوق</th><td>{money(summary.cash_paid, summary.currency)}</td></tr>
      <tr><th scope="row">مستلم غير موزع على التزامات محددة</th><td>{money(summary.unallocated_received, summary.currency)}</td></tr>
      <tr><th scope="row">مدفوع غير موزع على التزامات محددة</th><td>{money(summary.unallocated_paid, summary.currency)}</td></tr>
      <tr><th scope="row">افتتاحي لنا</th><td>{money(summary.opening_receivable, summary.currency)}</td></tr>
      <tr><th scope="row">افتتاحي علينا</th><td>{money(summary.opening_payable, summary.currency)}</td></tr>
      <tr><th scope="row">حقوق أخرى متبقية دون تصنيف تحصيل</th><td>{money(summary.other_receivable, summary.currency)}</td></tr>
      <tr><th scope="row">التزامات أخرى متبقية دون تصنيف شحن</th><td>{money(summary.other_payable, summary.currency)}</td></tr>
    </tbody></table></div></details>
    <div className="metrics">
      <div className="metric"><small>تحصيلات متوقعة غير مثبتة</small><strong>{money(summary.expected_cod, summary.currency)}</strong><small>متابعة منفصلة؛ ليست نقدًا مستلمًا.</small></div>
      <div className="metric"><small>أجور شحن صادر متوقعة غير مثبتة</small><strong>{money(summary.expected_shipping, summary.currency)}</strong><small>لا تُضاف إلى الأجور المستحقة أعلاه.</small></div>
      <div className="metric"><small>أجور راجع متوقعة غير مثبتة</small><strong>{money(summary.expected_return, summary.currency)}</strong><small>لا تُضاف إلى الأجور المستحقة أعلاه.</small></div>
    </div>
    <p className="note">التوقعات هنا من الالتزامات المحفوظة والمسندة لهذه الشركة فقط، ولا تمثل كل طلبات المتجر المستقبلية.</p>
    <p className="note">الحوالة المستلمة تُسجل بقيمتها الفعلية. خصم أجور من تحصيلات الشركة يحتاج تسوية موثقة؛ عرض المبلغين لا ينشئ مقاصة أو حركة بنك.</p>
  </section>;
}
