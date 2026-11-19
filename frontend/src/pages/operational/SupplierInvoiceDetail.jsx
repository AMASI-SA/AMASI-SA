import React, { useState } from 'react';
import { Amount } from './ApprovedFinanceUI';
import { simpleBalancesApi } from './simpleBalancesApi';
import { useBalanceRead } from './simpleBalancesHooks';

function ProductIdentity({ line }) {
  const [failedImage, setFailedImage] = useState(null);
  if (line.kind) return <div>{line.name}<div className="under">{line.kind === 'service' ? 'خدمة' : 'منتج'}</div></div>;
  return <div className="invoice-product">
    {line.image_url && failedImage !== line.image_url
      ? <img className="invoice-thumb" src={line.image_url} alt={line.name || 'صورة المنتج'} width="40" height="40"
          loading="lazy" decoding="async" onError={() => setFailedImage(line.image_url)}/>
      : <span className="invoice-thumb no-photo" role="img" aria-label="صورة المنتج غير متوفرة">📦</span>}
    <div><span className="under">الرقم المخزني</span><div><bdi>{line.sku || 'غير متوفر'}</bdi></div></div>
  </div>;
}

export default function SupplierInvoiceDetail({ row, scope }) {
  const read = useBalanceRead(config => simpleBalancesApi.supplierInvoice(row.party_id, row.invoice_id, config),
    JSON.stringify([scope, row.party_id, row.invoice_id]));
  if (read.error) return <p role="alert">{read.error} <button type="button" onClick={read.reload}>إعادة المحاولة</button></p>;
  const invoice = read.data;
  if (!invoice || invoice.id !== row.invoice_id) return <p role="status">جارٍ تحميل بنود الفاتورة…</p>;
  return <section className="supplier-invoice-lines"><h3>بنود الفاتورة</h3><div className="scroll"><table className="ledger"><thead><tr>
    {['المنتج أو الخدمة', 'الكمية', 'سعر الوحدة', 'قيمة البند'].map(title => <th key={title}>{title}</th>)}
  </tr></thead><tbody>{invoice.lines.map((line, i) => <tr key={i}>
    <td className="invoice-line-description">
      <ProductIdentity line={line}/>
      {line.services.map((service, j) => <div className="invoice-line-service under" key={j}>
        <span>{service.name ? 'الخدمة · ' + service.name : 'خدمة'}</span>
        <Amount value={service.total} currency={invoice.currency}/>
      </div>)}
    </td>
    <td><span className="invoice-field-label" aria-hidden="true">الكمية</span>{line.quantity}</td>
    <td><span className="invoice-field-label" aria-hidden="true">سعر الوحدة</span><Amount value={line.unit} currency={invoice.currency}/></td>
    <td><span className="invoice-field-label" aria-hidden="true">قيمة البند</span><Amount value={line.total} currency={invoice.currency}/></td>
  </tr>)}</tbody></table></div>
    {invoice.supplier_reference && <p>رقم فاتورة المورد · {invoice.supplier_reference}</p>}
    {invoice.note && <p>ملاحظة · {invoice.note}</p>}
    {invoice.tax != null && <p>الضريبة <Amount value={invoice.tax} currency={invoice.currency}/></p>}
    <p><strong>إجمالي الفاتورة <Amount value={invoice.total} currency={invoice.currency}/></strong></p></section>;
}
