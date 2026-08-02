import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    CheckCircle,
    WarningCircle,
} from "@phosphor-icons/react";
import {
    getGoogleAdsReportingReadiness,
    saveGoogleAdsAccountSelection,
    syncGoogleAdsReporting,
} from "../../services/googleAdsIntegrationsV2";

function errorMessage(error) {
    return (
        error?.response?.data?.detail?.message
        || error?.response?.data?.message
        || error?.message
        || "تعذر تنفيذ العملية."
    );
}

export default function GoogleAdsReportingControl() {
    const [readiness, setReadiness] = useState(null);
    const [selected, setSelected] = useState([]);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState("");
    const [error, setError] = useState("");

    const load = useCallback(async () => {
        setError("");
        try {
            const value = await getGoogleAdsReportingReadiness();
            setReadiness(value);
            setSelected(
                (value?.selection?.accounts || [])
                    .filter((account) => account.selected)
                    .map((account) => account.account_id),
            );
        } catch (requestError) {
            setError(errorMessage(requestError));
        }
    }, []);

    useEffect(() => {
        load();
    }, [load]);

    const accounts = readiness?.selection?.accounts || [];
    const changed = useMemo(() => {
        const stored = accounts
            .filter((account) => account.selected)
            .map((account) => account.account_id)
            .sort();
        return stored.join(",") !== [...selected].sort().join(",");
    }, [accounts, selected]);

    function toggle(accountId) {
        setMessage("");
        setError("");
        setSelected((current) => (
            current.includes(accountId)
                ? current.filter((value) => value !== accountId)
                : [...current, accountId]
        ));
    }

    async function saveSelection() {
        if (!selected.length) {
            setError("اختر حساب Google Ads واحدًا على الأقل.");
            return;
        }
        setBusy(true);
        setError("");
        setMessage("");
        try {
            await saveGoogleAdsAccountSelection(selected);
            setMessage("تم حفظ حسابات Google Ads المختارة داخل ميزان.");
            await load();
        } catch (requestError) {
            setError(errorMessage(requestError));
        } finally {
            setBusy(false);
        }
    }

    async function sync() {
        setBusy(true);
        setError("");
        setMessage("");
        try {
            const result = await syncGoogleAdsReporting(7);
            setMessage(
                `اكتملت مزامنة 7 أيام وحُفظ ${Number(result?.rows_saved || 0).toLocaleString("en-US")} صف.`,
            );
            await load();
        } catch (requestError) {
            setError(errorMessage(requestError));
        } finally {
            setBusy(false);
        }
    }

    return (
        <section
            className="rounded-xl border border-emerald-100 bg-emerald-50/50 p-3"
            data-testid="google-ads-reporting-control"
        >
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                    <div className="text-sm font-black text-slate-900">
                        تقارير Google Ads الأصلية
                    </div>
                    <div className="mt-1 text-[11px] font-semibold leading-5 text-slate-600">
                        قراءة GAQL فقط، وتحديث تلقائي كل 5 دقائق بعد التفعيل.
                    </div>
                </div>
                <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[10px] font-black ${
                    readiness?.ready
                        ? "border-emerald-200 bg-emerald-100 text-emerald-800"
                        : "border-amber-200 bg-amber-50 text-amber-700"
                }`}>
                    {readiness?.ready
                        ? <CheckCircle size={14} weight="fill" />
                        : <WarningCircle size={14} weight="fill" />}
                    {readiness?.ready ? "جاهز" : "غير جاهز"}
                </span>
            </div>

            {accounts.length > 0 && (
                <div className="mt-3 space-y-2" data-testid="google-ads-account-selection">
                    {accounts.map((account) => (
                        <label
                            key={account.account_id}
                            className="flex cursor-pointer items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700"
                        >
                            <input
                                type="checkbox"
                                checked={selected.includes(account.account_id)}
                                onChange={() => toggle(account.account_id)}
                                disabled={busy}
                            />
                            <span className="min-w-0 flex-1 truncate">
                                {account.display_name || `Google Ads ${account.account_id}`}
                            </span>
                            <span className="font-mono text-[10px] text-slate-400">
                                {account.account_id}
                            </span>
                        </label>
                    ))}
                </div>
            )}

            {accounts.length === 0 && readiness && (
                <p className="mt-3 rounded-lg border border-amber-200 bg-white px-3 py-2 text-xs font-bold text-amber-700">
                    اربط Google واكتشف حساب Google Ads قبل تفعيل التقارير.
                </p>
            )}

            <div className="mt-3 flex flex-wrap gap-2">
                <button
                    type="button"
                    onClick={saveSelection}
                    disabled={busy || !changed || !selected.length}
                    className="rounded-lg border border-emerald-200 bg-white px-3 py-2 text-xs font-black text-emerald-800 disabled:cursor-not-allowed disabled:opacity-50"
                    data-testid="google-ads-save-selection"
                >
                    حفظ الحسابات
                </button>
                <button
                    type="button"
                    onClick={sync}
                    disabled={busy || !readiness?.ready || changed}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-xs font-black text-white disabled:cursor-not-allowed disabled:opacity-50"
                    data-testid="google-ads-reporting-sync"
                >
                    <ArrowClockwise size={15} className={busy ? "animate-spin" : ""} />
                    مزامنة 7 أيام
                </button>
            </div>

            {message && (
                <p className="mt-3 text-xs font-bold text-emerald-700" role="status">
                    {message}
                </p>
            )}
            {error && (
                <p className="mt-3 text-xs font-bold text-rose-700" role="alert">
                    {error}
                </p>
            )}
        </section>
    );
}
