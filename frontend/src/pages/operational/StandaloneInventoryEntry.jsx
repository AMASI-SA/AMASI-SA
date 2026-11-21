import React, {useEffect, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {operationalApi as api, messageFor, requestId} from './api';
import {Field, Notice} from './OpeningBalances';
import {InventoryProductPicker,InventoryProductOptions,InventoryProductPhoto} from '../../components/inventory/suite/InventoryProductFields';
import {validateInventoryConfiguration} from '../../lib/inventoryProductConfiguration';

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
        if(line.kind==='product')validateInventoryConfiguration(line,line.purchase_configuration);
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
        setPending(null); setLine(null); setSearch('');
        setCatalog(current => ({...current, entries: [{...result, id: result.batch_id}, ...current.entries.filter(e => e.id !== result.batch_id)].slice(0, 100)}));
        setVersion(v => v + 1);
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
  const matches=kind==='product'?catalog?.products||[]:catalog?.components||[];
  return <section dir="rtl" aria-label="إضافة المخزون" className="mz-stock-entry"><h2>إضافة المخزون الموجود فعليًا</h2>
    <p>احفظ كل منتج بكميته ومواصفاته وموقعه؛ يُثبَّت فورًا ويظهر في الجدول. سجّل الفضي والذهبي كسطرين مستقلين.</p>
    <p role="note">هذا إدخال كميات موجودة عند التجهيز الأولي للمخزن، وليس فاتورة شراء أو تسوية نقص. لا يغيّر أرصدة البنوك والموردين ولا يصرف الكمية لطلبات العملاء؛ ربط التقييم والتشغيل مؤجل.</p>
    <Notice error={error}/>{message && <p role="status">{message}</p>}
    {!catalog && <button disabled={busy} onClick={() => setVersion(v => v + 1)}>تحميل بيانات المخزون</button>}
    {catalog && <>
      <div className="mz-stock-state"><strong>{catalog.writer_disabled?'إدخال المخزون المستقل متوقف.':'إدخال المخزون المستقل مفعّل.'}</strong><details><summary>إدارة الإدخال</summary><p>يخص هذا التحكم الإدخال الأولي المستقل فقط؛ الربط المحاسبي والتشغيل يبقيان وفق إعداداتهما الحالية.</p><button disabled={busy || (!!pending && !catalog.writer_disabled)} onClick={toggleWriter}>{catalog.writer_disabled ? 'تفعيل إدخال المخزون المستقل' : 'إيقاف إدخال المخزون المستقل'}</button></details></div>
      {!catalog.locations.length && <p role="alert">أنشئ المخزن والدواليب أولًا من <Link to="/components-v2?workspace=warehouse">إدارة المخازن</Link>؛ تظهر هنا مواقع المخازن المستقلة فقط.</p>}
      {pending && <div role="alert">توجد عملية حفظ بانتظار تأكيد نتيجتها. إعادة المحاولة بنفس المرجع تمنع تكرار الكمية.<button disabled={busy || catalog.writer_disabled} onClick={save}>إعادة محاولة الحفظ</button></div>}
      <fieldset className="op-stock-form" disabled={busy || !!pending || !!recovery.error || catalog.writer_disabled || !catalog.locations.length}>
        <legend>إدخال منتج واحد</legend>
        {!line ? <>
          <Field label="نوع الصنف"><select value={kind} onChange={e => {setKind(e.target.value); setSearch('');}}><option value="product">منتجات</option><option value="component">مكونات</option></select></Field>
          <InventoryProductPicker items={matches} query={search} onQueryChange={setSearch} initialOpen label="بحث بالاسم أو الرقم المخزني" onPick={p=>setLine({...p,kind,quantity:'1',unit_cost:'',location_id:'',purchase_configuration:{state:kind==='product'?'ready':'raw',selections:[],inputs:[]}})}/>
        </> : <div className="mz-purchase-line">
          <div className="mz-product-selected"><InventoryProductPhoto product={line}/><div><h3>{line.name}</h3><span>الرقم المخزني <bdi>{line.sku||'غير محدد'}</bdi></span></div><button type="button" onClick={()=>setLine(null)}>تغيير الصنف</button></div>
          {line.kind==='product'&&<InventoryProductOptions product={line} config={line.purchase_configuration} onChange={config=>update({purchase_configuration:config})}/>}
          <Field label="موقع التخزين"><select value={line.location_id} onChange={e=>update({location_id:e.target.value})}><option value="">اختر الموقع</option>{catalog.locations.map(l=><option key={l.id} value={l.id}>{l.name||l.code}{l.warehouse_name?' · '+l.warehouse_name:''}</option>)}</select></Field>
          <div className="mz-stock-amounts">
          <Field label={`الكمية (${unitLabel(line.unit || 'piece')})`}><input inputMode="decimal" value={line.quantity} onChange={e => update({quantity: e.target.value})}/></Field>
          <Field label="تكلفة الوحدة بالريال"><input inputMode="decimal" value={line.unit_cost} onChange={e => update({unit_cost: e.target.value})}/></Field>
          <div className="mz-stock-total"><span>إجمالي التكلفة</span><strong>{Number(line.quantity)>0&&Number(line.unit_cost)>0?(Number(line.quantity)*Number(line.unit_cost)).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'0.00'} ر.س</strong></div></div>
          <div className="mz-stock-actions"><button className="op-primary" aria-label="حفظ المنتج في المخزون" onClick={save}>{busy?'جارٍ تأكيد الحفظ…':'حفظ المنتج في المخزون'}</button><button onClick={()=>setLine(null)}>إلغاء الإدخال</button></div>
        </div>}
      </fieldset>
      <div className="op-table"><table><caption>آخر 100 إضافة مثبتة — جميع الكميات في صفحة المخزون الحالي</caption>
        <thead><tr>{['المنتج', 'الحالة والمواصفات', 'الموقع', 'الكمية', 'تكلفة الوحدة', 'الإجمالي'].map(t => <th key={t}>{t}</th>)}</tr></thead>
        <tbody>{catalog.entries.flatMap(entry => entry.lines.map((item, i) => <tr key={`${entry.id}:${i}`}><td>{item.name}</td><td>{item.preparation_state === 'ready_complete' ? 'جاهز' : 'يحتاج تجهيز'} · {Object.entries(item.specifications || {}).map(([k,v]) => `${k}: ${v}`).join(' · ')}</td><td>{item.location_name}</td><td>{item.quantity} {unitLabel(item.unit)}</td><td>{item.unit_cost}</td><td>{item.total_cost}</td></tr>))}</tbody>
      </table>{!catalog.entries.length && <p>لم تُضف كميات بعد.</p>}</div>
    </>}
  </section>;
}
