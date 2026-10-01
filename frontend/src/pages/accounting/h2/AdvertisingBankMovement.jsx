import { useEffect, useState } from "react";
import api from "../../../lib/api";
import { ADVERTISING_BASE } from "./advertisingAdapter";

const eligible = row => row?.id && row.bank_account_id && row.currency === "SAR" &&
    row.direction === "out" && row.status === "unclassified" &&
    ["bank_statement_import", "manual_reconciled_bank_statement"].includes(row.source) &&
    row.file_id && row.file_hash && /^\d{4}-\d{2}-\d{2}$/.test(row.movement_date) &&
    ![row.receipt_id, row.accounting_event_id, row.confirmed_provider, row.explicit_provider, row.suggested_provider].some(Boolean);

export default function AdvertisingBankMovement({ account }) {
    const [rows, setRows] = useState([]);
    const [kind, setKind] = useState("");
    const [movementId, setMovementId] = useState("");
    const [feeId, setFeeId] = useState("");
    const [original, setOriginal] = useState("");
    const [fx, setFx] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [posted, setPosted] = useState("");
    const [loading, setLoading] = useState(true);
    useEffect(() => {
        let active = true;
        api.get("/accounting-module/daily-movements", { params: { status: "unclassified", limit: 500 } }).then(({ data }) => {
            if (active) {
                if (Array.isArray(data?.items)) setRows(data.items.filter(eligible));
                else setError("تعذر التحقق من قائمة حركات البنك");
                setLoading(false);
            }
        }, failure => { if (active) { setError(failure?.response?.data?.detail?.code || "تعذر تحميل حركات كشف البنك"); setLoading(false); } });
        return () => { active = false; };
    }, [account]);
    const movement = rows.find(row => row.id === movementId);
    const fee = rows.find(row => row.id === feeId);
    const foreign = kind === "wallet_funding" && account.currency !== "SAR";
    const binding = kind === "wallet_funding" ? account.wallet_binding : kind === "payable_settlement" ? account.payable_binding : null;
    const valid = account.platform && account.integration_account_id && binding && movement && (!feeId || (fee && fee.id !== movement.id && fee.bank_account_id === movement.bank_account_id && fee.movement_date === movement.movement_date)) &&
        (!foreign || (/^\d+(\.\d+)?$/.test(original) && Number(original) > 0 && /^[0-9a-f]{64}$/.test(fx)));
    async function submit(event) {
        event.preventDefault();
        if (!valid || busy || posted) return;
        setBusy(true); setError("");
        const payload = { platform: account.platform, integration_account_id: account.integration_account_id,
            kind, bank_financial_account_id: movement.bank_account_id, bank_evidence_id: movement.id,
            amount_sar: String(movement.amount), effective_at: `${movement.movement_date}T00:00:00+03:00` };
        if (fee) Object.assign(payload, { bank_fee_sar: String(fee.amount), bank_fee_evidence: fee.id });
        if (foreign) Object.assign(payload, { original_wallet_currency_amount: original, wallet_currency: account.currency, fx_snapshot_id: fx });
        try {
            const { data } = await api.post(`${ADVERTISING_BASE}/bank-movement`, payload);
            if (!["posted", "already_posted"].includes(data?.status) || !data.txn_group_id) throw new Error("posting_proof_missing");
            setPosted(data.txn_group_id);
        } catch (failure) { setError(failure?.response?.data?.detail?.code || "تعذر ترحيل الحركة البنكية"); }
        finally { setBusy(false); }
    }
    return <form onSubmit={submit} aria-label="تمويل وتسوية الإعلان" className="ac-filters">
        <p>اختر حركة من كشف البنك المستورد. يتحقق النظام من أصل الملف قبل الترحيل. التمويل والتسوية لا يكرران مصروف الإعلان.</p>
        {loading && <p>جاري تحميل حركات البنك</p>}
        <label>نوع الحركة<select value={kind} onChange={e => { setKind(e.target.value); setPosted(""); }}>
            <option value="">اختر العملية</option>
            {account.wallet_binding && <option value="wallet_funding">تمويل المحفظة</option>}
            {account.payable_binding && <option value="payable_settlement">سداد المستحق</option>}
        </select></label>
        <label>حركة البنك المستوردة<select value={movementId} onChange={e => { setMovementId(e.target.value); setFeeId(""); setPosted(""); }}>
            <option value="">اختر حركة مثبتة بكشف البنك</option>
            {rows.map(row => <option key={row.id} value={row.id}>{row.movement_date} · {row.amount} SAR · {row.bank_account_name || row.bank_account_id} · {row.reference || row.id}</option>)}
        </select></label>
        {!loading && !rows.length && <p>لا توجد حركات صادرة مستوردة متاحة؛ استورد كشف البنك أولًا.</p>}
        <label>حركة الرسوم البنكية المستقلة (اختياري)<select value={feeId} onChange={e => setFeeId(e.target.value)}>
            <option value="">بدون رسوم</option>
            {rows.filter(row => movement && row.id !== movement.id && row.bank_account_id === movement.bank_account_id && row.movement_date === movement.movement_date).map(row => <option key={row.id} value={row.id}>{row.amount} SAR · {row.reference || row.id}</option>)}
        </select></label>
        {foreign && <><label>المبلغ الفعلي بعملة المحفظة ({account.currency})<input value={original} onChange={e => setOriginal(e.target.value)} inputMode="decimal" /></label>
            <label>معرف إثبات سعر الصرف المؤكد<input value={fx} onChange={e => setFx(e.target.value.trim())} /></label></>}
        <button type="submit" disabled={!valid || busy || Boolean(posted)}>ترحيل الحركة المثبتة</button>
        {error && <p role="alert">{error}</p>}
        {posted && <p role="status">تم ترحيل الحركة · <bdi>{posted}</bdi></p>}
    </form>;
}
