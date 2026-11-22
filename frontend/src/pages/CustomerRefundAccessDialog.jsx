import { useEffect, useRef, useState } from 'react';
import { getCustomerRefundAccess, saveCustomerRefundAccess } from '../services/customerRefundAccess';

const sourceKey = row => `${row.kind}:${row.id}`;
const sourceValid = row => ['bank', 'provider'].includes(row?.kind) && typeof row.id === 'string' && row.id.length > 0;
function valid(result, employee) {
    const access = result?.access;
    return result?.employee_id === employee.id && result?.user?.id === employee.account?.user_id
        && ['none', 'read', 'write'].includes(access?.mode) && typeof access.enabled === 'boolean'
        && Number.isInteger(access.policy_version) && access.policy_version >= 0
        && Number.isInteger(access.page_revision) && access.page_revision >= 0
        && /^[a-f0-9]{64}$/.test(access.session_scope)
        && Array.isArray(access.sources) && access.sources.every(sourceValid)
        && Array.isArray(result.sources) && result.sources.every(row => sourceValid(row) && row.currency === 'SAR' && typeof row.name === 'string');
}

export default function CustomerRefundAccessDialog({ employee, onClose, onSaved }) {
    const [current, setCurrent] = useState(null);
    const [mode, setMode] = useState('none');
    const [selected, setSelected] = useState([]);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [conflict, setConflict] = useState(false);
    const [reload, setReload] = useState(0);
    const lock = useRef(false);
    const mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    useEffect(() => {
        const controller = new AbortController();
        setLoading(true); setCurrent(null); setError(''); setConflict(false);
        getCustomerRefundAccess(employee.id, controller.signal).then(result => {
            if (controller.signal.aborted) return;
            if (!valid(result, employee)) throw new Error('invalid refund access');
            setCurrent(result); setMode(result.access.mode); setSelected(result.access.sources.map(sourceKey));
        }).catch(reason => {
            if (!controller.signal.aborted) setError(reason?.response?.status === 403
                ? 'إدارة هذه الصلاحية متاحة لمالك الحساب فقط.' : 'تعذّر تحميل صلاحيات استرجاع المبالغ. أعد المحاولة.');
        }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [employee.id, employee.account?.user_id, reload]);

    const choices = current ? [...current.sources, ...current.access.sources
        .filter(row => !current.sources.some(source => sourceKey(source) === sourceKey(row)))
        .map(row => ({ ...row, name: 'مصدر لم يعد متاحًا', available: false }))] : [];
    const unavailable = choices.some(row => row.available === false && selected.includes(sourceKey(row)));
    async function save(event) {
        event.preventDefault();
        if (lock.current || !current || conflict) return;
        lock.current = true; setBusy(true); setError('');
        const payload = {
            expected_policy_version: current.access.policy_version,
            expected_page_revision: current.access.page_revision,
            expected_session_scope: current.access.session_scope,
            mode, sources: choices.filter(row => selected.includes(sourceKey(row))).map(({ kind, id }) => ({ kind, id })),
        };
        try {
            const result = await saveCustomerRefundAccess(employee.id, payload);
            if (!valid(result, employee) || result.access.mode !== mode
                || result.access.policy_version !== payload.expected_policy_version + 1
                || result.access.page_revision !== payload.expected_page_revision + 1) throw new Error('invalid saved refund access');
            if (mounted.current) onSaved();
        } catch (reason) {
            if (!mounted.current) return;
            const code = reason?.response?.data?.detail?.code;
            if (code === 'customer_refund_workspace_disabled') {
                setConflict(true);
                setError('للموظف صلاحيات مالية أخرى موقوفة. فعّلها صراحة من صلاحيات الأرصدة أولًا، ثم أعد تحميل هذه النافذة.');
            } else if (reason?.response?.status === 409) {
                setConflict(true); setError('تغيّر حساب الموظف أو صلاحياته. أعد تحميل آخر نسخة قبل الحفظ.');
            } else if (reason?.response?.status === 403) {
                setConflict(true); setError('لم تعد لديك صلاحية إدارة هذا الحساب. أعد فتح الصفحة.');
            } else {
                setConflict(true);
                setError('تعذّر تأكيد الحفظ. أعد تحميل الصلاحيات لمعرفة الحالة الحالية قبل المحاولة.');
            }
        } finally {
            lock.current = false;
            if (mounted.current) setBusy(false);
        }
    }
    return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4" dir="rtl">
        <section role="dialog" aria-modal="true" aria-labelledby="customer-refund-access-title" className="max-h-[90vh] w-full max-w-xl overflow-auto rounded-2xl bg-white p-5 shadow-xl">
            <div className="flex items-start justify-between gap-3"><div><h2 id="customer-refund-access-title" className="text-xl font-black">استرجاع المبالغ للعملاء</h2><p className="mt-1 text-sm text-slate-600">{employee.name}</p></div>
                <button type="button" aria-label="إغلاق صلاحيات الاسترجاع" disabled={busy} onClick={onClose} className="rounded-lg border px-3 py-2 font-bold">×</button></div>
            <p className="my-4 rounded-xl bg-sky-50 p-3 text-sm leading-7 text-sky-950">صلاحية مشتركة لتطبيق أماسي وموقع ميزان. المصادر المختارة تستخدم لاسترجاع المبالغ فقط.</p>
            {loading && <p role="status">جارٍ تحميل الصلاحيات…</p>}
            {error && <div role="alert" className="my-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900">{error}
                <button type="button" disabled={busy || loading} className="mr-2 font-bold underline" onClick={() => setReload(value => value + 1)}>إعادة تحميل الصلاحيات</button></div>}
            {current && <form onSubmit={save} className="space-y-4">
                <label className="block font-bold">صلاحية الموظف<select aria-label="صلاحية استرجاع المبالغ" value={mode} disabled={busy || conflict} onChange={event => setMode(event.target.value)} className="mt-2 w-full rounded-xl border p-3">
                    <option value="none">بدون صلاحية</option><option value="read">عرض فقط</option><option value="write">عرض وتسجيل</option></select></label>
                <fieldset disabled={busy || conflict || mode === 'none'} className="space-y-2 rounded-xl border p-3"><legend className="px-2 font-bold">البنوك ومنصات الدفع المسموحة</legend>
                    {choices.map(row => <label key={sourceKey(row)} className="flex items-center gap-2 rounded-lg bg-slate-50 p-3 text-sm"><input type="checkbox" aria-label={row.name}
                        checked={selected.includes(sourceKey(row))} disabled={row.available === false && !selected.includes(sourceKey(row))}
                        onChange={event => setSelected(values => event.target.checked ? [...values, sourceKey(row)] : values.filter(value => value !== sourceKey(row)))} />
                        <span>{row.name}<small className="mr-2 text-slate-500">{row.kind === 'bank' ? 'بنك' : 'منصة دفع'}</small></span></label>)}
                    {!choices.length && <p className="text-sm text-slate-500">لا توجد مصادر استرجاع مكتملة الإعداد.</p>}
                </fieldset>
                {mode !== 'none' && !selected.length && <p className="text-sm text-amber-900">اختر بنكًا أو منصة واحدة على الأقل.</p>}
                {mode !== 'none' && unavailable && <p className="text-sm text-amber-900">أزل المصدر غير المتاح قبل الحفظ.</p>}
                <div className="flex justify-end gap-2"><button type="button" disabled={busy} onClick={onClose} className="rounded-xl border px-4 py-2 font-bold">إلغاء</button>
                    <button type="submit" disabled={busy || conflict || mode !== 'none' && (!selected.length || unavailable)} className="rounded-xl bg-emerald-700 px-4 py-2 font-bold text-white disabled:opacity-50">{busy ? 'جارٍ الحفظ…' : 'حفظ الصلاحية للتطبيق وميزان'}</button></div>
            </form>}
        </section>
    </div>;
}
