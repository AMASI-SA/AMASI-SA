import React, { useRef, useState } from "react";
import { operationalApi as api, messageFor, requestId } from "./api";

const providers = { salla: "سلة", tamara: "تمارا", tabby: "تابي", emkan: "إمكان" };

export default function ProviderHistoryReview({ sessionScope }) {
    const [items, setItems] = useState(null), [order, setOrder] = useState(""), [statement, setStatement] = useState("");
    const [file, setFile] = useState(null), [confirmed, setConfirmed] = useState(false), [message, setMessage] = useState(""), [busy, setBusy] = useState(false);
    const intent = useRef(null);
    const selected = items?.find(r => r.order_id === order);
    async function load() {
        const result = await api.providerHistoryReviews();
        if (result.session_scope !== sessionScope) throw new Error("scope");
        setItems(result.items); setOrder(""); setStatement(""); setFile(null); setConfirmed(false);
    }
    async function read() {
        setBusy(true); setMessage("");
        try { await load(); } catch (e) { setItems(null); setMessage(messageFor(e)); } finally { setBusy(false); }
    }
    async function save(e) {
        e.preventDefault();
        if (busy || !file || !confirmed || !selected || selected.blocks.length || selected.awaiting_refresh) return;
        setBusy(true); setMessage("");
        try {
            const receipt = await api.receipt(file);
            const context = JSON.stringify([selected.preview_hash, receipt.id, statement, sessionScope]);
            if (intent.current?.context !== context) intent.current = { context, id: requestId() };
            await api.approveProviderHistory({ request_id: intent.current.id, order_id: order, expected_preview_hash: selected.preview_hash,
                expected_session_scope: sessionScope, evidence_id: receipt.id, statement_reference: statement, confirmed_outstanding: true });
            setItems(prev => prev.map(r => r.order_id === order ? { ...r, awaiting_refresh: true } : r));
            setMessage("حُفظت المطابقة. سيتحقق الاحتساب التالي من إثبات الطلب والسداد قبل إضافة المستحق.");
        } catch (err) { setMessage(messageFor(err)); } finally { setBusy(false); }
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
