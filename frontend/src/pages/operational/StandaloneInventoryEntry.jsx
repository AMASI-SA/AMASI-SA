import React, {useEffect, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {operationalApi as api, messageFor, requestId} from './api';
import {Field, Notice} from './OpeningBalances';

const choices = ['select', 'radio', 'color', 'image'];
const texts = ['text', 'string', 'textarea'];
const unitLabel = unit => ({piece: 'قطعة', m: 'متر', kg: 'كيلوجرام'}[unit] || unit);

export default function StandaloneInventoryEntry({context}) {
  const allowed = context.can_manage_inventory_opening === true;
  const storageKey = `mezan.inventory.entry.v1:${context.session_scope}`;
  const [recovery] = useState(() => {
    try {
      const command = JSON.parse(localStorage.getItem(storageKey) || 'null');
      if (command && (!command.request_id || !command.preview_hash || command.lines?.length !== 1)) throw Error();
      return {command};
    } catch { return {error: 'تعذر قراءة عملية الحفظ السابقة؛ تحقق من تخزين المتصفح قبل المتابعة.'}; }
  });
  const [pending, setPending] = useState(recovery.command);
  const [catalog, setCatalog] = useState(null), [error, setError] = useState(recovery.error || '');
  const [message, setMessage] = useState(''), [kind, setKind] = useState('product');
  const [search, setSearch] = useState(''), [line, setLine] = useState(null);
  const [busy, setBusy] = useState(false), [version, setVersion] = useState(0);
  const lock = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let alive = true;
    if (allowed) api.standaloneInventoryContext().then(data => {
      if (!Array.isArray(data?.products) || !Array.isArray(data?.components) || !Array.isArray(data?.locations)
          || !Array.isArray(data?.entries) || typeof data.writer_disabled !== 'boolean') throw Error();
      if (alive) setCatalog(data);
    }).catch(e => { if (alive) setError(messageFor(e)); });
    return () => { alive = false; };
  }, [allowed, version]);
  const update = patch => setLine(current => ({...current, ...patch}));
  const configure = patch => update({purchase_configuration: {...line.purchase_configuration, ...patch}});
  async function toggleWriter() {
    if (lock.current || !catalog || !allowed) return;
    lock.current = true; setBusy(true); setError('');
    try {
      await api.standaloneInventoryControl({revision: catalog.control_revision, paused: !catalog.writer_disabled});
      if (mounted.current) setVersion(v => v + 1);
    } catch (e) { if (mounted.current) setError(messageFor(e)); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  async function save() {
    if (lock.current || !allowed || !catalog || catalog.writer_disabled || recovery.error) return;
    lock.current = true; setBusy(true); setError(''); setMessage('');
    try {
      let command = pending;
      if (!command) {
        if (!line || !line.location_id || !/^\d+(\.\d{1,2})?$/.test(line.unit_cost) || Number(line.unit_cost) <= 0
            || !/^\d+(\.\d{1,6})?$/.test(line.quantity) || Number(line.quantity) <= 0 || Number(line.quantity) > 100000
            || (line.kind === 'product' && !Number.isInteger(Number(line.quantity)))) throw Error('أكمل الموقع والكمية الصحيحة والتكلفة الموجبة.');
        const body = {request_id: requestId(), lines: [{kind: line.kind, item_id: line.id,
          location_id: line.location_id, quantity: Number(line.quantity), unit_cost: line.unit_cost,
          purchase_configuration: line.purchase_configuration}]};
        const preview = await api.previewStandaloneInventory(body);
        if (!preview?.preview_hash || preview.writer_disabled || preview.fulfillment_eligible !== false) throw Error('تعذر التحقق من بيانات المخزون؛ لم يتم الحفظ.');
        command = {...body, preview_hash: preview.preview_hash};
        localStorage.setItem(storageKey, JSON.stringify(command));
        setPending(command);
      }
      const result = await api.saveStandaloneInventory(command);
      if (result?.status !== 'approved') throw Error('لم يصل تأكيد الحفظ؛ أعد المحاولة بنفس العملية.');
      localStorage.removeItem(storageKey);
      if (mounted.current) {
        setPending(null); setLine(null); setSearch(''); setVersion(v => v + 1);
        setMessage('تم تثبيت الكمية ونقل المنتج إلى جدول المخزون. يمكنك إضافة المنتج التالي.');
      }
    } catch (e) {
      if (e.response?.status === 409 && e.response?.data?.detail?.code === 'opening_inventory_preview_changed') {
        localStorage.removeItem(storageKey); if (mounted.current) setPending(null);
      }
      if (mounted.current) setError(e.response ? messageFor(e) : e.message);
    } finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  if (!allowed) return <p role="alert">إدخال المخزون متاح للمالك فقط.</p>;
  const matches = (kind === 'product' ? catalog?.products || [] : catalog?.components || [])
    .filter(p => `${p.name} ${p.sku || ''}`.toLowerCase().includes(search.trim().toLowerCase()));
  return <section dir="rtl" aria-label="إضافة المخزون"><h2>إضافة المخزون الموجود فعليًا</h2>
    <p>احفظ كل منتج بكميته ومواصفاته وموقعه؛ يُثبَّت فورًا ويظهر في الجدول. سجّل الفضي والذهبي كسطرين مستقلين.</p>
    <p role="note">هذا إدخال كميات موجودة عند التجهيز الأولي للمخزن، وليس فاتورة شراء أو تسوية نقص. لا يغيّر أرصدة البنوك والموردين ولا يصرف الكمية لطلبات العملاء؛ ربط التقييم والتشغيل مؤجل.</p>
    <Notice error={error}/>{message && <p role="status">{message}</p>}
    {!catalog && <button disabled={busy} onClick={() => setVersion(v => v + 1)}>تحميل بيانات المخزون</button>}
    {catalog && <>
      <p>{catalog.writer_disabled ? 'إدخال المخزون المستقل متوقف.' : 'إدخال المخزون المستقل مفعّل.'}</p>
      <button disabled={busy || !!pending} onClick={toggleWriter}>{catalog.writer_disabled ? 'تفعيل إدخال المخزون المستقل' : 'إيقاف إدخال المخزون المستقل'}</button>
      {!catalog.locations.length && <p role="alert">أنشئ المخزن والدواليب أولًا من <Link to="/components-v2?workspace=warehouse">إدارة المخازن</Link>؛ تظهر هنا مواقع المخازن المستقلة فقط.</p>}
      {pending && <div role="alert">توجد عملية حفظ بانتظار تأكيد نتيجتها. إعادة المحاولة بنفس المرجع تمنع تكرار الكمية.<button disabled={busy || catalog.writer_disabled} onClick={save}>إعادة محاولة الحفظ</button></div>}
      <fieldset disabled={busy || !!pending || !!recovery.error || catalog.writer_disabled || !catalog.locations.length}>
        <legend>إدخال منتج واحد</legend>
        {!line ? <>
          <Field label="نوع الصنف"><select value={kind} onChange={e => {setKind(e.target.value); setSearch('');}}><option value="product">منتجات</option><option value="component">مكونات</option></select></Field>
          <Field label="بحث بالاسم أو الرقم المخزني"><input value={search} onChange={e => setSearch(e.target.value)}/></Field>
          <ul>{matches.slice(0, 30).map(p => <li key={p.id}>{p.name} · {p.sku}<button onClick={() => setLine({...p, kind, quantity: '1', unit_cost: '', location_id: '', purchase_configuration: {state: kind === 'product' ? 'ready' : 'raw', selections: [], inputs: []}})}>اختيار {p.name}</button></li>)}</ul>
        </> : <div className="op-card">
          <h3>{line.name} · {line.sku}</h3>
          {!!line.configuration_issues?.length && <p role="alert">{line.configuration_issues.join(' · ')}</p>}
          <Field label="موقع التخزين"><select value={line.location_id} onChange={e => update({location_id: e.target.value})}><option value="">اختر الموقع</option>{catalog.locations.map(l => <option key={l.id} value={l.id}>{l.name} — {l.warehouse_name}</option>)}</select></Field>
          {line.kind === 'product' && <Field label="حالة المنتج"><select value={line.purchase_configuration.state} onChange={e => configure({state: e.target.value, inputs: []})}><option value="ready">جاهز بالمواصفات المحددة</option><option value="raw">يحتاج تجهيز</option></select></Field>}
          {(line.options || []).map(o => choices.includes(o.type) ? <Field key={o.id} label={o.name}><select value={line.purchase_configuration.selections.find(s => s.option_id === o.id)?.value_id || ''} onChange={e => configure({selections: [...line.purchase_configuration.selections.filter(s => s.option_id !== o.id), ...(e.target.value ? [{option_id: o.id, value_id: e.target.value}] : [])]})}><option value="">غير محدد</option>{o.values.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</select></Field> : texts.includes(o.type) && line.purchase_configuration.state === 'ready' ? <Field key={o.id} label={o.name}><input maxLength={o.type === 'textarea' ? 1000 : 100} value={line.purchase_configuration.inputs.find(s => s.option_id === o.id)?.value || ''} onChange={e => configure({inputs: [...line.purchase_configuration.inputs.filter(s => s.option_id !== o.id), ...(e.target.value ? [{option_id: o.id, value: e.target.value}] : [])]})}/></Field> : null)}
          <Field label={`الكمية (${unitLabel(line.unit || 'piece')})`}><input inputMode="decimal" value={line.quantity} onChange={e => update({quantity: e.target.value})}/></Field>
          <Field label="تكلفة الوحدة بالريال"><input inputMode="decimal" value={line.unit_cost} onChange={e => update({unit_cost: e.target.value})}/></Field>
          <button onClick={save}>حفظ المنتج في المخزون</button><button onClick={() => setLine(null)}>إلغاء الإدخال</button>
        </div>}
      </fieldset>
      <div className="op-table"><table><caption>آخر 100 إضافة مثبتة — جميع الكميات في صفحة المخزون الحالي</caption>
        <thead><tr>{['المنتج', 'الحالة والمواصفات', 'الموقع', 'الكمية', 'تكلفة الوحدة', 'الإجمالي'].map(t => <th key={t}>{t}</th>)}</tr></thead>
        <tbody>{catalog.entries.flatMap(entry => entry.lines.map((item, i) => <tr key={`${entry.id}:${i}`}><td>{item.name}</td><td>{item.preparation_state === 'ready_complete' ? 'جاهز' : 'يحتاج تجهيز'} · {Object.entries(item.specifications || {}).map(([k,v]) => `${k}: ${v}`).join(' · ')}</td><td>{item.location_name}</td><td>{item.quantity} {unitLabel(item.unit)}</td><td>{item.unit_cost}</td><td>{item.total_cost}</td></tr>))}</tbody>
      </table>{!catalog.entries.length && <p>لم تُضف كميات بعد.</p>}</div>
    </>}
  </section>;
}
