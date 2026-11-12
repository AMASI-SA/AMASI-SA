import { useEffect, useRef, useState } from 'react';
import api from '../../lib/api';
import { refreshOrderActivity } from '../../services/orderActivity';
import { requestId } from './api';
import { canReadOperationalPage, visibleOperationalRead } from './visibleOperationalRead';

const base = '/operational-balances/historical-shipping-fees';

export default function HistoricalShippingFeeReview({orders = []}) {
    const [open, setOpen] = useState(false);
    const [number, setNumber] = useState('');
    const [result, setResult] = useState(null);
    const [reference, setReference] = useState('');
    const [confirmed, setConfirmed] = useState(false);
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState('');
    const lock = useRef(false);
    const pending = useRef(null);
    const generation = useRef(0), readCleanup = useRef(null), readLane = useRef({ pending: null });

    useEffect(() => () => { generation.current += 1; readCleanup.current?.(); }, []);

    const runApproval = async () => {
        if (lock.current) return;
        const current = generation.current;
        const stillCurrent = () => current === generation.current;
        readCleanup.current?.();
        lock.current = true;
        setBusy(true); setNotice('');
        // The explicit approval is durable; only read requests are aborted.
        try { await approve(stillCurrent); }
        catch (e) { if (stillCurrent()) setNotice(e.response?.data?.detail?.message || e.message || 'تعذرت المطابقة.'); }
        finally { if (stillCurrent()) { lock.current = false; setBusy(false); } }
    };
    const reset = () => { setResult(null); setReference(''); setConfirmed(false); pending.current = null; };
    const preview = refreshHistory => {
        if (lock.current || !number.trim()) return;
        const selected = number.trim(), current = generation.current;
        const stillCurrent = () => current === generation.current;
        let acquired = false;
        readCleanup.current?.();
        reset(); lock.current = true; setBusy(true); setNotice('');
        readCleanup.current = visibleOperationalRead(async config => {
            if (refreshHistory && !acquired) {
                // This explicit source-proof write may finish after departure.
                // Its result never starts another read while the page is hidden.
                await refreshOrderActivity(selected);
                acquired = true;
            }
            if (config.signal.aborted || !canReadOperationalPage()) throw new DOMException('Page is not active', 'AbortError');
            return api.post(base + '/preview', { order_number: selected }, { ...config, timeout: 30000 });
        }, {
            onData: ({ data }) => {
                if (!stillCurrent()) return;
                if (data.order_number !== selected || !Array.isArray(data.issues)) throw new Error('تعذر التحقق من مطابقة الطلب.');
                setResult(data);
            },
            onError: e => { if (stillCurrent()) setNotice(e.response?.data?.detail?.message || e.message || 'تعذرت المطابقة.'); },
            onEnd: () => { if (stillCurrent()) { lock.current = false; setBusy(false); } },
        }, readLane.current);
    };
    const approve = async stillCurrent => {
        if (!result?.can_approve || !result.preview_hash || !result.session_scope || !confirmed || !reference.trim()) return;
        const request = {order_number:result.order_number, expected_preview_hash:result.preview_hash,
            expected_session_scope:result.session_scope, evidence_ref:reference.trim(), confirm_opening_and_payouts:true};
        const key = JSON.stringify(request);
        if (pending.current?.key !== key) pending.current = {key, id:requestId()};
        const {data} = await api.post(base + '/approve', {...request, request_id:pending.current.id});
        if (!stillCurrent()) return;
        if (data.ok !== true || data.order_number !== result.order_number || !data.pending_reconciliation)
            throw new Error('تعذر التحقق من تسجيل الاعتماد. أعد المعاينة.');
        reset();
        setNotice('سُجل اعتماد الأجرة الأساسية لهذا الطلب. تظهر الأجرة عند إعادة الاحتساب إذا بقي إثبات التسليم مطابقًا.');
    };
    return <section className="ofp-no-print">
        <button className="ofp-button" type="button" disabled={busy} onClick={() => setOpen(!open)} aria-expanded={open}>مطابقة أجرة توصيل سابقة</button>
        {open && <fieldset disabled={busy} className="space-y-2 border p-3">
            <legend>مراجعة أجرة المندوب أو شركة الشحن لطلب واحد</legend>
            <label>رقم الطلب <input value={number} list="shipping-review-order-numbers" maxLength={100} onChange={e => { setNumber(e.target.value); reset(); setNotice(''); }} /></label>
            <datalist id="shipping-review-order-numbers">{orders.map(row => <option key={row.order_number} value={row.order_number}>{row.name}</option>)}</datalist>
            <button className="ofp-button" disabled={!number.trim()} onClick={() => preview(false)}>معاينة الأجرة والافتتاح والسداد</button>
            {result && <div>{result.issues.map((v,i) => <p key={i}>{v}</p>)}
                {result.already_approved && <p>سُجل اعتماد سابق لهذا الطلب.</p>}
                {result.candidate && <p>الأجرة الأساسية {result.candidate.amount} {result.candidate.currency}، الجهة {result.candidate.party_id}، وقت التسليم {result.candidate.delivered_at}. {result.candidate.waybill && 'البوليصة ' + result.candidate.waybill}</p>}
                {['openings','payouts'].map(key => <div key={key}><strong>{key === 'openings' ? 'الأرصدة الافتتاحية ذات الصلة' : 'السدادات ذات الصلة'}</strong>
                    {(result[key] || []).length ? <ul>{result[key].map((row,i) => <li key={row.id || i}>{row.name || row.kind || row.id} {row.amount} {row.currency} {row.direction === 'for_us' ? 'لنا' : row.direction === 'for_party' ? 'علينا' : ''}</li>)}</ul> : <p>لا توجد سجلات معروضة.</p>}</div>)}
                {!result.candidate && <button className="ofp-button" onClick={() => preview(true)}>جلب سجل هذا الطلب من سلة وإعادة المعاينة</button>}
                {result.can_approve && <div><label>مرجع مستند المطابقة <input value={reference} maxLength={300} onChange={e => setReference(e.target.value)} /></label>
                    <label className="block"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> راجعت الافتتاح والسدادات وتحققت أن الأجرة غير محتسبة وغير مسددة.</label>
                    <button className="ofp-button" disabled={!confirmed || reference.trim().length < 3} onClick={runApproval}>اعتماد أجرة هذا الطلب</button></div>}
            </div>}
        </fieldset>}
        {notice && <p role="status">{notice}</p>}
    </section>;
}
