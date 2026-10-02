import { useState } from "react";
import api from "../../../lib/api";
import { ADVERTISING_BASE } from "./advertisingAdapter";

export default function AdvertisingZeroOpening({ account, onConfirmed }) {
    const [original, setOriginal] = useState("");
    const [group, setGroup] = useState("");
    const [at, setAt] = useState("");
    const [evidence, setEvidence] = useState("");
    const [confirmed, setConfirmed] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const valid = account.platform && account.integration_account_id && account.wallet_binding && account.currency !== "SAR" &&
        /^0(?:\.0+)?$/.test(original) && group.trim() && evidence.trim().length >= 3 && confirmed &&
        /^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(at) && Number.isFinite(Date.parse(at));
    async function submit(event) {
        event.preventDefault();
        if (!valid || busy) return;
        setBusy(true); setError("");
        try {
            const { data } = await api.post(`${ADVERTISING_BASE}/wallet-opening-evidence`, {
                platform: account.platform, integration_account_id: account.integration_account_id,
                currency: account.currency, original_currency_amount: original, opening_sar_amount: "0.00",
                zero_original_confirmed: true, opening_txn_group_id: group.trim(), effective_at: at,
                evidence: evidence.trim(),
            });
            if (!data?.id || data.zero_original_confirmed !== true || !data.confirmed_by) throw new Error("zero_confirmation_missing");
            onConfirmed();
        } catch (failure) { setError(failure?.response?.data?.detail?.code || "تعذر تأكيد دليل افتتاح المحفظة الصفري"); }
        finally { setBusy(false); }
    }
    return <form aria-label="تأكيد افتتاح المحفظة الصفري" className="ac-filters" onSubmit={submit}>
        <h4>محفظة بدأت برصيد أصلي صفري</h4>
        <p>هذه الخطوة توثق كشف المحفظة عند الافتتاح المعتمد؛ لا تنشئ قيد افتتاح ولا تفترض الرصيد الأصلي من رصيد الريال. الافتتاح الموجب يحتاج إثبات سعر الصرف المعتاد.</p>
        <label>الرصيد الأصلي المثبت ({account.currency})<input value={original} inputMode="decimal" onChange={e => setOriginal(e.target.value)} /></label>
        <label>معرف قيد الافتتاح المعتمد<input value={group} onChange={e => setGroup(e.target.value)} /></label>
        <label>توقيت الافتتاح المعتمد مع المنطقة الزمنية<input value={at} placeholder="YYYY-MM-DDTHH:mm:ss+03:00" onChange={e => setAt(e.target.value)} /></label>
        <label>مرجع كشف المحفظة الأصلي المثبت للصفر<input value={evidence} onChange={e => setEvidence(e.target.value)} /></label>
        <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />أؤكد أن المصدر يثبت صفر {account.currency} وأن المحفظة مسجلة صراحة بصفر في الافتتاح المعتمد.</label>
        <button disabled={!valid || busy} type="submit">تأكيد دليل الصفر الأصلي</button>
        {error && <p role="alert">{error}</p>}
    </form>;
}
