import React, { useEffect, useRef, useState } from "react";
import { operationalApi as api, messageFor, requestId } from "./api";
import { visibleOperationalRead } from './visibleOperationalRead';

const providers = { salla: "سلة", tamara: "تمارا", tabby: "تابي", emkan: "إمكان" };

export default function ProviderHistoryReview({ sessionScope }) {
    const [items, setItems] = useState(null), [order, setOrder] = useState(""), [statement, setStatement] = useState("");
    const [file, setFile] = useState(null), [confirmed, setConfirmed] = useState(false), [message, setMessage] = useState(""), [busy, setBusy] = useState(false);
    const intent = useRef(null);
    const lock = useRef(false), readCleanup = useRef(null), readLane = useRef({ pending: null });
    const identity = useRef({ scope: sessionScope, generation: 0 });
    if (identity.current.scope !== sessionScope) identity.current = { scope: sessionScope, generation: identity.current.generation + 1 };
    useEffect(() => {
        readCleanup.current?.(); intent.current = null; lock.current = false;
        setItems(null); setOrder(''); setStatement(''); setFile(null); setConfirmed(false); setMessage(''); setBusy(false);
        return () => { identity.current.generation += 1; readCleanup.current?.(); };
    }, [sessionScope]);
    const current = value => value.scope === identity.current.scope && value.generation === identity.current.generation;
    const selected = items?.find(r => r.order_id === order);
    function read() {
        if (lock.current) return;
        const owner = { ...identity.current };
        readCleanup.current?.(); lock.current = true;
        setBusy(true); setMessage('');
        readCleanup.current = visibleOperationalRead(config => api.providerHistoryReviews(config), {
            onData: result => {
                if (!current(owner)) return;
                if (result.session_scope !== owner.scope || !Array.isArray(result.items)) throw new Error('scope');
                setItems(result.items); setOrder(''); setStatement(''); setFile(null); setConfirmed(false);
            },
            onError: e => { if (current(owner)) { setItems(null); setMessage(messageFor(e)); } },
            onEnd: () => { if (current(owner)) { lock.current = false; setBusy(false); } },
        }, readLane.current);
    }
    async function save(e) {
        e.preventDefault();
        if (lock.current || !file || !confirmed || !selected || selected.blocks.length || selected.awaiting_refresh) return;
        const owner = { ...identity.current };
        readCleanup.current?.(); lock.current = true;
        setBusy(true); setMessage('');
        try {
            const receipt = await api.receipt(file);
            const context = JSON.stringify([selected.preview_hash, receipt.id, statement, sessionScope]);
            const approval = intent.current?.context === context ? intent.current : { context, id: requestId() };
            if (current(owner)) intent.current = approval;
            // Receipt and approval are one explicit submission, not a read.
            await api.approveProviderHistory({ request_id: approval.id, order_id: order, expected_preview_hash: selected.preview_hash,
                expected_session_scope: sessionScope, evidence_id: receipt.id, statement_reference: statement, confirmed_outstanding: true });
            if (!current(owner)) return;
            setItems(prev => prev.map(r => r.order_id === order ? { ...r, awaiting_refresh: true } : r));
            setMessage("حُفظت المطابقة. سيتحقق الاحتساب التالي من إثبات الطلب والسداد قبل إضافة المستحق.");
        } catch (err) { if (current(owner)) setMessage(messageFor(err)); }
        finally { if (current(owner)) { lock.current = false; setBusy(false); } }
    }
    return <section dir="rtl" className="ofp-no-print">
        <button className="ofp-button" type="button" disabled={busy} onClick={read}>مراجعة مستحقات الطلبات السابقة لدى المنصات</button>
        {items && <div className="border rounded p-3 space-y-2">
            {!items.length ? <p>لا توجد طلبات بإثبات محفوظ يحتاج هذه المطابقة في آخر احتساب.</p> : <label>الطلب <select aria-label="الطلب المطلوب مطابقته" disabled={busy} value={order} onChange={e => { setOrder(e.target.value); setConfirmed(false); setStatement(""); setFile(null); setMessage(""); }}>
                <option value="">اختر طلبًا</option>{items.map(r => <option key={r.order_id} value={r.order_id}>{r.order_number} · {providers[r.provider]} · {r.paid} {r.currency}</option>)}</select></label>}
            {selected && <div key={selected.order_id}><p>{providers[selected.provider]} · دفع العميل {selected.paid} {selected.currency} · المتبقي على العميل {selected.remaining} {selected.currency}</p>
                <p>المبلغ محل المطابقة {selected.paid} {selected.currency}. الافتتاحي {selected.openings.map(o => `${o.amount} ${selected.currency} ${o.direction === "for_us" ? "لنا" : "علينا"}`).join("، ") || "لا يوجد"}. حركات المنصة المحفوظة بهذه العملة {selected.movement_count}.</p>
                {selected.awaiting_refresh ? <p>مطابقة معتمدة بانتظار الاحتساب.</p> : selected.blocks.length ? <ul>{selected.blocks.map(s => <li key={s}>{s}</li>)}</ul> : <form onSubmit={save}><fieldset disabled={busy} className="space-y-2">
                    <label className="block">مرجع سطر الطلب في كشف المنصة <input required minLength={3} maxLength={250} value={statement} onChange={e => setStatement(e.target.value)} /></label>
                    <label className="block">كشف المنصة الذي يثبت أن المبلغ لا يزال مستحقًا <input required type="file" accept="application/pdf,image/png,image/jpeg" onChange={e => setFile(e.target.files[0])} /></label>
                    <label className="block"><input required type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> طابقت هذا الطلب والمبلغ مع الكشف وتأكدت أنه خارج الرصيد الافتتاحي وكل سداد سابق.</label>
                    <button type="submit" className="ofp-button" disabled={!confirmed || !file || statement.trim().length < 3}>اعتماد مطابقة هذا الطلب</button>
                </fieldset></form>}</div>}
        </div>}
        {message && <p role="status">{message}</p>}
    </section>;
}
