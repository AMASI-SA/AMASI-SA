import { useEffect, useRef, useState } from 'react';
import { useOptionalAuth } from '../context/AuthContext';
import { getProviderStatementAccess, saveProviderStatementAccess } from '../services/providerStatementAccess';

const platforms = new Set(['tabby', 'tamara', 'salla']);
const uniqueProviders = rows => Array.isArray(rows) && rows.length <= platforms.size
    && rows.every(id => platforms.has(id)) && new Set(rows).size === rows.length;
const sameProviders = (a, b) => a.length === b.length && a.every(id => b.includes(id));
const visible = () => typeof document === 'undefined' || document.visibilityState !== 'hidden';

function valid(result, employee) {
    const access = result?.access;
    return result?.employee_id === employee.id && result?.user?.id === employee.account?.user_id
        && ['none', 'read', 'write'].includes(access?.mode) && typeof access.enabled === 'boolean'
        && Number.isSafeInteger(access.policy_version) && access.policy_version >= 0
        && Number.isSafeInteger(access.page_revision) && access.page_revision >= 0
        && /^[a-f0-9]{64}$/.test(access.session_scope) && uniqueProviders(access.providers)
        && Array.isArray(result.providers) && uniqueProviders(result.providers.map(row => row?.id))
        && result.providers.every(row => typeof row.name === 'string' && row.name.trim());
}

export default function ProviderStatementAccessDialog(props) {
    const auth = useOptionalAuth();
    const user = auth?.user;
    const identity = JSON.stringify([props.employee.id, props.employee.account?.user_id,
        auth?.authStatus, user?.id, user?.created_by, user?.owner_id, user?.role, user?.is_owner,
        user?.web_page_access, user?.mobile_app_access, user?.permissions]);
    if (auth?.loading || auth?.authStatus !== 'authenticated' || !user) return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4" dir="rtl">
        <section role="dialog" aria-modal="true" aria-labelledby="statement-access-title" className="w-full max-w-xl rounded-2xl bg-white p-5 shadow-xl">
            <h2 id="statement-access-title" className="text-xl font-black">كشوف تسويات منصات الدفع</h2>
            <p role="status" className="my-4">يلزم التحقق من الجلسة الحالية لعرض الصلاحيات.</p>
            <button type="button" onClick={props.onClose} className="rounded-xl border px-4 py-2 font-bold">إغلاق</button>
        </section>
    </div>;
    return <StatementAccessWorkspace key={identity} {...props} />;
}

function StatementAccessWorkspace({ employee, onClose, onSaved }) {
    const [current, setCurrent] = useState(null);
    const [mode, setMode] = useState('none'), [selected, setSelected] = useState([]);
    const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
    const [error, setError] = useState(''), [conflict, setConflict] = useState(false);
    const [reload, setReload] = useState(0);
    const alive = useRef(true), lock = useRef(false), write = useRef(null);

    useEffect(() => {
        alive.current = true;
        return () => { alive.current = false; write.current?.abort(); };
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        let available = true;
        const pause = () => {
            if (visible()) return;
            available = false; controller.abort(); write.current?.abort();
            setLoading(false); setConflict(true);
            setError('توقفت العملية عند مغادرة الصفحة. أعد تحميل الصلاحيات لمعرفة آخر حالة.');
        };
        document.addEventListener('visibilitychange', pause);
        setLoading(true); setCurrent(null); setError(''); setConflict(false);
        if (!visible()) pause();
        else getProviderStatementAccess(employee.id, controller.signal).then(result => {
            if (!available || controller.signal.aborted) return;
            if (!valid(result, employee)) throw new Error('invalid_statement_access');
            setCurrent(result); setMode(result.access.mode); setSelected(result.access.providers);
        }).catch(reason => {
            if (!available || controller.signal.aborted) return;
            setError(reason?.response?.status === 403 ? 'إدارة هذه الصلاحية متاحة لمالك الحساب فقط.'
                : 'تعذّر تحميل صلاحية كشوف التسويات. أعد المحاولة.');
        }).finally(() => { if (available && !controller.signal.aborted) setLoading(false); });
        return () => {
            available = false; controller.abort();
            document.removeEventListener('visibilitychange', pause);
        };
    }, [employee.id, employee.account?.user_id, reload]);

    const availableProviders = current?.providers.map(row => row.id) || [];
    const choices = current ? [...current.providers, ...current.access.providers
        .filter(id => !availableProviders.includes(id))
        .map(id => ({ id, name: 'منصة لم تعد متاحة', unavailable: true }))] : [];
    const unavailable = selected.some(id => !availableProviders.includes(id));
    const invalidChoice = mode !== 'none' && (!selected.length || unavailable);

    async function save(event) {
        event.preventDefault();
        if (lock.current || !alive.current || !visible() || !current || conflict || loading || invalidChoice) return;
        lock.current = true; setBusy(true); setError('');
        const controller = new AbortController(); write.current = controller;
        const payload = { expected_policy_version: current.access.policy_version,
            expected_page_revision: current.access.page_revision, expected_session_scope: current.access.session_scope,
            mode, providers: mode === 'none' ? [] : [...selected] };
        try {
            const result = await saveProviderStatementAccess(employee.id, payload, controller.signal);
            if (!alive.current || controller.signal.aborted) return;
            if (!valid(result, employee) || result.access.mode !== mode
                || result.access.session_scope !== current.access.session_scope
                || result.access.policy_version !== payload.expected_policy_version + 1
                || result.access.page_revision !== payload.expected_page_revision + 1
                || !sameProviders(result.access.providers, payload.providers)) throw new Error('invalid_saved_statement_access');
            onSaved();
        } catch (reason) {
            if (!alive.current || controller.signal.aborted) return;
            setConflict(true);
            const code = reason?.response?.data?.detail?.code;
            setError(code === 'provider_statement_access_workspace_disabled'
                ? 'للموظف صلاحيات مالية أخرى موقوفة. راجع صلاحيات الأرصدة أولًا، ثم أعد تحميل هذه النافذة.'
                : reason?.response?.status === 403 ? 'لم تعد لديك صلاحية إدارة هذا الحساب. أعد فتح الصفحة.'
                : reason?.response?.status === 409 ? 'تغيّر حساب الموظف أو صلاحياته. أعد تحميل آخر نسخة قبل الحفظ.'
                : 'تعذّر تأكيد الحفظ. أعد تحميل الصلاحيات لمعرفة الحالة الحالية قبل المحاولة.');
        } finally {
            lock.current = false;
            if (write.current === controller) write.current = null;
            if (alive.current) setBusy(false);
        }
    }

    return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4" dir="rtl">
        <section role="dialog" aria-modal="true" aria-labelledby="statement-access-title" className="max-h-[90vh] w-full max-w-xl overflow-auto rounded-2xl bg-white p-5 shadow-xl">
            <div className="flex items-start justify-between gap-3"><div><h2 id="statement-access-title" className="text-xl font-black">كشوف تسويات منصات الدفع</h2><p className="mt-1 text-sm text-slate-600">{employee.name}</p></div>
                <button type="button" aria-label="إغلاق صلاحيات كشوف التسويات" disabled={busy} onClick={onClose} className="rounded-lg border px-3 py-2 font-bold">×</button></div>
            <p className="my-4 rounded-xl bg-sky-50 p-3 text-sm leading-7 text-sky-950">صلاحية مشتركة لتطبيق أماسي وموقع ميزان. اختر المنصات التي يستطيع الموظف عرض كشوفها أو رفعها. اعتماد العمليات الناقصة يحتاج صلاحيته المستقلة.</p>
            {loading && <p role="status">جارٍ تحميل الصلاحيات…</p>}
            {error && <div role="alert" className="my-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900">{error}
                <button type="button" disabled={busy || loading} className="mr-2 font-bold underline" onClick={() => setReload(value => value + 1)}>إعادة تحميل الصلاحيات</button></div>}
            {current && <form onSubmit={save} className="space-y-4">
                <label className="block font-bold">صلاحية الموظف<select aria-label="صلاحية كشوف التسويات" value={mode} disabled={busy || conflict} onChange={event => setMode(event.target.value)} className="mt-2 w-full rounded-xl border p-3">
                    <option value="none">بدون صلاحية</option><option value="read">عرض فقط</option><option value="write">عرض ورفع الكشوف</option></select></label>
                <fieldset disabled={busy || conflict || mode === 'none'} className="space-y-2 rounded-xl border p-3"><legend className="px-2 font-bold">منصات الدفع المسموحة</legend>
                    {choices.map(row => <label key={row.id} className="flex items-center gap-2 rounded-lg bg-slate-50 p-3 text-sm"><input type="checkbox" aria-label={row.name}
                        checked={selected.includes(row.id)} disabled={row.unavailable && !selected.includes(row.id)}
                        onChange={event => setSelected(values => event.target.checked ? [...values, row.id] : values.filter(value => value !== row.id))} />{row.name}</label>)}
                    {!choices.length && <p className="text-sm text-slate-500">لا توجد منصات متاحة لرفع الكشوف.</p>}
                </fieldset>
                {mode !== 'none' && !selected.length && <p className="text-sm text-amber-900">اختر منصة واحدة على الأقل.</p>}
                {mode !== 'none' && unavailable && <p className="text-sm text-amber-900">أزل المنصة غير المتاحة قبل الحفظ.</p>}
                <div className="flex justify-end gap-2"><button type="button" disabled={busy} onClick={onClose} className="rounded-xl border px-4 py-2 font-bold">إلغاء</button>
                    <button type="submit" disabled={busy || conflict || invalidChoice} className="rounded-xl bg-emerald-700 px-4 py-2 font-bold text-white disabled:opacity-50">{busy ? 'جارٍ الحفظ…' : 'حفظ الصلاحية للتطبيق وميزان'}</button></div>
            </form>}
        </section>
    </div>;
}
