import { useEffect, useRef, useState } from 'react';
import api from '../../lib/api';
import { requestId } from './api';
import { visibleOperationalRead } from './visibleOperationalRead';

const base = '/operational-balances/completed-shipping-fees';
const hash = /^[a-f0-9]{64}$/;
const money = /^\d+\.\d{2}$/;
const time = value => {
    const parsed = new Date(value);
    return value && Number.isFinite(parsed.getTime())
        ? parsed.toLocaleString('ar-SA', { timeZone: 'Asia/Riyadh', calendar: 'gregory' }) : 'غير متاح';
};

function validPreview(data, number, scope) {
    if (data?.order_number !== number || data.session_scope !== scope || !hash.test(data.preview_hash)
        || !Array.isArray(data.issues) || !Array.isArray(data.openings) || !Array.isArray(data.payouts)
        || data.issues.some(row => !row?.code || typeof row.message !== 'string')) return false;
    if (!data.candidate) return data.can_approve === false && data.effects === null;
    const c = data.candidate, e = data.effects;
    return c.order_number === number && c.order_id && c.party_type === 'courier' && c.party_id
        && c.currency === 'SAR' && money.test(c.amount) && ['prepaid', 'deferred'].includes(c.payment_mode)
        && e?.currency === c.currency && e.expense_increase === c.amount && e.opening_change === '0.00'
        && e.cash_outflow === (c.payment_mode === 'prepaid' ? c.amount : '0.00')
        && e.courier_payable_increase === (c.payment_mode === 'deferred' ? c.amount : '0.00');
}

export default function CompletedShippingFeeReview({ orders = [], sessionScope }) {
    const [open, setOpen] = useState(false), [number, setNumber] = useState('');
    const [result, setResult] = useState(null), [reference, setReference] = useState('');
    const [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false), [notice, setNotice] = useState('');
    const current = useRef({ scope: sessionScope, generation: 0 });
    const readCleanup = useRef(null), readLane = useRef({ pending: null });
    const pending = useRef(null), lock = useRef(false);
    // An identity change invalidates an in-flight preview before another render
    // can use its approval token, including when this component is not remounted.
    if (current.current.scope !== sessionScope) {
        current.current = { scope: sessionScope, generation: current.current.generation + 1 };
    }
    useEffect(() => {
        readCleanup.current?.();
        readCleanup.current = null;
        pending.current = null; lock.current = false;
        setOpen(false); setNumber(''); setResult(null); setReference(''); setConfirmed(false); setNotice(''); setBusy(false);
        return () => { current.current.generation += 1; readCleanup.current?.(); };
    }, [sessionScope]);

    const reset = () => { setResult(null); setReference(''); setConfirmed(false); pending.current = null; };
    const runApproval = async () => {
        if (lock.current || !hash.test(sessionScope || '')) return;
        const identity = { ...current.current };
        const stillCurrent = () => current.current.scope === identity.scope
            && current.current.generation === identity.generation;
        readCleanup.current?.();
        readCleanup.current = null;
        lock.current = true; setBusy(true); setNotice('');
        // Approval is an explicit idempotent write. Leaving the page only
        // invalidates its presentation; it must not abort the submitted request.
        try { await approve({ timeout: 30000 }, stillCurrent); }
        catch (error) {
            if (stillCurrent()) setNotice(error.response?.data?.detail?.message || error.message || 'تعذرت مراجعة الأجرة.');
        } finally {
            if (stillCurrent()) { lock.current = false; setBusy(false); }
        }
    };
    const preview = () => {
        if (lock.current || !hash.test(sessionScope || '')) return;
        const selected = number.trim();
        const identity = { ...current.current };
        const stillCurrent = () => current.current.scope === identity.scope
            && current.current.generation === identity.generation;
        readCleanup.current?.();
        reset(); lock.current = true; setBusy(true); setNotice('');
        readCleanup.current = visibleOperationalRead(
            config => api.post(base + '/preview', { order_number: selected }, { ...config, timeout: 30000 }), {
                onData: ({ data }) => {
                    if (!stillCurrent()) return;
                    if (!validPreview(data, selected, identity.scope)) throw new Error('تغيرت مطابقة الطلب أو الحساب؛ أعد المعاينة.');
                    setResult(data);
                },
                onError: error => {
                    if (stillCurrent()) setNotice(error.response?.data?.detail?.message || error.message || 'تعذرت مراجعة الأجرة.');
                },
                onEnd: () => { if (stillCurrent()) { lock.current = false; setBusy(false); } },
            }, readLane.current);
    };
    const approve = async (config, stillCurrent) => {
        if (!result?.can_approve || result.session_scope !== sessionScope || !confirmed || reference.trim().length < 3) return;
        const request = { order_number: result.order_number, expected_preview_hash: result.preview_hash,
            expected_session_scope: sessionScope, evidence_ref: reference.trim(), confirm_opening_and_payouts: true };
        const key = JSON.stringify(request);
        if (pending.current?.key !== key) pending.current = { key, id: requestId() };
        const { data } = await api.post(base + '/approve', { ...request, request_id: pending.current.id }, config);
        if (!stillCurrent()) return;
        if (data?.ok !== true || data.order_number !== result.order_number || data.session_scope !== sessionScope
            || data.pending_reconciliation !== true) throw new Error('تعذر التحقق من حفظ الاعتماد؛ أعد المعاينة قبل المحاولة التالية.');
        setResult(previous => ({ ...previous, can_approve: false, already_approved: true, approval_state: 'pending_reconciliation' }));
        setConfirmed(false); setReference(''); pending.current = null;
        setNotice('حُفظ اعتماد أجرة هذا الطلب. اضغط زر تحديث الأرصدة في هذه الصفحة للتحقق من الدليل والسداد وإظهار الأثر المعروض.');
    };

    return <section className="ofp-no-print" dir="rtl">
        <button className="ofp-button" type="button" disabled={busy} aria-expanded={open}
            onClick={() => setOpen(value => !value)}>مراجعة أجور تم التنفيذ</button>
        {open && <fieldset disabled={busy} className="space-y-3 border rounded p-3">
            <legend>مطابقة أجرة الشحن الأساسية لطلب واحد</legend>
            <p>الأجور السابقة التي أصبحت مؤهلة عند تم التنفيذ تحتاج مراجعة الافتتاح والسداد قبل احتسابها. لا يشترط وجود بوليصة إذا ثبت التنفيذ وهوية الشركة والتعرفة.</p>
            <label>رقم الطلب المطلوب مراجعته <input value={number} maxLength={160} list="completed-shipping-orders"
                onChange={event => { setNumber(event.target.value); reset(); setNotice(''); }} /></label>
            <datalist id="completed-shipping-orders">{Array.from(new Map(orders.filter(row => row.order_number)
                .map(row => [row.order_number, row])).values()).map(row => <option key={row.order_number} value={row.order_number}>{row.party_name || row.name}</option>)}</datalist>
            <button type="button" className="ofp-button" disabled={!number.trim() || !hash.test(sessionScope || '')}
                onClick={preview}>معاينة الأجرة وأثرها</button>
            {busy && <p role="status">جارٍ التحقق من أدلة هذا الطلب…</p>}
            {result && result.session_scope === sessionScope && <div className="space-y-3">
                {result.issues.length > 0 && <ul role="alert">{result.issues.map((row, index) => <li key={row.code + index}>{row.message}</li>)}</ul>}
                {result.candidate && <>
                    <p>الطلب {result.order_number} · {result.candidate.party_name || result.candidate.party_id} · أجرة أساسية {result.candidate.amount} {result.candidate.currency}</p>
                    <p>تم التنفيذ: {time(result.candidate.completed_at)} بتوقيت الرياض. بداية التشغيل: {time(result.period_start)}.</p>
                    <p>{result.candidate.payment_mode === 'prepaid'
                        ? `الشحن مسبق الدفع من الصندوق المعتمد: ${result.candidate.cash_account_name || 'غير متاح'}.`
                        : 'الشحن آجل؛ تُسجل الأجرة مستحقًا لشركة الشحن.'}</p>
                    <table className="ofp-table"><caption>الأثر عند اعتماد الدليل واحتسابه</caption><thead><tr><th>البند</th><th>المبلغ</th></tr></thead><tbody>
                        <tr><td>زيادة مصروف الشحن الأساسي</td><td>{result.effects.expense_increase} {result.effects.currency}</td></tr>
                        <tr><td>زيادة المستحق للشركة</td><td>{result.effects.courier_payable_increase} {result.effects.currency}</td></tr>
                        <tr><td>الخصم من الصندوق</td><td>{result.effects.cash_outflow} {result.effects.currency}</td></tr>
                        <tr><td>تغيير الرصيد الافتتاحي</td><td>{result.effects.opening_change} {result.effects.currency}</td></tr>
                    </tbody></table>
                </>}
                {['openings', 'payouts'].map(key => <div key={key}>
                    <strong>{key === 'openings' ? 'الأرصدة الافتتاحية للشركة بالعملة نفسها' : 'السدادات السابقة للشركة بالعملة نفسها'}</strong>
                    {result[key].length ? <ul>{result[key].map((row, index) => <li key={row.id || index}>
                        {row.name || row.id} · {row.amount} {row.currency} {row.direction === 'for_us' ? 'لنا' : row.direction === 'for_party' ? 'علينا' : ''}
                    </li>)}</ul> : <p>لا توجد سجلات ذات صلة معروضة في هذه المعاينة.</p>}
                </div>)}
                {result.already_approved && <p role="status">{result.approval_state === 'applied'
                    ? 'هذه الأجرة مثبتة بالفعل؛ لا يلزم اعتماد إضافي.' : 'الأجرة معتمدة؛ اضغط زر تحديث الأرصدة في هذه الصفحة لإظهار أثرها.'}</p>}
                {!result.already_approved && result.candidate && result.approval_state === 'not_required'
                    && <p>لا تحتاج هذه الأجرة اعتمادًا تاريخيًا؛ يتحقق تحديث الأرصدة داخل هذه الصفحة من الدليل الحالي.</p>}
                {result.can_approve && <div className="space-y-2">
                    <label className="block">مرجع مستند المطابقة <input value={reference} minLength={3} maxLength={300}
                        onChange={event => setReference(event.target.value)} /></label>
                    <label className="block"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /> راجعت الأرصدة الافتتاحية وكل السدادات وتحققت أن أجرة هذا الطلب غير محتسبة وغير مسددة.</label>
                    <button type="button" className="ofp-button" disabled={!confirmed || reference.trim().length < 3}
                        onClick={runApproval}>اعتماد أجرة تم التنفيذ لهذا الطلب</button>
                </div>}
            </div>}
        </fieldset>}
        {notice && <p role="status">{notice}</p>}
    </section>;
}
