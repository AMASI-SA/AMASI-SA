import React, { useEffect, useRef, useState } from 'react';
import { operationalApi as api, requestId, messageFor } from './api';
import { EntityPicker, KindPicker, Field, Notice } from './OpeningBalances';
export default function DailyMovements({
  source = 'mezan2',
  onSaved = () => {}
}) {
  const empty = {
    direction: '',
    party_type: '',
    party_id: '',
    bank_id: '',
    amount: '',
    currency: '',
    kind: 'payment',
    order_number: '',
    note: '',
    reference: '',
    actual_fee_amount: ''
  };
  const [form, setForm] = useState(empty),
    [banks, setBanks] = useState([]),
    [obligations, setObligations] = useState([]),
    [allocations, setAllocations] = useState({}),
    [file, setFile] = useState(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState('');
  const pending = useRef(null),
    receipt = useRef(null),
    lock = useRef(false),
    fileInput = useRef(null);
  useEffect(() => {
    let alive = true;
    Promise.all([api.entities('bank'), api.entities('cash')]).then(([b, c]) => {
      if (alive) setBanks([...b.items, ...c.items]);
    }).catch(e => {
      if (alive) setError(messageFor(e));
    });
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    let alive = true;
    setObligations([]);
    if (!form.party_type || !form.party_id || form.kind !== 'settlement') return;
    api.obligations(form.party_type, form.party_id).then(result => {
      if (alive) setObligations(result.items || []);
    }).catch(e => { if (alive) setError(messageFor(e)); });
    return () => { alive = false; };
  }, [form.party_type, form.party_id, form.kind]);
  const change = patch => {
    setForm(f => ({
      ...f,
      ...patch
    }));
    pending.current = null;
    setError('');
    setMessage('');
  };
  const eligible = obligations.filter(o => o.party_type === form.party_type && o.party_id === form.party_id && o.currency === form.currency && Number(o.outstanding) > 0 && o.direction === (form.direction === 'incoming' ? 'receivable' : 'payable'));
  const submit = async () => {
    if (lock.current) return;
    if (!form.direction || !form.party_id || (form.kind !== 'correction' && !form.bank_id) || !form.currency || !/^\d+(\.\d{1,2})?$/.test(form.amount) || Number(form.amount) <= 0) {
      setError('أكمل الاتجاه والجهة والبنك والمبلغ.');
      return;
    }
    if (form.kind === 'correction' && !form.note.trim()) { setError('أدخل سبب التصحيح.'); return; }
    const selected = (form.kind === 'settlement' ? eligible : []).filter(o => allocations[o.id]).map(o => ({
      obligation_id: o.id,
      amount: allocations[o.id]
    }));
    if (selected.some(a => !/^\d+(\.\d{1,2})?$/.test(a.amount) || Number(a.amount) <= 0)) {
      setError('أدخل مبلغ تسوية صحيحًا.');
      return;
    }
    lock.current = true;
    setBusy(true);
    setError('');
    pending.current ||= requestId();
    try {
      if (file && !receipt.current) receipt.current = await api.receipt(file);
      await api.movement({
        ...form,
        actual_fee_amount: form.kind === 'settlement' && form.party_type === 'provider' ? (form.actual_fee_amount || '0.00') : '0.00',
        request_id: pending.current,
        source,
        receipt_id: receipt.current?.id || null,
        order_number: form.order_number || null,
        allocations: selected
      });
      setForm(empty);
      setFile(null);
      setAllocations({});
      if (fileInput.current) fileInput.current.value = '';
      pending.current = null;
      receipt.current = null;
      setMessage('تم حفظ الحركة');
      onSaved();
      setObligations([]);
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
      lock.current = false;
    }
  };
  return <section className="op-card"><h2>الحركات المالية اليومية</h2><p className="op-muted">سجّل الحركة وأرفق الإيصال عند الحاجة.</p><Notice error={error} message={message} /><fieldset disabled={busy}><Field label="اتجاه الحركة"><span className="op-segment">{[['incoming', 'وارد'], ['outgoing', 'صادر']].map(([v, l]) => <button key={v} aria-pressed={form.direction === v} onClick={() => {
            change({
              direction: v
            });
            setAllocations({});
          }}>{l}</button>)}</span></Field><KindPicker value={form.party_type} onChange={kind => {
        change({
          party_type: kind,
          party_id: '',
          currency: ''
        });
        setAllocations({});
      }} /><EntityPicker kind={form.party_type} value={form.party_id} onChange={(id, item) => {
        change({
          party_id: id,
          currency: item?.currency || ''
        });
        setAllocations({});
      }} /><Field label="البنك أو الصندوق"><select value={form.bank_id} onChange={e => change({
          bank_id: e.target.value
        })}><option value="">اختر الحساب</option>{banks.filter(b => !form.currency || b.currency === form.currency).map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field><Field label="نوع الحركة"><select value={form.kind} onChange={e => change({
          kind: e.target.value
        })}>{[['payment', 'دفع'], ['collection', 'تحصيل'], ['settlement', 'تسوية مستحق'], ['transfer', 'تحويل'], ['refund', 'استرداد'], ['wallet_funding', 'تمويل محفظة'], ['correction', 'تصحيح موثق']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field><Field label={`المبلغ${form.currency ? ' · ' + form.currency : ''}`}><input inputMode="decimal" value={form.amount} onChange={e => change({
          amount: e.target.value
        })} /></Field>{form.kind === 'settlement' && <div><h3>المستحقات المراد تسويتها</h3>{eligible.length ? eligible.map((o, index) => <Field key={o.id} label={`${o.label || o.name || 'مستحق ' + (index + 1)} · المتبقي ${o.outstanding} ${o.currency}`}><input inputMode="decimal" value={allocations[o.id] || ''} onChange={e => {
            setAllocations(a => ({
              ...a,
              [o.id]: e.target.value
            }));
            pending.current = null;
          }} /></Field>) : <p>لا توجد مستحقات مؤكدة متاحة للتسوية.</p>}</div>}{form.kind === 'settlement' && form.party_type === 'provider' && <Field label="العمولة الفعلية المقتطعة من التسوية"><input inputMode="decimal" value={form.actual_fee_amount} onChange={e => change({actual_fee_amount:e.target.value})}/></Field>}<Field label="رقم الطلب (اختياري)"><input value={form.order_number} onChange={e => change({
          order_number: e.target.value
        })} /></Field><Field label="مرجع الحركة"><input value={form.reference} onChange={e => change({
          reference: e.target.value
        })} /></Field><Field label="الإيصال"><input ref={fileInput} type="file" accept="image/jpeg,image/png,application/pdf" onChange={e => {
          setFile(e.target.files[0] || null);
          receipt.current = null;
          pending.current = null;
        }} /></Field><Field label="ملاحظة"><textarea value={form.note} onChange={e => change({
          note: e.target.value
        })} /></Field><button className="op-primary" onClick={submit}>{busy ? 'جارٍ الحفظ…' : 'حفظ الحركة'}</button></fieldset></section>;
}
