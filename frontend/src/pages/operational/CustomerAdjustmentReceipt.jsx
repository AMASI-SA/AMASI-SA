import React, { useEffect, useRef, useState } from 'react';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { useBalanceRead } from './simpleBalancesHooks';
import { AdjustmentReadState } from './CustomerAdjustments';

const TYPES = ['image/png', 'image/jpeg', 'application/pdf'];
const MAX_SIZE = 5 * 1024 * 1024;

export function CustomerReceiptUpload({ context, orderNumber, value, onChange, onBusyChange, disabled, operation = 'customer.refund_execute' }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [name, setName] = useState('');
  const binding = JSON.stringify([context.actor.id, context.session_scope, orderNumber, operation]);
  const current = useRef(binding); current.current = binding;
  const mounted = useRef(false), lock = useRef(false);
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  async function upload(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || disabled || lock.current) return;
    setError('');
    if (!TYPES.includes(file.type) || !file.size || file.size > MAX_SIZE) {
      setError('اختر صورة PNG أو JPG أو ملف PDF بحجم لا يتجاوز 5 ميغابايت.'); return;
    }
    lock.current = true; setBusy(true); onBusyChange?.(true);
    const scope = binding;
    try {
      const body = new FormData();
      body.append('file', file); body.append('order_number', orderNumber); body.append('expected_session_scope', context.session_scope);
      body.append('operation', operation);
      const result = await api.uploadCustomerReceipt(body);
      if (!result?.id || result.order_number !== orderNumber || !TYPES.includes(result.mime) || result.size > MAX_SIZE) throw new Error('invalid receipt');
      if (mounted.current && current.current === scope) {setName(result.name); onChange(result.id);}
    } catch (failure) {
      if (mounted.current && current.current === scope) setError(simpleBalanceError(failure));
    } finally {
      lock.current = false;
      if (mounted.current && current.current === scope) {setBusy(false); onBusyChange?.(false);}
    }
  }
  return <div className="adjustment-receipt"><label className="field"><span>إيصال العملية (اختياري)</span>
    <input type="file" accept="image/png,image/jpeg,application/pdf" disabled={disabled || busy} onChange={upload}/>
    <small>صورة PNG أو JPG أو PDF، حتى 5 ميغابايت.</small></label>
    {busy && <p role="status">جارٍ رفع الإيصال…</p>}
    {error && <p className="note warning" role="alert">{error}</p>}
    {value && <p role="status">{name || 'إيصال مرفق'} <button type="button" className="textbtn" disabled={disabled || busy} onClick={() => {setName('');onChange('');}}>إزالة الإيصال</button></p>}
  </div>;
}

export function CustomerReceipt({ id, identity }) {
  const [open, setOpen] = useState(false), [preview, setPreview] = useState(null);
  const read = useBalanceRead(async config => {
    const blob = await api.customerReceipt(id, config);
    if (!(blob instanceof Blob) || !TYPES.includes(blob.type)) throw new Error('unsupported receipt');
    return blob;
  }, JSON.stringify([identity, id, 'customer-receipt']), open && Boolean(id));
  useEffect(() => {
    if (!read.data) {setPreview(null);return undefined;}
    const url = URL.createObjectURL(read.data); setPreview({blob:read.data,url});
    return () => URL.revokeObjectURL(url);
  }, [read.data]);
  const url = preview?.blob === read.data ? preview?.url : null;
  if (!id) return null;
  return <section className="adjustment-receipt"><button className="textbtn" type="button" onClick={() => setOpen(value => !value)}>{open ? 'إخفاء الإيصال' : 'عرض الإيصال'}</button>
    {open && <><AdjustmentReadState read={read}/>{url && (read.data.type === 'application/pdf'
      ? <a href={url} target="_blank" rel="noreferrer">فتح ملف الإيصال PDF</a>
      : <img src={url} alt="إيصال العملية المالية"/>)}</>}
  </section>;
}
