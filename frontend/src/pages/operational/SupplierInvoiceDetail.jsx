import React from 'react';
import { Amount } from './ApprovedFinanceUI';
import { simpleBalancesApi } from './simpleBalancesApi';
import { useBalanceRead } from './simpleBalancesHooks';

export default function SupplierInvoiceDetail({ row, scope }) {
  const read = useBalanceRead(config => simpleBalancesApi.supplierInvoice(row.party_id, row.invoice_id, config),
    JSON.stringify([scope, row.party_id, row.invoice_id]));
  if (read.error) return <p role="alert">{read.error} <button type="button" onClick={read.reload}>إعادة المحاولة</button></p>;
  const invoice = read.data;
  if (!invoice || invoice.id !== row.invoice_id) return <p role="status">جارٍ تحميل بنود الفاتورة…</p>;
  return <section><h3>بنود الفاتورة</h3><div className="scroll"><table className="ledger"><thead><tr>
    {['المنتج أو الخدمة', 'الكمية', 'سعر الوحدة', 'قيمة البند'].map(title => <th key={title}>{title}</th>)}
  </tr></thead><tbody>{invoice.lines.map((line, i) => <tr key={i}><td>{line.name || 'منتج'}{line.kind && <small className="under">{line.kind==='service'?'خدمة':'منتج'}</small>}
    {line.services.map((service, j) => <small className="under" key={j}>{service.name || 'خدمة'} · <Amount value={service.total} currency={invoice.currency}/></small>)}</td>
    <td>{line.quantity}</td><td><Amount value={line.unit} currency={invoice.currency}/></td>
    <td><Amount value={line.total} currency={invoice.currency}/></td></tr>)}</tbody></table></div>
    {invoice.supplier_reference && <p>رقم فاتورة المورد · {invoice.supplier_reference}</p>}
    {invoice.note && <p>ملاحظة · {invoice.note}</p>}
    {invoice.tax != null && <p>الضريبة <Amount value={invoice.tax} currency={invoice.currency}/></p>}
    <p><strong>إجمالي الفاتورة <Amount value={invoice.total} currency={invoice.currency}/></strong></p></section>;
}
