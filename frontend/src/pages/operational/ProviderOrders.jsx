import React from 'react';
import { Amount } from './ApprovedFinanceUI';
import { minorUnits, movementDate } from './simpleBalancesModel';

const feeLabels = { actual: 'فعلية', actual_settlement: 'فعلية', mixed: 'فعلية وتقديرية', plan_estimate: 'تقديرية', average_estimate: 'تقديرية' };

function Value({ amount, currency }) {
  return amount == null ? <span aria-label="المبلغ غير متاح">—</span> : <Amount value={amount} currency={currency} />;
}

export default function ProviderOrders({ result, name, canOpenOrderDetails }) {
  const asOf = result.resource?.provider_position?.as_of;
  return <>
    {asOf && <p className="under">آخر احتساب محفوظ · <bdi>{movementDate({recorded_at: asOf}, true)}</bdi></p>}
    {result.amounts?.length > 0 && <div className="panel sheet"><div className="section-head"><h3>من أين جاء رصيد {name}؟</h3><small>من بداية الأرصدة حتى آخر احتساب</small></div>
      <div className="scroll"><table className="ledger"><thead><tr>{['العملة', 'الافتتاحي', 'دفع العملاء', 'الاستردادات', 'العمولات', 'صافي التسويات', 'المتبقي لنا'].map(label =>
        <th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>{result.amounts.map(row =>
          <tr key={row.currency}><th scope="row">{row.currency}</th>
            {[row.opening, row.gross ?? row.known_gross, row.refunded, row.fees ?? row.known_fees, row.settled, row.net ?? row.known_net].map((value, i) =>
              <td key={i}><Value amount={value} currency={row.currency}/>{(i === 1 && row.gross == null || i === 3 && row.fees == null || i === 5 && row.net == null) && value != null && <small className="under">الجزء المعلوم</small>}</td>)}
          </tr>)}</tbody></table></div></div>}
    {result.amounts?.filter(row=>minorUnits(row.uncomputed_principal)>0n || row.uncomputed_principal_complete===false).map(row=>
      <div className="note warning" key={'pending-'+row.currency}>
        <strong>قيمة الطلبات غير المحتسبة قبل العمولة: <Value amount={row.uncomputed_principal} currency={row.currency}/></strong>
        {Number.isInteger(row.uncomputed_orders) && <span> · {row.uncomputed_orders} طلب</span>}
        {row.uncomputed_principal_complete===false && <span> · القيمة المعروفة فقط</span>}
        <p>غير مشمولة في الصافي المحتسب. راجع أسباب التعليق في الطلبات أدناه.</p>
      </div>)}
    {result.items.length > 0 ? <div className="scroll"><table className="ledger">
      <thead><tr>{['الطلب', 'دفع العميل', 'سُوّي من المستحق', 'عمولة الجزء المتبقي', 'صافي متبقٍ', 'الحالة'].map(label =>
        <th scope="col" key={label}>{label}</th>)}</tr></thead>
      <tbody>{result.items.map(row => <tr key={row.id}>
        <td>{row.order_number && canOpenOrderDetails ? <a className="textbtn" href={'/orders-v2/' + encodeURIComponent(row.order_number)}>طلب <bdi>{row.order_number}</bdi></a> :
          <span>{row.order_number ? 'طلب' : 'مرجع سلة'} <bdi>{row.order_number || row.id}</bdi></span>}
          <small className="under"><bdi>{row.day || 'تاريخ غير متاح'}</bdi></small>{row.reference && <small className="under"><bdi>{row.reference}</bdi></small>}</td>
        <td><Value amount={row.paid} currency={row.currency}/>
          {row.currency && row.currency !== 'SAR' && row.paid_sar != null && <small className="under"><Amount value={row.paid_sar} currency="SAR"/></small>}</td>
        <td><Value amount={row.settled} currency={row.balance_currency}/></td>
        <td><Value amount={row.remaining_fee} currency={row.balance_currency}/>{row.remaining_fee != null && <small className="under">{feeLabels[row.fee_basis]}</small>}</td>
        <td><Value amount={row.net} currency={row.balance_currency}/></td>
        <td>{row.reasons?.length ? <details><summary>يحتاج مراجعة</summary>{row.reasons.map(reason=><p key={reason}>{reason}</p>)}</details> :
          row.net == null ? 'بانتظار المطابقة' : 'محتسب'}</td></tr>)}</tbody>
    </table></div> : <p className="empty">لا توجد طلبات مطابقة في آخر احتساب محفوظ.</p>}
    {result.undated > 0 && <p className="note">طلبات بلا تاريخ محفوظ خارج فلتر الفترة ({result.undated}). اختر كل التواريخ لعرضها.</p>}
  </>;
}
