import React, {useEffect, useRef, useState} from 'react';
import './inventoryOptionRoles.css';

const roles = [
  ['physical', 'مواصفة للقطعة'],
  ['customization', 'تخصيص يحتاج تجهيز'],
  ['informational', 'إضافة جانبية لا تحتاج تجهيز مورد'],
];
const roleNames = new Set(roles.map(([value]) => value));
const textTypes = new Set(['text', 'string', 'textarea']);
const errorMessage = error => typeof error?.response?.data?.detail?.message === 'string' ? error.response.data.detail.message
  : typeof error?.message === 'string' ? error.message : 'تعذر حفظ التصنيف. احتفظنا باختياراتك؛ حاول مرة أخرى.';

function checkedPolicy(value, productId) {
  if (value?.product_id !== productId || !Number.isSafeInteger(value.revision) || value.revision < 0
      || typeof value.catalog_fingerprint !== 'string' || !value.catalog_fingerprint
      || !Array.isArray(value.options) || !value.roles || value.product?.id !== productId
      || !Array.isArray(value.product.options) || value.product.inventory_option_policy_revision !== value.revision
      || value.options.some(option => typeof option.id !== 'string' || !roleNames.has(value.roles[option.id]))
      || new Set(value.options.map(option => option.id)).size !== value.options.length) {
    throw Error('تعذر قراءة تصنيف الخيارات الحالي. أعد تحميله قبل الحفظ.');
  }
  return value;
}

export default function InventoryOptionRoles({product, disabled = false, loadPolicy, savePolicy, onApply, onEditingChange, onBusyChange}) {
  const productId = product.item_id || product.id;
  const [open, setOpen] = useState(false), [policy, setPolicy] = useState(null), [selection, setSelection] = useState({});
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const [stale, setStale] = useState(false);
  const lock = useRef(false), mounted = useRef(true);
  const callbacks = useRef({onEditingChange,onBusyChange});
  callbacks.current = {onEditingChange,onBusyChange};
  useEffect(() => {mounted.current = true; return () => {
    mounted.current = false;
    callbacks.current.onEditingChange?.(false);
    callbacks.current.onBusyChange?.(false);
  };}, []);
  function changeOpen(value) {setOpen(value); onEditingChange?.(value);}
  function changeBusy(value) {lock.current = value; setBusy(value); onBusyChange?.(value);}
  async function load() {
    if (disabled || lock.current) return;
    changeOpen(true); changeBusy(true); setError(''); setMessage('');
    try {
      const result = checkedPolicy(await loadPolicy(productId), productId);
      if (mounted.current) {setPolicy(result); setSelection({...result.roles}); setStale(false);}
    } catch (reason) {if (mounted.current) setError(errorMessage(reason));}
    finally {if (mounted.current) changeBusy(false); else lock.current = false;}
  }
  const dependency = policy?.options.find(option => selection[option.id] !== 'informational'
    && option.visibility_rules?.some(rule => selection[rule.option_id] === 'informational'));
  async function save() {
    if (disabled || lock.current || !policy || dependency || stale) return;
    changeBusy(true); setError(''); setMessage('');
    try {
      const result = checkedPolicy(await savePolicy(productId, {
        expected_revision: policy.revision, catalog_fingerprint: policy.catalog_fingerprint, roles: {...selection},
      }), productId);
      if (mounted.current) {
        onApply(result);
        setPolicy(result); setSelection({...result.roles}); changeOpen(false);
        setMessage('حُفظ التصنيف لهذا المنتج؛ سيُستخدم في إضافات المخزون وفواتير الشراء التالية.');
      }
    } catch (reason) {if (mounted.current) {setError(errorMessage(reason)); setStale(reason?.response?.status === 409);}}
    finally {if (mounted.current) changeBusy(false); else lock.current = false;}
  }
  return <section className="mz-option-role-editor" aria-label={'تصنيف خيارات '+product.name} aria-busy={busy}>
    {!open && <button type="button" className="mz-option-role-trigger" disabled={disabled || busy} onClick={load}>تصنيف الخيارات</button>}
    {message && <p role="status">{message}</p>}
    {open && <>
      <header><h4>تصنيف الخيارات وحقول النص</h4><p>يُحفظ التصنيف في ميزان لهذا المنتج. حدّد وظيفة الحقل، ثم أدخل مواصفات القطعة الموجودة في المخزون.</p></header>
      <ul className="mz-option-role-guide">
        <li><strong>مواصفة للقطعة:</strong> مثل المقاس ولون القطعة؛ تُحدد عند إضافة المخزون.</li>
        <li><strong>تخصيص يحتاج تجهيز:</strong> مثل الاسم المطرّز؛ تحدد هل نُفّذ أو سيُنفّذ عند طلب العميل.</li>
        <li><strong>إضافة جانبية لا تحتاج تجهيز مورد:</strong> مثل الملاحظة وكرت الإهداء ونصه؛ يختارها العميل في طلبه، ولا تُدخل قيمتها للمخزون.</li>
      </ul>
      {busy && <p role="status">جارٍ {policy ? 'حفظ أو تحديث التصنيف' : 'تحميل التصنيف'}…</p>}
      {error && <p role="alert">{error}</p>}
      {policy && <div className="mz-option-role-list">{policy.options.map(option => <label className="mz-option-role-row" key={option.id}>
        <span><strong>{option.name}</strong><small>{textTypes.has(option.type) ? 'حقل نص' : 'خيار'}{option.required ? ' · مطلوب في طلب العميل' : ' · اختياري في طلب العميل'}</small>
          {option.role_locked && <small>مواصفة ثابتة مرتبطة بمتغير المنتج</small>}</span>
        <select aria-label={'تصنيف '+option.name} value={selection[option.id]} disabled={disabled || busy || option.role_locked}
          onChange={event => {setSelection(current => ({...current, [option.id]: event.target.value})); if (!stale) setError('');}}>
          {roles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>)}</div>}
      {dependency && <p role="alert">الحقل «{dependency.name}» يعتمد على إضافة جانبية. صنّف حقول الإضافة التابعة لها كإضافات جانبية أيضًا، أو أبقِ السؤال المؤثر في تجهيز القطعة ضمن خيارات التجهيز.</p>}
      <p className="mz-option-role-note">إذا كان نص كرت الإهداء يظهر عند اختيار «نعم»، صنّف السؤال ونص الكرت كإضافات جانبية. يبقى ارتباطهما في طلب العميل محفوظًا.</p>
      <div className="mz-option-role-actions">
        <button type="button" className="op-primary" disabled={disabled || busy || !policy || !!dependency || stale} onClick={save}>حفظ التصنيف</button>
        {error && <button type="button" disabled={disabled || busy} onClick={load}>تحميل التصنيف الحالي</button>}
        <button type="button" disabled={disabled || busy} onClick={() => {changeOpen(false); setError('');}}>إلغاء التصنيف</button>
      </div>
    </>}
  </section>;
}
