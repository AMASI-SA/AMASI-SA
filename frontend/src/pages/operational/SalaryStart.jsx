import React, {useEffect, useRef, useState} from 'react';
import {operationalApi as api, messageFor, requestId} from './api';
import {Notice} from './OpeningBalances';
import {visibleOperationalRead} from './visibleOperationalRead';

const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const day = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);
const amount = value => typeof value === 'string' && /^(0|[1-9]\d*)\.\d{2}$/.test(value);
const issues = {
  salary_contract_missing: 'عقد راتب غير مكتمل؛ راجع الراتب وتاريخ سريانه.',
  employee_salary_contract_incomplete: 'عقد راتب غير مكتمل؛ راجع الراتب وتاريخ سريانه وحالة الموظف.',
  employee_salary_contract_missing_or_ambiguous: 'عقد راتب مفقود أو متعدد؛ يلزم تحديد العقد المعتمد للموظف.',
  operational_salary_source_incomplete: 'بيانات راتب غير مكتملة؛ يلزم مراجعتها.',
  operational_salary_employee_identity_ambiguous: 'هوية موظف مكررة أو غير مكتملة.',
  operational_salary_recorded_day_reconciliation_required: 'يوجد استحقاق محفوظ يختلف عن المصدر؛ يلزم تسوية موثقة ولا يُعدّل تلقائيًا.'
};
function SalaryIssue({item}) {
  const conflict = item?.code === 'operational_salary_recorded_day_reconciliation_required';
  const money = (value, currency) => amount(value) && typeof currency === 'string' && /^[A-Z]{3}$/.test(currency)
    ? `${value} ${currency}` : 'غير متاح';
  return <div role="alert" className="op-error">
    <p>{issues[item?.code] || 'توجد بيانات تحتاج مراجعة؛ الاعتماد متوقف.'}</p>
    {typeof item?.source_id === 'string' && <p>مرجع المراجعة: <bdi>{item.source_id}</bdi></p>}
    {conflict && <>
      {day(item.business_date) && <p>يوم الاستحقاق: <bdi>{item.business_date}</bdi></p>}
      <p>المحفوظ: <bdi>{money(item.recorded, item.currency)}</bdi> · حسب البداية المقترحة: <bdi>{money(item.proposed, item.proposed_currency)}</bdi></p>
      {item.reason === 'cutover_rounding_changed' && <p>يتطابق المحفوظ مع الحساب من البداية السابقة؛ تغيير يوم البداية يغيّر توزيع التقريب بين الأيام. يبقى الفرق للمراجعة ولا يُعتمد تلقائيًا.</p>}
    </>}
  </div>;
}
const invalid = () => new Error('تعذر التحقق من استجابة النظام؛ أعد تحميل بداية الرواتب.');
const scopeKey = context => `${context.session_scope || ''}:${context.started_at || ''}:${context.status || ''}:${context.can_manage_salary_start === true}`;
function validContext(data, context) {
  return data?.source === 'operational_salary_cutover' && data.read_only === true &&
    hash(data.session_scope) && data.session_scope === context.session_scope &&
    data.started_at === context.started_at && data.timezone === 'Asia/Riyadh' &&
    day(data.first_day) && day(data.effective_first_day) && typeof data.approved === 'boolean' &&
    (!data.approved || data.effective_first_day === data.first_day);
}
function validPreview(data, settings) {
  return data?.session_scope === settings.session_scope && data.first_day === settings.first_day &&
    hash(data.preview_hash) && day(data.business_date) && data.business_date >= data.first_day &&
    Number.isSafeInteger(data.employee_count) && data.employee_count >= 0 &&
    Number.isSafeInteger(data.new_days) && data.new_days >= 0 &&
    typeof data.can_approve === 'boolean' && Array.isArray(data.issues) &&
    (!data.can_approve || data.issues.length === 0) && data.opening_confirmation_required === true &&
    data.additional_amounts != null && typeof data.additional_amounts === 'object' &&
    !Array.isArray(data.additional_amounts) &&
    Object.entries(data.additional_amounts).every(([currency, value]) => /^[A-Z]{3}$/.test(currency) && amount(value)) &&
    (data.new_days === 0 || Object.keys(data.additional_amounts).length > 0);
}

export default function SalaryStart({context}) {
  const allowed = context.can_manage_salary_start === true && context.status === 'active' && hash(context.session_scope);
  const currentKey = scopeKey(context);
  const liveKey = useRef(currentKey); liveKey.current = currentKey;
  const generation = useRef(0), lock = useRef(false), pending = useRef(null), readLane = useRef({}), previewRead = useRef(null);
  const [settings, setSettings] = useState(null), [preview, setPreview] = useState(null);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false), [uncertain, setUncertain] = useState(false);
  const [revision, setRevision] = useState(0), [saved, setSaved] = useState(false);

  useEffect(() => {
    const token = ++generation.current, key = currentKey;
    let alive = true;
    const fresh = () => alive && token === generation.current && key === liveKey.current;
    lock.current = false; pending.current = null;
    setSettings(null); setPreview(null); setConfirmed(false); setError(''); setUncertain(false); setSaved(false);
    setBusy(allowed);
    const cancelRead = allowed ? visibleOperationalRead(async config => {
      const data = await api.salaryStartContext(config);
      if (!validContext(data, context)) throw invalid();
      return data;
    }, {
      onData: data => {if (fresh()) setSettings({...data, scopeKey: key});},
      onError: e => {if (fresh()) setError(e.response ? messageFor(e) : e.message);},
      onEnd: () => {if (fresh()) setBusy(false);},
    }, readLane.current) : null;
    return () => {alive = false; cancelRead?.(); previewRead.current?.(); previewRead.current = null; generation.current += 1;};
  // Reload only for a changed actor, cutoff, permission/phase or an explicit reload.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentKey, allowed, revision]);

  const ready = allowed && settings?.scopeKey === currentKey;
  const fresh = (token, key) => token === generation.current && key === liveKey.current;
  const reload = () => {if (!lock.current) setRevision(v => v + 1);};
  function loadPreview() {
    if (!ready || settings.approved || saved || lock.current || pending.current) return;
    lock.current = true; setBusy(true); setError(''); setPreview(null); setConfirmed(false);
    const token = generation.current, key = currentKey;
    previewRead.current?.();
    previewRead.current = visibleOperationalRead(async config => {
      const data = await api.previewSalaryStart({first_day: settings.first_day}, config);
      if (!validPreview(data, settings)) throw invalid();
      return data;
    }, {
      onData: data => {if (fresh(token, key)) setPreview(data);},
      onError: e => {if (fresh(token, key)) setError(e.response ? messageFor(e) : e.message);},
      onEnd: () => {if (fresh(token, key)) {lock.current = false; setBusy(false);}},
    }, readLane.current);
  }
  async function approve() {
    if (!ready || settings.approved || saved || lock.current || !preview?.can_approve || (!confirmed && !pending.current)) return;
    const token = generation.current, key = currentKey;
    pending.current = pending.current || {request_id: requestId(), first_day: settings.first_day,
      expected_preview_hash: preview.preview_hash, expected_session_scope: preview.session_scope,
      opening_salaries_excluded: true};
    lock.current = true; setBusy(true); setError('');
    try {
      const data = await api.approveSalaryStart(pending.current);
      if (data?.ok !== true || data.first_day !== settings.first_day || data.bank_money_changed !== false) throw invalid();
      if (fresh(token, key)) {pending.current = null; setSaved(true); setPreview(null); setConfirmed(false); setUncertain(false);}
    } catch (e) {
      if (!fresh(token, key)) return;
      // An absent/5xx response is ambiguous: retry the exact same operation.
      const status = e?.response?.status;
      if ([400, 401, 403, 404, 409, 422].includes(status)) {
        pending.current = null; setPreview(null); setConfirmed(false); setUncertain(false);
        setSettings(null);
        setError('لم يُعتمد الطلب بهذه الاستجابة. أعد تحميل الحالة ثم المعاينة قبل المحاولة؛ ربما تغيرت البيانات أو الصلاحيات.');
      } else {
        setUncertain(true);
        setError('لم يصل تأكيد نهائي للاعتماد. لا نفترض نجاحه أو فشله؛ أعد إرسال الطلب نفسه أو تحقق بإعادة تحميل الحالة.');
      }
    } finally {if (fresh(token, key)) {lock.current = false; setBusy(false);}}
  }

  if (!allowed) return <p role="alert">إعداد بداية الرواتب متاح للمالك من المتصفح أثناء التشغيل فقط.</p>;
  return <section className="op-card" dir="rtl" aria-label="بداية استحقاق الرواتب">
    <h2>بداية استحقاق الرواتب</h2>
    <p>يوم القطع التشغيلي نفسه هو أول يوم راتب. يُحتسب استحقاق يوم كامل للموظف المستحق، ولو بدأ التشغيل بعد منتصف الليل.</p>
    <p><strong>استحقاق اليوم = الراتب الشهري ÷ عدد أيام الشهر الفعلي.</strong> تُراعى المباشرة والإجازة دون راتب وتاريخ سريان تغيير الراتب.</p>
    <Notice error={error}/>
    {!ready && busy && <p role="status">جارٍ التحقق من بداية الرواتب…</p>}
    {!ready && !busy && <button type="button" onClick={reload}>إعادة تحميل الحالة</button>}
    {ready && <>
      <dl><dt>أول يوم راتب — بتوقيت الرياض</dt><dd><strong><bdi>{settings.first_day}</bdi></strong> · يشمل هذا اليوم ولا يشمل ما قبله.</dd></dl>
      {(settings.approved || saved) ? <p role="status">تم اعتماد البداية من يوم القطع. يضيف الاحتساب التلقائي الأيام المستحقة مرة واحدة؛ الاعتماد ليس دفعًا للراتب ولا خصمًا من البنك.</p> : <>
        <p>قبل التفعيل، راجع الأيام الناقصة وأكد أنها غير مشمولة بالأرصدة الافتتاحية. لا تُعاد كتابة الاستحقاقات أو الدفعات السابقة.</p>
        <button type="button" disabled={busy || uncertain} onClick={loadPreview}>معاينة الاستحقاق من يوم القطع</button>
        {preview && <div aria-label="معاينة الرواتب">
          <p>المعاينة حتى <bdi>{preview.business_date}</bdi> · الموظفون: {preview.employee_count} · أيام استحقاق جديدة لجميع الموظفين: {preview.new_days}</p>
          <div className="op-table"><table><caption>الزيادة غير المسجلة فقط، وليست إجمالي أرصدة الموظفين</caption>
            <thead><tr><th scope="col">العملة</th><th scope="col">المبلغ الإضافي المستحق علينا</th></tr></thead>
            <tbody>{Object.entries(preview.additional_amounts).sort(([a], [b]) => a.localeCompare(b)).map(([currency, value]) => <tr key={currency}><th scope="row">{currency}</th><td><bdi>{value}</bdi></td></tr>)}</tbody>
          </table></div>
          {preview.new_days === 0 && <p>لا توجد أيام ناقصة في المعاينة الحالية؛ لا تُضاف مبالغ مكررة.</p>}
          {preview.issues.map((item, i) => <SalaryIssue key={i} item={item}/>)}
          {preview.can_approve ? <>
            <label className="op-field"><span><input type="checkbox" checked={confirmed} disabled={busy || uncertain} onChange={e => setConfirmed(e.target.checked)}/> أؤكد أن الأرصدة الافتتاحية لا تشمل رواتب يوم القطع وما بعده.</span></label>
            <button type="button" disabled={busy || (!confirmed && !uncertain)} onClick={approve}>{uncertain ? 'إعادة إرسال طلب الاعتماد نفسه' : 'اعتماد بداية الرواتب من يوم القطع'}</button>
          </> : <p>صحح مصادر المعاينة قبل الاعتماد؛ لم تتغير الأرصدة.</p>}
        </div>}
      </>}
      <button type="button" disabled={busy} onClick={reload}>إعادة تحميل الحالة</button>
    </>}
  </section>;
}
