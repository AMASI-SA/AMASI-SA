import React, {useEffect, useRef, useState} from 'react';
import {operationalApi as api, messageFor, requestId} from './api';
import {Field, Notice} from './OpeningBalances';

const choices = ['select', 'radio', 'color', 'image'];
const texts = ['text', 'string', 'textarea'];
const unitLabel = unit => ({piece: 'قطعة', m: 'متر', kg: 'كيلوجرام', stock_component: 'مكوّن مخزني'}[unit] || unit || 'قطعة');
const decimal = (value, precision) => {
  const text = String(value);
  if (!(precision ? new RegExp(`^\\d+(\\.\\d{1,${precision}})?$`) : /^\d+$/).test(text)) return null;
  const [whole, fraction = ''] = text.split('.');
  return BigInt(whole) * (BigInt(10) ** BigInt(precision)) + BigInt(fraction.padEnd(precision, '0') || '0');
};
const lineCents = line => {
  const precision = line.kind === 'component' ? line.quantity_precision || 0 : 0;
  const quantity = decimal(line.quantity, precision), cost = decimal(line.unit_cost, 2);
  if (quantity == null || cost == null || quantity <= BigInt(0) || cost <= BigInt(0) || Number(line.quantity) > 100000) return null;
  const scale = BigInt(10) ** BigInt(precision);
  return (quantity * cost + scale / BigInt(2)) / scale;
};
const money = cents => `${cents / BigInt(100)}.${String(cents % BigInt(100)).padStart(2, '0')}`;
function OptionInput({type, ...props}) {
  const Control = type === 'textarea' ? 'textarea' : 'input';
  return <Control maxLength={type === 'textarea' ? 1000 : 100} {...props}/>;
}
function ItemImage({url, name}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  const safe = typeof url === 'string' && (/^https?:\/\//i.test(url) || /^\/(?!\/)/.test(url));
  return safe && !failed ? <img src={url} alt={name} width="64" height="64" loading="lazy" onError={() => setFailed(true)} style={{objectFit: 'contain'}}/> : <span role="img" aria-label={`لا توجد صورة — ${name}`}>بدون صورة</span>;
}
export default function OpeningInventory({context}) {
  const allowed = context.can_manage_inventory_opening === true;
  const [catalog, setCatalog] = useState(null), [error, setError] = useState('');
  const [search, setSearch] = useState(''), [lines, setLines] = useState([]);
  const [kind, setKind] = useState('product');
  const [preview, setPreview] = useState(null), [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false), [result, setResult] = useState(null);
  const [revision, setRevision] = useState(0);
  const lock = useRef(false), generation = useRef(0);
  useEffect(() => {
    let alive = true;
    generation.current += 1;
    setCatalog(null); setLines([]); setPreview(null); setResult(null); setError(''); setConfirmed(false);
    if (allowed) api.inventoryOpeningContext().then(data => {
      if (!Array.isArray(data?.products) || !Array.isArray(data?.locations) || typeof data.writer_disabled !== 'boolean' || typeof data.approved !== 'boolean') throw Error('invalid_context');
      if (alive) setCatalog(data);
    }).catch(e => {if (alive) setError(messageFor(e));});
    return () => {alive = false; generation.current += 1;};
  }, [allowed, context.session_scope, revision]);
  const edit = next => {setLines(next); setPreview(null); setConfirmed(false); setError('');};
  const update = (index, patch) => edit(lines.map((line, i) => i === index ? {...line, ...patch} : line));
  const config = (index, patch) => update(index, {purchase_configuration: {...lines[index].purchase_configuration, ...patch}});
  const submitPreview = async () => {
    if (lock.current || !allowed || !lines.length || catalog?.approved) return;
    lock.current = true; setBusy(true); setError(''); setPreview(null); setConfirmed(false);
    const current = generation.current;
    try {
      const body = {request_id: requestId(), lines: lines.map(line => {
        if (lineCents(line) == null || !line.location_id) throw Error('أكمل الكمية الصحيحة لوحدة الصنف حتى 100000 والتكلفة الموجبة بمنزلتين عشريتين وموقع التخزين لكل سطر.');
        return {kind: line.kind, item_id: line.id, location_id: line.location_id, quantity: Number(line.quantity), unit_cost: line.unit_cost, purchase_configuration: line.purchase_configuration};
      })};
      const data = await api.previewInventoryOpening(body);
      if (!data?.preview_hash || !Array.isArray(data.lines) || data.financial_effect !== 'none' || typeof data.writer_disabled !== 'boolean') throw Error('تعذر التحقق من المعاينة؛ لم يُحفظ المخزون.');
      if (current === generation.current) setPreview({body, data});
    } catch (e) {if (current === generation.current) setError(e.response ? messageFor(e) : e.message);}
    finally {lock.current = false; setBusy(false);}
  };
  const approve = async () => {
    if (lock.current || !allowed || !preview || !confirmed || preview.data.writer_disabled || catalog?.writer_disabled || catalog?.approved) return;
    lock.current = true; setBusy(true); setError('');
    const current = generation.current;
    try {
      const data = await api.approveInventoryOpening({...preview.body, preview_hash: preview.data.preview_hash});
      if (data?.status !== 'approved') throw Error('لم يصل تأكيد اعتماد المخزون.');
      if (current === generation.current) {setResult(data); setPreview(null); setConfirmed(false);}
    } catch (e) {if (current === generation.current) setError(e.response ? messageFor(e) : e.message);}
    finally {lock.current = false; setBusy(false);}
  };
  const matches = (kind === 'component' ? catalog?.components || [] : catalog?.products || []).filter(p => `${p.name} ${p.sku || ''}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const costs = lines.map(lineCents);
  const total = lines.length && costs.every(cost => cost != null) ? costs.reduce((sum, cost) => sum + cost, BigInt(0)) : null;
  if (!allowed) return <p role="alert">إعداد المخزون الافتتاحي متاح للمالك فقط.</p>;
  return <section dir="rtl" aria-label="إدخال المخزون الافتتاحي"><h2>المخزون الافتتاحي للمنتجات والمكونات</h2>
    <p>سجّل المخزون الموجود فعليًا عند البداية، وليس شراءً جديدًا. الاعتماد يثبت الكميات والتكلفة والمواقع دون إنشاء قيود مالية أو تفعيل المحاسبة الأساسية.</p>
    <p>التكلفة هنا تشغيلية؛ التسجيل وحده لا يتيح المخزون لتنفيذ الطلبات قبل استيفاء ضوابط التقييم والجاهزية.</p>
    <Notice error={error}/>
    {!catalog && !error && <p role="status">جارٍ تحميل كتالوج المنتجات ومواقع التخزين…</p>}
    {!catalog && error && <button onClick={() => setRevision(v => v + 1)}>إعادة تحميل الكتالوج</button>}
    {result ? <p role="status">تم اعتماد المخزون الافتتاحي. يمكنك عرضه في المخزون المتاح. لم تُنشأ قيود مالية.</p> : catalog?.approved ? <p role="status">سبق اعتماد المخزون الافتتاحي؛ لا يمكن تسجيل افتتاحية ثانية.</p> : catalog && <>
      {catalog.writer_disabled && <p role="alert">كاتب المخزون مغلق؛ يمكنك تجهيز المعاينة فقط. الاعتماد محجوب حتى يُتاح وفق التفويض المعتمد. هذه الصفحة لا تفعّل الكاتب.</p>}
      <fieldset disabled={busy}><legend>اختيار المنتجات والكمية الفعلية</legend>
        <Field label="نوع الصنف"><select value={kind} onChange={e => {setKind(e.target.value); setSearch('');}}><option value="product">منتجات</option><option value="component">مكونات</option></select></Field>
        <Field label="بحث في كتالوج المنتجات والمكونات بالاسم أو الرمز"><input value={search} onChange={e => setSearch(e.target.value)}/></Field>
        <div className="op-tile-grid">{matches.slice(0, 50).map(p => <button type="button" aria-label={`إضافة ${p.name}${p.sku ? ` · ${p.sku}` : ''}`} key={p.id} onClick={() => edit([...lines, {...p, kind, quantity: '1', unit_cost: '', location_id: '', purchase_configuration: {state: 'raw', selections: [], inputs: []}}])}><ItemImage url={p.image_url} name={p.name}/><strong>{p.name}</strong><span>{p.sku || 'بدون رمز'}</span>{kind === 'component' && <small>{unitLabel(p.component_type)} · {unitLabel(p.unit)}</small>}</button>)}</div>
        {matches.length > 50 && <p>نعرض أول 50 من {matches.length} صنفًا؛ حدّد الاسم أو الرمز لتضييق البحث.</p>}
        {!matches.length && <p>{kind === 'product' ? 'لا توجد منتجات مطابقة في الكتالوج.' : 'لا توجد مكونات مطابقة في كتالوج ميزان 2.'}</p>}
        {!catalog.locations.length && <p role="alert">لا توجد مواقع تخزين مؤهلة؛ يلزم إعداد موقع قبل اعتماد المخزون.</p>}
        {lines.map((line, index) => <fieldset key={index} className="op-card"><legend>{index + 1} — {line.name}</legend>
          <ItemImage url={line.image_url} name={line.name}/><p>{line.name} · {line.sku || 'بدون رمز'}</p>
          {line.kind === 'component' && <p>النوع: {unitLabel(line.component_type)} · الوحدة: {unitLabel(line.unit)} (حسب الكتالوج)</p>}
          <Field label={`موقع التخزين — ${index + 1}`}><select value={line.location_id} onChange={e => update(index, {location_id: e.target.value})}><option value="">اختر الموقع</option>{catalog.locations.map(l => <option key={l.id} value={l.id}>{l.name || l.code}{l.warehouse_name ? ` — ${l.warehouse_name}` : ''}</option>)}</select></Field>
          {line.kind === 'product' && <Field label={`حالة المنتج — ${index + 1}`}><select value={line.purchase_configuration.state} onChange={e => config(index, {state: e.target.value, inputs: []})}><option value="raw">خام غير مخصص بالكامل</option><option value="ready">جاهز</option></select></Field>}
          {(line.kind === 'product' ? line.options || [] : []).map(o => choices.includes(o.type) ? <Field key={o.id} label={`${o.name} — ${index + 1}`}><select value={line.purchase_configuration.selections.find(s => s.option_id === o.id)?.value_id || ''} onChange={e => config(index, {selections: [...line.purchase_configuration.selections.filter(s => s.option_id !== o.id), ...(e.target.value ? [{option_id: o.id, value_id: e.target.value}] : [])]})}><option value="">غير محدد</option>{(o.values || []).map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</select></Field> : texts.includes(o.type) ? line.purchase_configuration.state === 'ready' && <Field key={o.id} label={`${o.name} — ${index + 1}`}><OptionInput type={o.type} value={line.purchase_configuration.inputs.find(s => s.option_id === o.id)?.value || ''} onChange={e => config(index, {inputs: [...line.purchase_configuration.inputs.filter(s => s.option_id !== o.id), ...(e.target.value ? [{option_id: o.id, value: e.target.value}] : [])]})}/></Field> : <p key={o.id}>الخيار {o.name} يحتاج مراجعة قبل إثبات الجاهزية.</p>)}
          <Field label={`الكمية — ${index + 1}`}><input inputMode={line.kind === 'component' && line.quantity_precision ? 'decimal' : 'numeric'} value={line.quantity} onChange={e => update(index, {quantity: e.target.value})}/></Field>
          <Field label={`تكلفة الوحدة بالريال — ${index + 1}`}><input inputMode="decimal" value={line.unit_cost} onChange={e => update(index, {unit_cost: e.target.value})}/></Field>
          <p role="status" aria-label={`إجمالي السطر ${index + 1}`}>إجمالي السطر: {costs[index] == null ? 'أكمل الكمية والتكلفة' : `${money(costs[index])} SAR`}</p>
          <button type="button" onClick={() => edit(lines.filter((_, i) => i !== index))}>إزالة السطر {index + 1}</button>
        </fieldset>)}
        <p role="status" aria-label="إجمالي تكلفة المدخلات">إجمالي تكلفة المدخلات: {total == null ? 'أكمل جميع السطور' : `${money(total)} SAR`}</p>
        <button type="button" disabled={!lines.length || !catalog.locations.length} onClick={submitPreview}>معاينة المخزون الافتتاحي</button>
      </fieldset>
      {preview && <section className="op-card" aria-label="معاينة المخزون الافتتاحي"><h3>راجع الكميات قبل الاعتماد</h3><p>إجمالي الكمية: {(preview.data.quantity_totals || []).map(row => `${row.quantity} ${unitLabel(row.unit)}`).join(' / ') || '—'} · إجمالي التكلفة: {preview.data.total_cost} {preview.data.currency}</p>
        <div className="op-table"><table><caption>المنتجات والكميات التي ستُسجل</caption><thead><tr>{['المنتج', 'الخيارات', 'موقع التخزين', 'الكمية', 'تكلفة الوحدة', 'إجمالي التكلفة'].map(title => <th key={title} scope="col">{title}</th>)}</tr></thead><tbody>{preview.data.lines.map((line, i) => <tr key={i}><th scope="row">{line.name}{line.kind === 'component' && <small> · {unitLabel(line.component_type)}</small>}</th><td>{Object.entries(line.specifications || {}).map(([k, v]) => `${k}: ${v}`).join(' · ') || 'بدون تخصيص'} · {line.preparation_state === 'ready_complete' ? 'جاهز' : 'خام / عادي'}</td><td>{line.location_name}</td><td>{line.quantity} {unitLabel(line.unit)}</td><td>{line.unit_cost} SAR</td><td>{line.total_cost} SAR</td></tr>)}</tbody></table></div>
        <p>الاعتماد يضيف مخزونًا فعليًا، ولا ينشئ شراءً أو قيدًا ماليًا.</p>
        <p role="note">تُسجل الكمية الفعلية والتكلفة التشغيلية فقط. تظل الكمية محجوبة عن تنفيذ الطلبات حتى استكمال مطابقة التقييم؛ لا يعني الاعتماد أنها متاحة للبيع.</p>
        {preview.data.writer_disabled && <p role="alert">الاعتماد محجوب: كاتب المخزون مغلق.</p>}
        <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)}/>راجعت جميع المنتجات والخيارات والمواقع والكميات، وهذه افتتاحية المخزون الموجودة فعليًا.</label>
        <button disabled={busy || !confirmed || preview.data.writer_disabled || catalog.writer_disabled} onClick={approve}>اعتماد المخزون الافتتاحي</button>
      </section>}
    </>}
  </section>;
}
