import React, {useEffect, useRef, useState} from 'react';
import {operationalApi as api, messageFor, requestId} from './api';

export default function AdFundingSettings({onSaved, onClose}) {
  const [data, setData] = useState(null), [account, setAccount] = useState('');
  const [evidence, setEvidence] = useState(''), [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const lock = useRef(false), pending = useRef(null);
  useEffect(() => {
    let alive = true;
    api.adFundingSettings().then(result => {if (alive) setData(result);})
      .catch(e => {if (alive) setError(messageFor(e));});
    return () => {alive = false;};
  }, []);
  const selected = data?.items.find(item => item.integration_account_id === account);
  const save = async () => {
    if (lock.current || !selected?.editable || data.status !== 'draft') return;
    if (evidence.trim().length < 3) {setError('أضف ملاحظة توضح اعتماد طريقة التمويل.'); return;}
    lock.current = true; setBusy(true); setError(''); pending.current ||= requestId();
    try {
      await api.saveAdFundingSettings({request_id:pending.current,
        integration_account_id:account, currency:selected.currency, version:selected.version,
        mode:'wallet_first', evidence:evidence.trim()});
      onSaved();
    } catch (e) {setError(messageFor(e));}
    finally {lock.current = false; setBusy(false);}
  };
  return <section className="op-card" aria-label="تمويل الحساب الإعلاني">
    <h3>تمويل الحساب الإعلاني</h3>
    <p>يُخصم الصرف من المحفظة، وعند نفادها يُسجّل الباقي دينًا. السداد يغطي الدين أولًا، ثم يزيد رصيد المحفظة.</p>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p role="status">جارٍ تحميل الحسابات…</p>}
    {data?.status !== 'draft' && data && <p>إعداد التمويل متاح قبل إنهاء الأرصدة الافتتاحية.</p>}
    {data && !data.items.length && <p>اختر الحسابات المتصلة للعرض أولًا.</p>}
    <fieldset disabled={busy || !data || data.status !== 'draft'}>
      <label className="op-field"><span>الحساب الإعلاني</span><select value={account} onChange={e => {
        setAccount(e.target.value); pending.current = null; setError('');
      }}><option value="">اختر الحساب</option>{data?.items.map(item =>
        <option key={item.integration_account_id} value={item.integration_account_id} disabled={!item.editable}>
          {item.name} · {item.currency}{item.configured ? ' — معدّ' : ''}{!item.editable ? ' — الإعداد محفوظ أو مرتبط مسبقًا' : ''}
        </option>)}</select></label>
      {selected && <p>عملة المحفظة والدين: <bdi>{selected.currency}</bdi></p>}
      <label className="op-field"><span>ملاحظة اعتماد التمويل</span><textarea maxLength={1000} value={evidence}
        onChange={e => {setEvidence(e.target.value); pending.current = null;}} /></label>
      <button type="button" disabled={!selected?.editable || busy} onClick={save}>اعتماد التمويل التشغيلي</button>
    </fieldset>
    <p>بعد الاعتماد أدخل رصيد المحفظة والدين الافتتاحي كلًّا على حدة، حسب المبلغ الفعلي.</p>
    <button type="button" disabled={busy} onClick={onClose}>إغلاق</button>
  </section>;
}
