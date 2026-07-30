import { useEffect, useMemo, useState } from "react";
import {
    CheckCircle,
    CurrencyCircleDollar,
    SpinnerGap,
    WarningCircle,
} from "@phosphor-icons/react";
import {
    getSnapchatAccountSelection,
    getSnapchatSelectedPerformanceSummary,
} from "../../services/snapchatIntegrationsV2";

function accountId(account = {}) {
    return String(
        account.account_id
        || account.external_account_id
        || account.ad_account_id
        || "",
    ).trim();
}

function formatNumber(value, digits = 2) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "0.00";
    return new Intl.NumberFormat("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
    }).format(parsed);
}

function formatDateRange(summary) {
    if (!summary?.date_from) return "اليوم";
    if (!summary.date_to || summary.date_to === summary.date_from) {
        return summary.date_from;
    }
    return `${summary.date_from} — ${summary.date_to}`;
}

function mergeAccounts(accounts, selection, summary) {
    const rows = new Map();
    for (const account of accounts || []) {
        const id = accountId(account);
        if (!id) continue;
        rows.set(id, { ...account, account_id: id, selected: false });
    }
    for (const account of selection?.accounts || []) {
        const id = accountId(account);
        if (!id) continue;
        rows.set(id, {
            ...(rows.get(id) || {}),
            ...account,
            account_id: id,
            selected: account.selected === true,
        });
    }
    for (const id of summary?.selected_account_ids || []) {
        const clean = String(id || "").trim();
        if (!clean) continue;
        rows.set(clean, {
            ...(rows.get(clean) || {}),
            account_id: clean,
            selected: true,
        });
    }
    return [...rows.values()].sort((left, right) => {
        if (left.selected !== right.selected) return left.selected ? -1 : 1;
        return String(left.display_name || left.account_id).localeCompare(
            String(right.display_name || right.account_id),
            "ar",
        );
    });
}

export default function SnapchatAccountScope({
    accounts = [],
    initialSelection = null,
    initialSummary = null,
}) {
    const [selection, setSelection] = useState(initialSelection);
    const [summary, setSummary] = useState(initialSummary);
    const [loading, setLoading] = useState(!initialSelection || !initialSummary);
    const [loadError, setLoadError] = useState(false);

    useEffect(() => {
        if (initialSelection && initialSummary) return undefined;
        let active = true;
        setLoading(true);
        Promise.allSettled([
            initialSelection
                ? Promise.resolve(initialSelection)
                : getSnapchatAccountSelection(),
            initialSummary
                ? Promise.resolve(initialSummary)
                : getSnapchatSelectedPerformanceSummary(),
        ]).then(([selectionResult, summaryResult]) => {
            if (!active) return;
            if (selectionResult.status === "fulfilled") {
                setSelection(selectionResult.value);
            }
            if (summaryResult.status === "fulfilled") {
                setSummary(summaryResult.value);
            }
            setLoadError(
                selectionResult.status === "rejected"
                || summaryResult.status === "rejected",
            );
            setLoading(false);
        });
        return () => {
            active = false;
        };
    }, [initialSelection, initialSummary]);

    const mergedAccounts = useMemo(
        () => mergeAccounts(accounts, selection, summary),
        [accounts, selection, summary],
    );
    const selectedAccounts = mergedAccounts.filter((account) => account.selected);
    const selectedCount = Math.max(
        Number(selection?.selected_count || 0),
        Number(summary?.selected_account_count || 0),
        selectedAccounts.length,
    );
    const unselectedCount = Math.max(0, mergedAccounts.length - selectedAccounts.length);
    const performanceByAccount = new Map(
        (summary?.accounts || []).map((account) => [accountId(account), account]),
    );

    return (
        <section
            className="mt-3 rounded-xl border border-emerald-100 bg-emerald-50/40 p-3"
            data-testid="snapchat-account-scope"
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                    <div className="text-xs font-extrabold text-slate-800">
                        حسابات مزامنة Snapchat
                    </div>
                    <div className="mt-0.5 text-[11px] text-slate-500">
                        المصروف والتقارير لا تشمل إلا الحسابات التي حددها المالك.
                    </div>
                </div>
                <span
                    className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-white px-2.5 py-1 text-[11px] font-extrabold text-emerald-700"
                    data-testid="snapchat-selected-count"
                    aria-label={`${selectedCount} محدد`}
                >
                    <CheckCircle size={14} weight="fill" />
                    {selectedCount} محدد
                </span>
            </div>

            <div className="mt-3 space-y-2">
                {mergedAccounts.slice(0, 3).map((account) => {
                    const id = accountId(account);
                    const performance = performanceByAccount.get(id);
                    return (
                        <div
                            key={id}
                            className={`rounded-lg border px-3 py-2 text-xs ${
                                account.selected
                                    ? "border-emerald-200 bg-white"
                                    : "border-slate-100 bg-slate-50/70"
                            }`}
                            data-testid={`snapchat-scope-account-${id}`}
                        >
                            <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-1.5">
                                        <span className="truncate font-bold text-slate-700">
                                            {account.display_name || "حساب Snapchat"}
                                        </span>
                                        {account.selected && (
                                            <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-extrabold text-emerald-700">
                                                محدد للمزامنة
                                            </span>
                                        )}
                                    </div>
                                    <div className="mt-1 truncate font-mono text-[10px] text-slate-400">
                                        {id}
                                    </div>
                                </div>
                                <div className="shrink-0 text-left font-mono text-[10px] text-slate-500">
                                    <div>{account.currency || performance?.currency || "—"}</div>
                                    <div>{account.timezone || performance?.timezone || "—"}</div>
                                </div>
                            </div>
                            {account.selected && performance && (
                                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-emerald-100 pt-2 text-[11px]">
                                    <span className="text-slate-500">المصروف في {formatDateRange(summary)}</span>
                                    <span className="font-mono font-extrabold text-slate-800">
                                        {formatNumber(performance.spend_native)} {performance.currency || account.currency}
                                        {performance.currency !== "SAR" && (
                                            <> · {formatNumber(performance.spend_sar)} SAR</>
                                        )}
                                    </span>
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>

            {mergedAccounts.length > 3 && (
                <div className="mt-2 text-center text-[11px] text-slate-400">
                    +{mergedAccounts.length - 3} حسابات مكتشفة أخرى
                </div>
            )}

            {summary && (
                <div className="mt-3 grid gap-2 rounded-lg border border-emerald-100 bg-white p-3 sm:grid-cols-2">
                    <div>
                        <div className="flex items-center gap-1 text-[11px] font-bold text-slate-500">
                            <CurrencyCircleDollar size={15} weight="fill" />
                            إجمالي مصروف الحسابات المحددة
                        </div>
                        <div className="mt-1 font-mono text-lg font-black text-emerald-800">
                            {formatNumber(summary.spend_sar)} SAR
                        </div>
                    </div>
                    <div className="text-[11px] leading-5 text-slate-500 sm:text-left">
                        <div>{summary.rows_included || 0} صف مجمع مستخدم</div>
                        <div>{summary.unselected_rows_excluded || 0} صف لحسابات غير محددة تم استبعاده</div>
                        {unselectedCount > 0 && <div>{unselectedCount} حساب مكتشف غير داخل في الإجمالي</div>}
                    </div>
                </div>
            )}

            {loading && (
                <div className="mt-3 flex items-center gap-2 text-[11px] text-slate-500">
                    <SpinnerGap size={15} className="animate-spin" />
                    جاري تحميل نطاق المزامنة والمصروف…
                </div>
            )}
            {loadError && !loading && (
                <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-100 bg-amber-50 p-2 text-[11px] leading-5 text-amber-800">
                    <WarningCircle size={16} className="mt-0.5 shrink-0" weight="fill" />
                    تعذر تحديث بعض تفاصيل الحسابات الآن. الاختيار المحفوظ لم يتغير، ويمكن إعادة تحميل الصفحة للمحاولة مجددًا.
                </div>
            )}
        </section>
    );
}
