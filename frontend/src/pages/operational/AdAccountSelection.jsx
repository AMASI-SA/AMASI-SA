import React, { useEffect, useRef, useState } from 'react';
import { operationalApi as api, messageFor, requestId } from './api';

const labels = {snapchat_ads:'سناب شات', meta_ads:'ميتا', google_ads:'جوجل', tiktok_ads:'تيك توك'};

export default function AdAccountSelection({onSaved, onClose}) {
  const [data, setData] = useState(null), [selected, setSelected] = useState([]);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const pending = useRef(null), saving = useRef(false);
  useEffect(() => {
    let active = true;
    api.adAccountSelection().then(result => {
      if (!active) return;
      setData(result);
      setSelected(result.items.filter(i => i.editable && i.selected).map(i => i.id));
    }).catch(e => {if (active) setError(messageFor(e));});
    return () => {active = false;};
  }, []);
  const save = async () => {
    if (!data || saving.current) return;
    saving.current = true; setBusy(true); setError('');
    pending.current ||= requestId();
    try {
      await api.saveAdAccountSelection({request_id:pending.current, version:data.version, account_ids:selected});
      onSaved();
    } catch (e) {setError(messageFor(e));}
    finally {saving.current = false; setBusy(false);}
  };
  return <section className="op-card" aria-label="اختيار الحسابات الإعلانية">
    <h3>الحسابات الظاهرة في الأرصدة</h3>
    <p>سناب وميتا يتبعان اختيارك المحفوظ في التطبيقات والربط. اختر هنا حسابات جوجل وتيك توك التي تريد عرضها في الأرصدة فقط.</p>
    <p>لا يغيّر هذا الحفظ المزامنة أو الحملات أو الأرصدة أو الربط المالي، ولا يفعّل المحاسبة. الحساب المختار قد يحتاج استكمال ربطه المالي قبل إدخال الرصيد.</p>
    <a href="/integrations-v2" target="_blank" rel="noopener noreferrer">إعداد اختيار سناب وميتا في التطبيقات والربط</a>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p role="status">جارٍ تحميل الحسابات…</p>}
    {data && !data.items.length && <p>لا توجد حسابات متصلة متاحة.</p>}
    {data && <fieldset disabled={busy}>{data.items.map(item => <label key={item.id} className="op-field">
      <input type="checkbox" checked={item.editable ? selected.includes(item.id) : item.selected}
        disabled={!item.editable} onChange={event => {
          setSelected(ids => event.target.checked ? [...ids, item.id] : ids.filter(id => id !== item.id));
          pending.current = null;
        }}/>
      <span>{labels[item.provider]} — {item.name} · <bdi>{item.external_account_id}</bdi>{!item.editable && ' — من اختيار التطبيقات والربط'}</span>
    </label>)}</fieldset>}
    <button type="button" onClick={save} disabled={!data || busy}>حفظ اختيار العرض فقط</button>
    <button type="button" onClick={onClose} disabled={busy}>إغلاق</button>
  </section>;
}
