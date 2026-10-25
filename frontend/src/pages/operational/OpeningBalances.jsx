import React, { useEffect, useRef, useState } from 'react';
import { operationalApi as api, entityKinds, requestId, messageFor } from './api';
import './operational.css';
import AdAccountSelection from './AdAccountSelection';
import AdFundingSettings from './AdFundingSettings';
const setupHints = {
  bank: 'اختر الحساب البنكي أو أضف حساب جهاز الشبكة باسمه. أدخل الرصيد الموجود فعليًا وقت البداية؛ لا يتطلب ذلك مندوبًا أو إيصالًا، ولا يُستورد أي رصيد سابق تلقائيًا.',
  courier: 'ظهور شركة الشحن في الطلبات لا يثبت ربط جهة الرصيد. راجع هوية الشركة وتأكيد ربطها في إعدادات الشحن بميزان 2.',
  ad_account: 'تظهر الحسابات المختارة للعرض فقط. استخدامها كرصيد يحتاج تأكيد ربطه وتحديد طريقة التمويل والعملة. الربط لا يعني تفعيل الكتابة المالية.',
};
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
  onItems,
  savedOpenings = []
}) {
  const [items, setItems] = useState([]),
    [loading, setLoading] = useState(false),
    [refresh, setRefresh] = useState(0),
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
  }, [kind, onItems, refresh]);
  const incomplete = items.some(i => i.ready === false || i.settings_complete === false);
  return <><Field label="الجهة"><select value={value} disabled={disabled || loading || !kind} onChange={e => onChange(e.target.value, items.find(i => i.id === e.target.value))}><option value="">{loading ? 'جاري تحميل الجهات…' : 'اختر الجهة'}</option>{items.map(i => <option key={i.id} value={i.id} disabled={i.ready === false || i.settings_complete === false}>{i.name}{savedOpenings.some(row => row.party_type === kind && row.party_id === i.id && row.currency === i.currency) ? ' — ✓ رصيد محفوظ' : ''}{i.ready === false || i.settings_complete === false ? ' — يلزم استكمال الربط' : ''}</option>)}</select></Field>{error && <p role="alert">{error}</p>}{kind && !loading && !error && !items.length && <p>لا توجد جهات مؤهلة لهذا النوع في إعدادات الأرصدة.</p>}{!loading && !error && (!items.length || incomplete) && setupHints[kind] && <p role="status" className="op-muted">{setupHints[kind]}{kind === 'courier' && <> <a href="/integrations-v2?workspace=financial&page=shipping-cod" target="_blank" rel="noopener noreferrer">فتح إعدادات الشحن والأسعار</a> <button type="button" disabled={disabled || loading} onClick={() => setRefresh(n => n + 1)}>تحديث الجهات بعد الإعداد</button></>}</p>}</>;
}
export function KindPicker({
  value,
  onChange,
  disabled
}) {
  return <Field label="نوع الرصيد"><select value={value} disabled={disabled} onChange={e => onChange(e.target.value)}><option value="">اختر النوع</option>{entityKinds.filter(([id]) => id !== 'pos_receivable').map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></Field>;
}
export function OpeningBalances({
  context,
  onFinished
}) {
  const [selectingAds, setSelectingAds] = useState(false);
  const [fundingAds, setFundingAds] = useState(false);
  const empty = {
    party_type: '',
    party_id: '',
    direction: '',
    amount: '',
    currency: ''
  };
  const [savedOpenings, setSavedOpenings] = useState(context.openings || []);
  useEffect(() => {
    if (Array.isArray(context.openings)) setSavedOpenings(context.openings);
    if (Number.isInteger(context.opening_count)) setCount(context.opening_count);
  }, [context.openings, context.opening_count]);
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
        const result = await api.opening(opening);
        const saved = result.saved_opening;
        if (saved) setSavedOpenings(rows => [...rows.filter(row => row.id !== saved.id), saved]);
        setCount(n => Number.isInteger(result.opening_count) ? result.opening_count : n + 1);
        setForm({...empty, party_type: form.party_type});
        request.current = null;
        setMessage(saved ? `تم حفظ رصيد ${saved.name}: ${saved.amount} ${saved.currency} — ${saved.direction === 'for_party' ? 'له' : 'عليه'}` : 'تم حفظ الرصيد');
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
  return <section className="op-card"><h2>الأرصدة الافتتاحية</h2><p className="op-muted">أدخل رصيد كل جهة، ثم احفظ وأنهِ الإعداد لبدء الاحتساب التلقائي.</p><p className="op-muted">هذه الشاشة لأرصدة الجهات المالية. المخزون الافتتاحي يُسجّل بكمياته ومواقعه وإثبات تقييمه في مسار المخزون؛ لا تُكرر قيمته هنا كمصروف أو رصيد له/عليه.</p><Notice error={error} message={message} /><fieldset disabled={busy}><KindPicker value={form.party_type} onChange={kind => { setAdding(false); setName(''); addRequest.current = null; change({ ...empty, party_type: kind }); }} />{form.party_type === 'ad_account' && context.can_select_ad_accounts && <button type="button" onClick={() => setSelectingAds(true)}>اختيار الحسابات الإعلانية الظاهرة</button>}{form.party_type === 'ad_account' && context.can_configure_ad_funding && <button type="button" onClick={() => setFundingAds(true)}>إعداد المحفظة والدين</button>}{fundingAds && <AdFundingSettings onClose={() => setFundingAds(false)} onSaved={() => {setFundingAds(false); setRevision(n => n + 1); change({party_id:'',currency:''});}} />}{selectingAds && <AdAccountSelection onClose={() => setSelectingAds(false)} onSaved={() => {setSelectingAds(false); setRevision(n => n + 1); change({party_id:'',currency:''});}} />}<EntityPicker savedOpenings={savedOpenings} key={revision} kind={form.party_type} value={form.party_id} onChange={(id, item) => change({
        party_id: id,
        currency: item?.currency || ''
      })} />{form.party_type === 'bank' && <p className="op-muted">رصيد الشبكة مستقل عن رصيد البنك وعهدة المندوب. عند تحويله للبنك، سجّل تحويلًا بين الحسابين.</p>}{['bank', 'cash', 'external_person', 'operating_expense'].includes(form.party_type) && <button type="button" className="op-link" onClick={() => setAdding(true)}>+ إضافة {form.party_type === 'bank' ? 'حساب بنك أو شبكة' : form.party_type === 'cash' ? 'صندوق' : form.party_type === 'operating_expense' ? 'نوع مصروف تشغيلي' : 'جهة خارجية'}</button>}{adding && <div className="op-add"><Field label="الاسم"><input value={name} onChange={e => { setName(e.target.value); addRequest.current = null; }} /></Field><p>العملة: SAR</p><button onClick={add}>إضافة</button><button onClick={() => setAdding(false)}>إلغاء</button></div>}<div className="op-field" role="group" aria-label="الاتجاه"><span>الاتجاه</span><span className="op-segment">{[['for_party', form.party_type === 'ad_account' ? 'دين مستحق للمنصة' : 'له'], ['for_us', form.party_type === 'ad_account' ? 'رصيد المحفظة' : 'عليه']].map(([value, label]) => <button key={value} type="button" aria-pressed={form.direction === value} onClick={() => change({
            direction: value
          })}>{label}</button>)}</span></div><p className="op-muted">{['bank', 'cash'].includes(form.party_type) ? 'عليه: رصيد متاح لأماسي. له: مديونية على أماسي.' : 'له: مستحق للجهة عند أماسي. عليه: مستحق لأماسي لدى الجهة.'}</p><p className="op-muted" role="status">{form.currency ? `عملة الرصيد: ${form.currency}` : "اختر الجهة لإظهار عملة الرصيد"}</p><Field label={`المبلغ${form.currency ? ' · ' + form.currency : ''}`}><input inputMode="decimal" value={form.amount} onChange={e => change({
          amount: e.target.value
        })} /></Field><button className="op-primary" onClick={() => save(false)}>{busy ? 'جارٍ الحفظ…' : 'حفظ'}</button><p className="op-count">الأرصدة المدخلة: {count}</p><section aria-label="الأرصدة المحفوظة"><h3>الأرصدة المحفوظة</h3>{savedOpenings.length ? <div className="op-table"><table><thead><tr><th>نوع الرصيد</th><th>الجهة</th><th>الاتجاه</th><th>المبلغ</th><th>العملة</th><th>الحالة</th></tr></thead><tbody>{savedOpenings.map(row => <tr key={row.id}><td>{entityKinds.find(([kind]) => kind === row.party_type)?.[1] || row.party_type}</td><td>{row.name}</td><td>{row.party_type === 'ad_account' ? (row.direction === 'for_party' ? 'دين مستحق للمنصة' : 'رصيد المحفظة') : (row.direction === 'for_party' ? 'له' : 'عليه')}</td><td>{row.amount}</td><td>{row.currency}</td><td>✓ محفوظ</td></tr>)}</tbody></table></div> : <p>{count ? 'توجد أرصدة محفوظة؛ تفاصيلها غير متاحة في هذه النسخة.' : 'لم تُحفظ أرصدة بعد.'}</p>}</section><button className="op-finish" onClick={() => save(true)}>حفظ وإنهاء</button></fieldset></section>;
}
