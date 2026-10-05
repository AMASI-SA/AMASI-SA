import React, { useEffect, useRef, useState } from 'react';
import { operationalApi as api, entityKinds, requestId, messageFor } from './api';
import './operational.css';
export function Field({
  label,
  children
}) {
  return <label className="op-field"><span>{label}</span>{children}</label>;
}
export function Notice({
  error,
  message
}) {
  return <>{error && <p role="alert" className="op-error">{error}</p>}{message && <p role="status" className="op-success">{message}</p>}</>;
}
export function EntityPicker({
  kind,
  value,
  onChange,
  disabled,
  onItems
}) {
  const [items, setItems] = useState([]),
    [loading, setLoading] = useState(false),
    [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    setItems([]);
    setError('');
    if (!kind) return;
    setLoading(true);
    api.entities(kind).then(result => {
      if (alive) {
        setItems(result.items);
        onItems?.(result.items);
      }
    }).catch(e => {
      if (alive) setError(messageFor(e));
    }).finally(() => {
      if (alive) setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [kind, onItems]);
  return <><Field label="الجهة"><select value={value} disabled={disabled || loading || !kind} onChange={e => onChange(e.target.value, items.find(i => i.id === e.target.value))}><option value="">{loading ? 'جاري تحميل الجهات…' : 'اختر الجهة'}</option>{items.map(i => <option key={i.id} value={i.id} disabled={i.ready === false}>{i.name}{i.ready === false ? ' — إعداد غير مكتمل' : ''}</option>)}</select></Field>{error && <p role="alert">{error}</p>}{kind && !loading && !error && !items.length && <p>لا توجد جهات متاحة لهذا النوع.</p>}</>;
}
export function KindPicker({
  value,
  onChange,
  disabled
}) {
  return <Field label="نوع الرصيد"><select value={value} disabled={disabled} onChange={e => onChange(e.target.value)}><option value="">اختر النوع</option>{entityKinds.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></Field>;
}
export function OpeningBalances({
  context,
  onFinished
}) {
  const empty = {
    party_type: '',
    party_id: '',
    direction: '',
    amount: '',
    currency: ''
  };
  const [form, setForm] = useState(empty),
    [count, setCount] = useState(context.opening_count || 0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState(''),
    [adding, setAdding] = useState(false),
    [name, setName] = useState(''),
    [revision, setRevision] = useState(0);
  const request = useRef(null),
    lock = useRef(false), addRequest = useRef(null);
  const change = patch => {
    setForm(f => ({
      ...f,
      ...patch
    }));
    request.current = null;
    setError('');
    setMessage('');
  };
  const save = async finish => {
    if (lock.current) return;
    const hasInput = Object.values(form).some(Boolean);
    const valid = form.party_type && form.party_id && form.direction && /^\d+(\.\d{1,2})?$/.test(form.amount) && form.currency;
    if ((!finish || hasInput) && !valid) {
      setError('اختر النوع والجهة والاتجاه وأدخل مبلغًا صحيحًا.');
      return;
    }
    lock.current = true;
    setBusy(true);
    setError('');
    request.current ||= requestId();
    try {
      const opening = {
        ...form,
        request_id: request.current
      };
      if (finish) {
        await api.finish({
          request_id: request.current,
          ...(hasInput ? {
            opening
          } : {})
        });
        onFinished();
      } else {
        await api.opening(opening);
        setCount(n => n + 1);
        setForm(empty);
        request.current = null;
        setMessage('تم حفظ الرصيد');
      }
    } catch (e) {
      setError(messageFor(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };
  const add = async () => {
    if (!name.trim() || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    addRequest.current ||= requestId();
    const key = addRequest.current;
    try {
      const result = await api.addEntity(form.party_type, {
        request_id: key,
        name: name.trim(),
        currency: 'SAR'
      });
      change({
        party_id: result.id,
        currency: result.currency
      });
      setRevision(n => n + 1);
      setAdding(false);
      setName('');
      addRequest.current = null;
    } catch (e) {
      setError(messageFor(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };
  return <section className="op-card"><h2>الأرصدة الافتتاحية</h2><p className="op-muted">أدخل رصيد كل جهة، ثم احفظ وأنهِ الإعداد لبدء الاحتساب التلقائي.</p><Notice error={error} message={message} /><fieldset disabled={busy}><KindPicker value={form.party_type} onChange={kind => change({
        ...empty,
        party_type: kind
      })} /><EntityPicker key={revision} kind={form.party_type} value={form.party_id} onChange={(id, item) => change({
        party_id: id,
        currency: item?.currency || ''
      })} />{['cash', 'external_person'].includes(form.party_type) && <button type="button" className="op-link" onClick={() => setAdding(true)}>+ إضافة {form.party_type === 'cash' ? 'صندوق' : 'جهة خارجية'}</button>}{adding && <div className="op-add"><Field label="الاسم"><input value={name} onChange={e => { setName(e.target.value); addRequest.current = null; }} /></Field><p>العملة: SAR</p><button onClick={add}>إضافة</button><button onClick={() => setAdding(false)}>إلغاء</button></div>}<Field label="الاتجاه"><span className="op-segment">{[['for_party', 'له'], ['for_us', 'عليه']].map(([value, label]) => <button key={value} type="button" aria-pressed={form.direction === value} onClick={() => change({
            direction: value
          })}>{label}</button>)}</span></Field><p className="op-muted">{['bank', 'cash'].includes(form.party_type) ? 'عليه: رصيد متاح لأماسي. له: مديونية على أماسي.' : 'له: مستحق للجهة عند أماسي. عليه: مستحق لأماسي لدى الجهة.'}</p><Field label={`المبلغ${form.currency ? ' · ' + form.currency : ''}`}><input inputMode="decimal" value={form.amount} onChange={e => change({
          amount: e.target.value
        })} /></Field><button className="op-primary" onClick={() => save(false)}>{busy ? 'جارٍ الحفظ…' : 'حفظ'}</button><p className="op-count">الأرصدة المدخلة: {count}</p><button className="op-finish" onClick={() => save(true)}>حفظ وإنهاء</button></fieldset></section>;
}
