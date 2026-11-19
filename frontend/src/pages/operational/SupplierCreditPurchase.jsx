import React, { useRef, useState } from 'react';
import { todaySA } from '../../lib/dates';
import { Amount, Heading, Icon } from './ApprovedFinanceUI';
import { simpleBalancesApi } from './simpleBalancesApi';
import { minorUnits, signedResourceBalance } from './simpleBalancesModel';
import { usePendingBalanceCommand } from './simpleBalancesPending';

const newLine = id => ({ id, kind: 'service', name: '', quantity: '1', unit_price: '' });
const digits = value => value.trim().replace(/[٠-٩]/g, n => '٠١٢٣٤٥٦٧٨٩'.indexOf(n))
  .replace(/[۰-۹]/g, n => '۰۱۲۳۴۵۶۷۸۹'.indexOf(n)).replace(/٫/g, '.');
const moneyText = units => String(units / 100n) + '.' + String(units % 100n).padStart(2, '0');
const pricePattern = /^[0-9]{1,10}(?:[.][0-9]{1,2})?$/;

function lineAmount({ quantity, unit_price }) {
  const qty = digits(quantity), price = digits(unit_price);
  if (!/^[0-9]{1,6}(?:[.][0-9]{1,3})?$/.test(qty) || !pricePattern.test(price)) return null;
  const [whole, fraction = ''] = qty.split('.');
  const units = BigInt(whole + fraction.padEnd(3, '0')) * minorUnits(price);
  const rounded = (units + 500n) / 1000n;
  return rounded > 0n ? rounded : null;
}

function Summary({ total, resource }) {
  const before = minorUnits(signedResourceBalance(resource));
  const after = before === null ? null : before + minorUnits(total);
  return <div className="note"><strong>قيمة الفاتورة · <Amount value={total}/></strong>
    {after !== null && <p>{after === 0n ? 'يتوازن حساب المورد بعد الحفظ' : <>
      {after > 0n ? 'المتبقي للمورد بعد الحفظ' : 'المتبقي لنا عند المورد بعد الحفظ'} · <Amount value={moneyText(after < 0n ? -after : after)}/></>}</p>}</div>;
}

export default function SupplierCreditPurchase({ context, resource, onSaved }) {
  const [lines,setLines] = useState(() => [newLine(0)]), nextId = useRef(1);
  const [date,setDate] = useState(todaySA), [reference,setReference] = useState(''), [note,setNote] = useState('');
  const [tax,setTax] = useState(''), [review,setReview] = useState(null), [error,setError] = useState('');
  const mutation = usePendingBalanceCommand({ context,
    storageIdentity: JSON.stringify(['supplier-credit-invoice', context.actor.id, context.session_scope, resource.id]),
    validate: ({action,target_id,command}) => action === 'entry' && target_id === null && command.party_type === 'supplier'
      && command.party_id === resource.id && command.side === 'for_party' && command.reason === 'credit_purchase' && Array.isArray(command.invoice?.lines),
    execute: value => simpleBalancesApi.entry(value.command),
    validateSaved: (result, value) => Boolean(result.invoice_id && result.invoice_number && result.movement_id)
      && result.party_type === 'supplier' && result.party_id === resource.id && result.amount === value.command.amount, onSaved });
  const item = mutation.pending?.command || review;
  const amounts = lines.map(lineAmount), taxText = digits(tax) || '0.00';
  const total = amounts.every(n => n !== null) && pricePattern.test(taxText)
    ? amounts.reduce((a,b) => a+b, minorUnits(taxText)) : null;
  const change = (id, update) => setLines(current => current.map(line => line.id === id ? {...line,...update} : line));
  function prepare(event) {
    event.preventDefault();
    if (total === null || total > 99999999999999n || lines.some(line => !line.name.trim())) {
      setError('أدخل وصف كل بند وكمية وسعرًا صحيحين، وراجع إجمالي الفاتورة.'); return;
    }
    setError('');
    setReview({party_type:'supplier', party_id:resource.id, reason:'credit_purchase', side:'for_party',
      business_date:date, amount:moneyText(total), reference:reference.trim(), note:note.trim(),
      invoice:{tax:moneyText(minorUnits(taxText)), lines:lines.map(line => ({
        name:line.name.trim(), kind:line.kind, quantity:digits(line.quantity), unit_price:moneyText(minorUnits(digits(line.unit_price))) }))}});
  }
  const locked = mutation.recovering || mutation.blocked || mutation.busy;
  return <><Heading title="فاتورة شراء آجل" subtitle={resource.name + ' · فاتورة مستقلة عن استلام الطلبات'}/>
    {mutation.error && <p className="note warning" role="alert">{mutation.error}</p>}
    {mutation.blocked && <button type="button" className="button" onClick={mutation.recover}>استعادة العملية السابقة</button>}
    {mutation.recovering && <p role="status">جارٍ التحقق من آخر عملية…</p>}
    {item ? <section className="panel form"><h2>مراجعة فاتورة {resource.name}</h2>
      <p>{item.business_date}{item.reference && ' · رقم فاتورة المورد '+item.reference} · يسجّلها {context.actor.name}</p>
      <div className="scroll"><table className="ledger"><thead><tr>{['البند','الكمية','سعر الوحدة','المجموع'].map(h=><th key={h}>{h}</th>)}</tr></thead>
        <tbody>{item.invoice.lines.map((line,i)=><tr key={i}><td>{line.name}<small className="under">{line.kind==='service'?'خدمة':'منتج'}</small></td>
          <td>{line.quantity}</td><td><Amount value={line.unit_price}/></td><td><Amount value={moneyText(lineAmount(line))}/></td></tr>)}</tbody></table></div>
      {item.note && <p>{item.note}</p>}<p>ضريبة الفاتورة · <Amount value={item.invoice.tax}/></p>
      <Summary resource={resource} total={item.amount}/>
      <div className="form-actions"><button type="button" className="button primary" disabled={locked} onClick={()=>mutation.submit('entry',null,()=>item)}>
        {mutation.busy?'جارٍ الحفظ…':mutation.pending?'إعادة محاولة حفظ الفاتورة نفسها':'حفظ الفاتورة على حساب المورد'}</button>
        {!mutation.pending && <button type="button" className="button" disabled={locked} onClick={()=>setReview(null)}>تعديل البنود</button>}</div></section>
    : <form className="panel form" onSubmit={prepare}><h2>بيانات الفاتورة</h2>
      <div className="fields"><label className="field">تاريخ الفاتورة<input required type="date" min={context.ledger_start} max={todaySA()} value={date} onChange={e=>setDate(e.target.value)}/></label>
        <label className="field">رقم فاتورة المورد (اختياري)<input maxLength={160} value={reference} onChange={e=>setReference(e.target.value)}/></label></div>
      <h2>المنتجات والخدمات</h2>{lines.map((line,i)=><fieldset className="purchase-line" key={line.id}><legend>البند {i+1}</legend>
        <label className="field">النوع<select value={line.kind} onChange={e=>change(line.id,{kind:e.target.value})}><option value="service">خدمة</option><option value="product">منتج</option></select></label>
        <label className="field">وصف البند<input required maxLength={200} placeholder="مثل تطريز اسم إضافي" value={line.name} onChange={e=>change(line.id,{name:e.target.value})}/></label>
        <label className="field">الكمية<input required inputMode="decimal" dir="ltr" value={line.quantity} onChange={e=>change(line.id,{quantity:e.target.value})}/></label>
        <label className="field">سعر الوحدة قبل الضريبة (ر.س)<input required inputMode="decimal" dir="ltr" value={line.unit_price} onChange={e=>change(line.id,{unit_price:e.target.value})}/></label>
        <div>قيمة البند<strong className="under">{amounts[i]===null?'—':<Amount value={moneyText(amounts[i])}/>}</strong></div>
        {lines.length>1 && <button type="button" className="textbtn" onClick={()=>setLines(lines.filter(row=>row.id!==line.id))}>حذف البند</button>}</fieldset>)}
      <button type="button" className="button" disabled={lines.length>=100} onClick={()=>setLines([...lines,newLine(nextId.current++)])}><Icon name="plus"/>إضافة بند</button>
      <div className="fields"><label className="field">مبلغ الضريبة حسب الفاتورة (اختياري)<input inputMode="decimal" dir="ltr" value={tax} onChange={e=>setTax(e.target.value)}/></label>
        <label className="field">ملاحظة (اختياري)<input maxLength={1000} value={note} onChange={e=>setNote(e.target.value)}/></label></div>
      {total!==null && <Summary resource={resource} total={moneyText(total)}/>}
      {error && <p role="alert" className="note warning">{error}</p>}
      <div className="form-actions"><button type="submit" className="button primary" disabled={locked}>مراجعة الفاتورة</button></div></form>}</>;
}
