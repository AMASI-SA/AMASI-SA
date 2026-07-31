import { useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    CheckCircle,
    FloppyDisk,
    SpinnerGap,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import {
    getMetaAccountSelection,
    saveMetaAccountSelection,
    startMetaReportingSync,
} from "../../services/metaIntegrationsV2";

export default function MetaReportingControl({
    integration,
    initialSelection = null,
}) {
    const [selection, setSelection] = useState(initialSelection);
    const [selectedIds, setSelectedIds] = useState(() => new Set(
        (initialSelection?.accounts || [])
            .filter((account) => account.selected)
            .map((account) => account.account_id),
    ));
    const [loading, setLoading] = useState(!initialSelection);
    const [saving, setSaving] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [lastResult, setLastResult] = useState(null);
    const [loadError, setLoadError] = useState("");

    useEffect(() => {
        if (initialSelection) return undefined;
        let active = true;
        setLoading(true);
        getMetaAccountSelection()
            .then((result) => {
                if (!active) return;
                setSelection(result);
                setSelectedIds(new Set(
                    result.accounts
                        .filter((account) => account.selected)
                        .map((account) => account.account_id),
                ));
                setLoadError("");
            })
            .catch(() => {
                if (active) setLoadError("تعذر تحميل حسابات Meta المكتشفة.");
            })
            .finally(() => {
                if (active) setLoading(false);
            });
        return () => {
            active = false;
        };
    }, [initialSelection]);

    const accounts = selection?.accounts || [];
    const selectionChanged = useMemo(() => {
        const persisted = new Set(
            accounts.filter((account) => account.selected).map((account) => account.account_id),
        );
        if (persisted.size !== selectedIds.size) return true;
        return [...selectedIds].some((accountId) => !persisted.has(accountId));
    }, [accounts, selectedIds]);
    const connected = integration?.connection_status === "connected"
        && integration?.connection_provenance === "api_connection";
    const actionEnabled = Boolean(integration?.actions?.sync_data?.enabled);
    const canSync = connected && actionEnabled && selectedIds.size > 0 && !saving && !syncing;

    function toggle(accountId) {
        setSelectedIds((current) => {
            const next = new Set(current);
            if (next.has(accountId)) next.delete(accountId);
            else next.add(accountId);
            return next;
        });
    }

    async function persistSelection() {
        if (!selectedIds.size || saving) return;
        setSaving(true);
        try {
            const result = await saveMetaAccountSelection([...selectedIds]);
            setSelection(result);
            setSelectedIds(new Set(
                result.accounts
                    .filter((account) => account.selected)
                    .map((account) => account.account_id),
            ));
            toast.success("تم حفظ حسابات Meta المحددة للمزامنة");
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error(
                (typeof detail === "string" ? detail : detail?.message)
                || "تعذر حفظ اختيار حسابات Meta.",
            );
        } finally {
            setSaving(false);
        }
    }

    async function syncSevenDays() {
        if (!canSync) return;
        setSyncing(true);
        setLastResult(null);
        try {
            const result = await startMetaReportingSync({ days: 7 });
            setLastResult(result);
            if (result.status === "complete") {
                toast.success(
                    `اكتملت مزامنة Meta: ${result.accounts_complete} حساب، ${result.rows_saved} صف يومي`,
                );
            } else if (result.status === "partial") {
                toast.warning(
                    `اكتملت مزامنة Meta جزئيًا: ${result.accounts_complete}/${result.accounts_attempted} حساب`,
                    { duration: 8000 },
                );
            } else {
                toast.error(result.error?.message || "تعذر إكمال مزامنة Meta.");
            }
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error(
                (typeof detail === "string" ? detail : detail?.message)
                || "تعذر بدء مزامنة Meta المباشرة.",
            );
        } finally {
            setSyncing(false);
        }
    }

    return (
        <section
            className="rounded-xl border border-blue-100 bg-blue-50/40 p-4"
            data-testid="meta-reporting-control"
        >
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="text-sm font-extrabold text-slate-800">
                        حسابات وتقارير Meta المباشرة
                    </div>
                    <div className="mt-1 text-[11px] leading-5 text-slate-500">
                        اختر حسابات أماسي فقط. المزامنة الأولى محدودة بسبعة أيام ولا تكتب في المحاسبة أو قيود.
                    </div>
                </div>
                <span
                    className="rounded-full border border-blue-200 bg-white px-2.5 py-1 text-[11px] font-extrabold text-blue-700"
                    aria-label={`${selectedIds.size} حساب Meta محدد`}
                >
                    {selectedIds.size} محدد
                </span>
            </div>

            {loading ? (
                <div className="mt-3 flex items-center gap-2 text-xs text-slate-500">
                    <SpinnerGap size={16} className="animate-spin" />
                    جاري تحميل الحسابات…
                </div>
            ) : loadError ? (
                <div className="mt-3 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
                    <WarningCircle size={16} weight="fill" />
                    {loadError}
                </div>
            ) : accounts.length ? (
                <div className="mt-3 space-y-2">
                    {accounts.map((account) => (
                        <label
                            key={account.account_id}
                            className="flex cursor-pointer items-start justify-between gap-3 rounded-lg border border-slate-100 bg-white px-3 py-2 text-xs"
                        >
                            <div className="flex min-w-0 items-start gap-2">
                                <input
                                    type="checkbox"
                                    checked={selectedIds.has(account.account_id)}
                                    onChange={() => toggle(account.account_id)}
                                    className="mt-1"
                                    aria-label={`اختيار ${account.display_name || account.account_id}`}
                                />
                                <div className="min-w-0">
                                    <div className="truncate font-bold text-slate-700">
                                        {account.display_name || "حساب Meta"}
                                    </div>
                                    <div className="truncate font-mono text-[10px] text-slate-400">
                                        {account.account_id}
                                    </div>
                                    {account.business_name && (
                                        <div className="truncate text-[10px] text-slate-500">
                                            {account.business_name}
                                        </div>
                                    )}
                                </div>
                            </div>
                            <div className="shrink-0 text-left font-mono text-[10px] text-slate-500">
                                <div>{account.currency || "—"}</div>
                                <div>{account.timezone || "—"}</div>
                            </div>
                        </label>
                    ))}
                </div>
            ) : (
                <div className="mt-3 rounded-lg border border-slate-100 bg-white p-3 text-xs text-slate-500">
                    لا توجد حسابات Meta مكتشفة بعد. أكمل الربط أولًا.
                </div>
            )}

            <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <button
                    type="button"
                    onClick={persistSelection}
                    disabled={!selectionChanged || !selectedIds.size || saving}
                    className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-extrabold text-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
                    data-testid="meta-save-account-selection"
                >
                    {saving ? <SpinnerGap size={16} className="animate-spin" /> : <FloppyDisk size={16} />}
                    حفظ الحسابات المحددة
                </button>
                <button
                    type="button"
                    onClick={syncSevenDays}
                    disabled={!canSync}
                    title={!canSync ? integration?.actions?.sync_data?.reason || "حدد حسابًا واحفظه أولًا" : undefined}
                    className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-blue-700 bg-blue-700 px-3 text-xs font-extrabold text-white disabled:cursor-not-allowed disabled:opacity-50"
                    data-testid="meta-reporting-sync-seven-days"
                >
                    {syncing ? <SpinnerGap size={16} className="animate-spin" /> : <ArrowClockwise size={16} />}
                    {syncing ? "جاري مزامنة Meta…" : "مزامنة 7 أيام"}
                </button>
            </div>

            {lastResult && (
                <div className={`mt-3 flex items-start gap-2 rounded-lg border p-3 text-xs ${
                    lastResult.status === "complete"
                        ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                        : lastResult.status === "partial"
                            ? "border-amber-200 bg-amber-50 text-amber-800"
                            : "border-rose-200 bg-rose-50 text-rose-800"
                }`}>
                    <CheckCircle size={17} className="mt-0.5 shrink-0" weight="fill" />
                    <div>
                        <div className="font-extrabold">{lastResult.status}</div>
                        <div className="mt-1">
                            {lastResult.accounts_complete}/{lastResult.accounts_attempted} حساب، {lastResult.rows_saved} صف، {lastResult.errors_count} ملاحظة
                        </div>
                    </div>
                </div>
            )}
        </section>
    );
}
