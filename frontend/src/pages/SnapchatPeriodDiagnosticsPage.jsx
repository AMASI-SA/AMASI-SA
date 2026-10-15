import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import SnapchatPeriodDiagnostic from "../components/marketing/SnapchatPeriodDiagnostic";
import api from "../lib/api";

export default function SnapchatPeriodDiagnosticsPage() {
    const [account, setAccount] = useState(null);
    const [error, setError] = useState("");
    const [from, setFrom] = useState("");
    const [to, setTo] = useState("");
    useEffect(() => {
        let active = true;
        api.get("/integrations-v2/snapchat-v2/status").then(({ data }) => {
            if (!active) return;
            setAccount(data.selected_account || null);
            if (!data.selected_account) setError("اختر حساب سناب أولًا من صفحة سناب.");
        }).catch(() => { if (active) setError("تعذر قراءة الحساب. هذه الصفحة متاحة لمالك المتجر فقط."); });
        return () => { active = false; };
    }, []);
    return <main dir="rtl" className="space-y-4 p-4">
        <h1 className="text-xl font-bold">تشخيص اتساق فترات طلبات سناب</h1>
        <Link to="/snapchat-accounts" className="text-emerald-700 underline">العودة إلى سناب</Link>
        {error && <p role="alert">{error}</p>}
        {account && <>
            <p>الحساب المعتمد: {account.name || account.ad_account_id}</p>
            <div className="flex flex-wrap gap-4">
                <label>من <input aria-label="من" type="date" value={from} onChange={event => setFrom(event.target.value)} className="rounded border p-2" /></label>
                <label>إلى <input aria-label="إلى" type="date" value={to} onChange={event => setTo(event.target.value)} className="rounded border p-2" /></label>
            </div>
            {from && to && from <= to && <SnapchatPeriodDiagnostic key={`${account.ad_account_id}:${from}:${to}`}
                accountId={account.ad_account_id} dateFrom={from} dateTo={to} />}
        </>}
    </main>;
}
