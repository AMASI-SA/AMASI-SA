import React, { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { authIdentity, useBalanceRead } from './simpleBalancesHooks';
import { resourceKey, resourceLabels } from './simpleBalancesModel';
import './simpleBalances.css';
import './operationalToolTheme.css';

const operationLabels = {
  employee: 'الموظفون', supplier: 'الموردون', external: 'الجهات الخارجية', accounts: 'الحسابات',
  custody: 'العهد', expense: 'المصروفات', owner: 'المالك', provider: 'مزودو الدفع',
  ads: 'الإعلانات', courier: 'الشحن والتوصيل', customer: 'العملاء', entities: 'إضافة الحسابات',
};
const emptyGrant = resource => ({ kind: resource.kind, id: resource.id, use: false, movements: 'none', balance: false, report: false, print: false });
const emptyBulk = () => ({ use: '', movements: '', balance: '', report: '', print: '' });
const bulkScopeLabels = { ...resourceLabels, employee: 'الموظفين', supplier: 'الموردين', provider: 'مزودي الدفع', store_driver: 'الموصلين' };

function AccessEditor({ user, catalog, identity }) {
  const key = JSON.stringify([identity, user.id]);
  const read = useBalanceRead(async config => {
    const result = await api.userAccess(user.id, config);
    if (result?.user?.id !== user.id || !Number.isInteger(result?.access?.version)
      || !Array.isArray(result.access.resources) || !Array.isArray(result.access.operations)) throw new Error('invalid access document');
    return result;
  }, key);
  const [draft, setDraft] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [conflict, setConflict] = useState(false);
  const [kind, setKind] = useState('');
  const [query, setQuery] = useState('');
  const [bulk, setBulk] = useState(emptyBulk);
  const lock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!read.data) return;
    setDraft({ ...read.data.access, operations: [...read.data.access.operations], resources: read.data.access.resources.map(row => ({ ...row })) });
    setDirty(false); setConflict(false); setError(''); setMessage(''); setBulk(emptyBulk());
  }, [read.data]);
  const change = update => {
    if (busy || !draft) return;
    setDraft(value => ({ ...value, ...update })); setDirty(true); setMessage(''); setError('');
  };
  const grantFor = resource => ({ ...emptyGrant(resource), ...draft?.resources.find(row => resourceKey(row) === resourceKey(resource)) });
  const updateGrant = (resource, patch) => {
    const grant = { ...grantFor(resource), ...patch };
    change({ resources: [...draft.resources.filter(row => resourceKey(row) !== resourceKey(resource)), grant] });
  };
  const updateOperation = (operation, checked) => change({ operations: checked
    ? [...new Set([...draft.operations, operation])]
    : draft.operations.filter(value => value !== operation) });
  const scopedResources = catalog.resources.filter(resource => !kind || resource.kind === kind);
  const scopeLabel = kind ? bulkScopeLabels[kind] || kind : 'الحسابات والأطراف';
  const hasBulk = Object.values(bulk).some(Boolean);
  const applyBulk = () => {
    if (busy || lock.current || conflict || !draft || !scopedResources.length || !hasBulk) return;
    const patch = {};
    if (bulk.use) patch.use = bulk.use === 'allow';
    if (bulk.movements) patch.movements = bulk.movements;
    if (bulk.balance) patch.balance = bulk.balance === 'allow';
    if (bulk.report) patch.report = bulk.report === 'allow';
    if (bulk.print) patch.print = bulk.print === 'allow';
    const resources = new Map(draft.resources.map(resource => [resourceKey(resource), resource]));
    let changed = 0;
    for (const resource of scopedResources) {
      const key = resourceKey(resource);
      const current = resources.get(key) || emptyGrant(resource);
      if (Object.keys(patch).some(field => current[field] !== patch[field])) {
        resources.set(key, { ...current, ...patch });
        changed += 1;
      }
    }
    if (changed) change({ resources: [...resources.values()] });
    setBulk(emptyBulk()); setQuery('');
    setMessage(changed
      ? `طُبقت اختياراتك على جميع ${scopeLabel} ضمن القائمة الحالية (${scopedResources.length}). يمكنك تعديل أي اسم في الجدول، ثم حفظ الصلاحيات.`
      : 'الحسابات والأطراف المحددة لديها هذه الصلاحيات بالفعل.');
  };
  async function save(event) {
    event.preventDefault();
    if (lock.current || !draft || !dirty || conflict) return;
    lock.current = true; setBusy(true); setError(''); setMessage('');
    try {
      const body = {
        expected_version: draft.version,
        enabled: draft.enabled === true,
        operations: [...draft.operations],
        resources: draft.resources.filter(row => row.use || row.balance || row.report || row.print || row.movements !== 'none')
          .map(({ kind: resourceKind, id, use, movements, balance, report, print }) => ({ kind: resourceKind, id, use: use === true, movements, balance: balance === true, report: report === true, print: print === true })),
      };
      const result = await api.saveUserAccess(user.id, body);
      if (result?.user?.id !== user.id || !Number.isInteger(result?.access?.version)) throw new Error('invalid saved access');
      if (mounted.current) {
        setDraft(result.access); setDirty(false); setConflict(false);
        setMessage(`تم حفظ صلاحيات ${user.name}. تُطبق على الطلبات التالية مباشرة.`);
      }
    } catch (failure) {
      if (mounted.current) {
        if (failure?.response?.data?.detail?.code === 'operational_access_owner_immutable') {
          setConflict(true);
          setError('صلاحيات المالك كاملة ولا يمكن تعديلها من صلاحيات الموظفين.');
        } else if (failure?.response?.status === 409) {
          setConflict(true);
          setError('عدّل شخص آخر هذه الصلاحيات. بقيت اختياراتك أمامك؛ حمّل آخر نسخة ثم راجع التغييرات قبل الحفظ.');
        } else setError(simpleBalanceError(failure));
      }
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  const matching = scopedResources.filter(resource => !query.trim() || `${resource.name} ${resourceLabels[resource.kind] || ''}`.includes(query.trim()));
  const groups = [...new Set(catalog.operations.map(operation => operation.key.split('.')[0]))];
  if (read.error) return <section><p role="alert" className="sb-notice sb-error">{read.error}</p><button type="button" className="sb-secondary" onClick={read.reload}>إعادة تحميل الصلاحيات</button></section>;
  if (!draft || read.loading) return <p role="status">جارٍ تحميل صلاحيات الموظف…</p>;
  return <form className="sb-access-editor" onSubmit={save}>
    <div className="sb-section-title"><h2>صلاحيات {user.name}</h2><span>{dirty ? 'تغييرات لم تُحفظ' : 'النسخة المحفوظة'}</span></div>
    {error && <p role="alert" className="sb-notice sb-error">{error}</p>}{message && <p role="status" className="sb-notice">{message}</p>}
    <label className="sb-check"><input type="checkbox" checked={draft.enabled === true} disabled={busy} onChange={event => change({ enabled: event.target.checked })} /><span>السماح بدخول مساحة الأرصدة</span></label>
    {!draft.enabled && <p className="sb-small-note">الدخول متوقف. تبقى الاختيارات أدناه محفوظة حتى تعيد تفعيل الدخول.</p>}
    <section className="sb-access-resources"><h3>الحسابات والجهات المسموحة</h3><p>حدد النوع لتطبيق الصلاحيات على جميع أسمائه، ثم عدّل أي اسم في الجدول حسب الحاجة. الاستخدام والحركات والرصيد خيارات مستقلة.</p>
      <div className="sb-form-two"><label className="sb-field"><span>نوع الحساب أو الجهة</span><select value={kind} onChange={event => { setKind(event.target.value); setBulk(emptyBulk()); setMessage(''); }}><option value="">جميع الحسابات والأطراف</option>{catalog.resource_kinds.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label><label className="sb-field"><span>بحث في الحسابات</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="اسم البنك أو الموظف أو الجهة" /></label></div>
      <fieldset className="sb-access-bulk" disabled={busy || conflict || !scopedResources.length} aria-describedby="sb-resource-bulk-note">
        <legend>صلاحيات جميع {scopeLabel}</legend>
        <p id="sb-resource-bulk-note">يشمل {scopedResources.length} من الحسابات والأطراف الحالية ضمن هذا الاختيار، حتى غير الظاهرة في البحث. الحساب أو الطرف الجديد يحتاج تحديد صلاحيته لاحقًا.</p>
        <div className="sb-access-bulk-fields">
          <label className="sb-field"><span>استخدام الحسابات والأطراف</span><select value={bulk.use} onChange={event => setBulk(value => ({ ...value, use: event.target.value }))}><option value="">بدون تغيير</option><option value="allow">مسموح</option><option value="deny">غير مسموح</option></select></label>
          <label className="sb-field"><span>عرض الحركات</span><select value={bulk.movements} onChange={event => setBulk(value => ({ ...value, movements: event.target.value }))}><option value="">بدون تغيير</option><option value="none">لا يعرضها</option><option value="own">التي سجّلها بنفسه</option><option value="all">حركات جميع الموظفين</option></select></label>
          <label className="sb-field"><span>عرض الأرصدة</span><select value={bulk.balance} onChange={event => setBulk(value => ({ ...value, balance: event.target.value }))}><option value="">بدون تغيير</option><option value="allow">مسموح</option><option value="deny">غير مسموح</option></select></label>
          <label className="sb-field"><span>تجهيز كشوف الفترة</span><select value={bulk.report} onChange={event => setBulk(value => ({ ...value, report: event.target.value }))}><option value="">بدون تغيير</option><option value="allow">مسموح</option><option value="deny">غير مسموح</option></select></label>
          <label className="sb-field"><span>طباعة وحفظ الكشوف</span><select value={bulk.print} onChange={event => setBulk(value => ({ ...value, print: event.target.value }))}><option value="">بدون تغيير</option><option value="allow">مسموح</option><option value="deny">غير مسموح</option></select></label>
        </div>
        <button type="button" className="sb-secondary" disabled={busy || conflict || !scopedResources.length || !hasBulk} onClick={applyBulk}>تطبيق على جميع {scopeLabel} ({scopedResources.length})</button>
        <p className="sb-small-note">يمكنك تعديل أي اسم في الجدول بعد التطبيق. اضغط «حفظ صلاحيات الموظف» لتفعيل الاختيارات.</p>
      </fieldset>
      <p className="sb-small-note">كشف الفترة يعرض الحركات المسموح بها فقط. الطباعة والحفظ يتطلبان السماح بتجهيز الكشف أيضًا. حركات الموظف نفسه تنتج تقرير نشاط محدودًا؛ الأرصدة الكاملة تحتاج السماح بكل الحركات وبالرصيد.</p>
      <div className="sb-table-wrap sb-access-resource-list"><table className="sb-access-table" aria-label="صلاحيات الحسابات والجهات"><thead><tr><th scope="col">الحساب</th><th scope="col">استخدامه</th><th scope="col">الحركات المعروضة</th><th scope="col">الرصيد</th><th scope="col">كشف فترة</th><th scope="col">طباعة وحفظ</th></tr></thead><tbody>{matching.map(resource => {
        const grant = grantFor(resource);
        return <tr key={resourceKey(resource)}><th scope="row"><strong>{resource.name}</strong><small>{resourceLabels[resource.kind] || resource.kind}</small></th>
          <td><label className="sb-access-toggle"><span className="sb-access-mobile-label">استخدام الحساب</span><input aria-label={`استخدام ${resource.name}`} type="checkbox" disabled={busy} checked={grant.use} onChange={event => updateGrant(resource, { use: event.target.checked })} /></label></td>
          <td><label className="sb-access-control"><span className="sb-access-mobile-label">الحركات المعروضة</span><select aria-label={`حركات ${resource.name}`} value={grant.movements} disabled={busy} onChange={event => updateGrant(resource, { movements: event.target.value })}><option value="none">لا يعرضها</option><option value="own">التي سجّلها بنفسه</option><option value="all">حركات جميع الموظفين</option></select></label></td>
          <td><label className="sb-access-toggle"><span className="sb-access-mobile-label">عرض الرصيد</span><input aria-label={`رصيد ${resource.name}`} type="checkbox" disabled={busy} checked={grant.balance} onChange={event => updateGrant(resource, { balance: event.target.checked })} /></label></td>
          <td><label className="sb-access-toggle"><span className="sb-access-mobile-label">تجهيز كشف الفترة</span><input aria-label={`كشف فترة ${resource.name}`} type="checkbox" disabled={busy} checked={grant.report === true} onChange={event => updateGrant(resource, { report: event.target.checked })} /></label></td>
          <td><label className="sb-access-toggle"><span className="sb-access-mobile-label">طباعة وحفظ الكشف</span><input aria-label={`طباعة ${resource.name}`} type="checkbox" disabled={busy} checked={grant.print === true} onChange={event => updateGrant(resource, { print: event.target.checked })} /></label></td></tr>;
      })}</tbody></table></div>{!matching.length && <p className="sb-empty">لا توجد حسابات مطابقة للبحث.</p>}
    </section>
    <section><h3>العمليات المسموحة</h3><p>تُنفذ هذه العمليات على الحسابات المختارة أعلاه فقط. اختيار الحساب وحده لا يمنح صلاحية الدفع منه.</p><div className="sb-operation-groups">{groups.map(group => <fieldset key={group} disabled={busy}><legend>{operationLabels[group] || group}</legend>{catalog.operations.filter(operation => operation.key.startsWith(`${group}.`)).map(operation => <label key={operation.key} className="sb-check"><input type="checkbox" checked={draft.operations.includes(operation.key)} onChange={event => updateOperation(operation.key, event.target.checked)} /><span>{operation.label}</span></label>)}</fieldset>)}</div></section>
    <div className="sb-actions"><button type="submit" className="sb-primary" disabled={busy || !dirty || conflict}>{busy ? 'جارٍ حفظ الصلاحيات…' : 'حفظ صلاحيات الموظف'}</button><button type="button" className="sb-secondary" disabled={busy} onClick={read.reload}>{conflict ? 'تحميل آخر نسخة من الصلاحيات' : 'إعادة قراءة النسخة المحفوظة'}</button></div>
  </form>;
}

export default function SimpleBalanceAccess() {
  const { search } = useLocation();
  const memberId = new URLSearchParams(search).get('member') || '';
  return <BalanceAccessPage key={memberId} initialMemberId={memberId}/>;
}

function BalanceAccessPage({initialMemberId}) {
  const auth = useOptionalAuth();
  const identity = authIdentity(auth);
  const owner = auth?.user?.is_owner === true;
  const [selectedId, setSelectedId] = useState(initialMemberId);
  const read = useBalanceRead(async config => {
    const [catalog, users] = await Promise.all([api.accessCatalog(config), api.accessUsers(config)]);
    if (!Array.isArray(catalog?.operations) || !Array.isArray(catalog?.resources)
      || !Array.isArray(catalog?.resource_kinds) || !Array.isArray(users?.items)) throw new Error('invalid access catalog');
    return { catalog, users: users.items.filter(item => item.role !== 'owner' && item.id !== auth.user.id) };
  }, identity, owner && !auth?.loading);
  const user = read.data?.users.find(item => item.id === selectedId);
  return <main className="sb-workspace sb-access-page mz-finance-tool" dir="rtl"><header className="sb-header"><div><span>ميزان · إدارة الحساب</span><h1>صلاحيات الموظفين</h1></div><Link className="sb-header-link" to="/operational-balances?view=tools">العمليات والكشوف</Link></header><div className="sb-content">
    {!owner ? <p role="alert" className="sb-notice sb-error">إدارة صلاحيات الأرصدة متاحة لمالك الحساب.</p>
      : <><p>حدد الموظف، ثم الحسابات التي يتعامل معها والعمليات المسموحة له. الحفظ يتم بعد مراجعة اختياراتك.</p>
        {read.error && <><p role="alert" className="sb-notice sb-error">{read.error}</p><button type="button" className="sb-secondary" onClick={read.reload}>إعادة التحميل</button></>}
        {!read.data && !read.error && <p role="status">جارٍ تحميل الموظفين والحسابات…</p>}
        {read.data && <><label className="sb-field"><span>الموظف</span><select value={user?.id || ''} onChange={event => setSelectedId(event.target.value)}><option value="">اختر الموظف</option>{read.data.users.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>{!read.data.users.length && <p>لا توجد حسابات موظفين متاحة لإدارة صلاحياتها.</p>}{selectedId && !user && <p role="status">لا يوجد حساب مطابق لهذا الموظف ضمن صلاحيات الأرصدة. اختر حسابًا من القائمة.</p>}
        <Link className="sb-header-link" to={'/employees-v2?workspace=permissions' + (user ? '&member=' + encodeURIComponent(user.id) : '')}>إدارة صفحات {user ? user.name : 'ميزان'}</Link>
        {user && <AccessEditor key={`${identity}:${user.id}`} identity={identity} user={user} catalog={read.data.catalog} />}</>}
      </>}
  </div></main>;
}

export { AccessEditor };
