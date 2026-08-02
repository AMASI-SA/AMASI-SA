import { useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    Bank,
    CheckCircle,
    Coins,
    CurrencyCircleDollar,
    FloppyDisk,
    ShieldCheck,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    AD_COST_PROVIDER_LABELS,
    AD_COST_PROVIDER_ORDER,
    getAdAccountCostSettingsV2,
    saveAdAccountCostSettingsV2,
} from "../services/adsAccountCostSettingsV2";

const money = (value) => Number(value || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
});

const statusLabel = (value) => ({
    connected: "متصل",
    needs_reauth: "يحتاج إعادة ربط",
    data_available: "بيانات متاحة",
    error: "خطأ",
}[value] || value || "غير معروف");

function draftFromItem(item) {
    return {
        native_currency: item.native_currency,
        exchange_rate_to_sar: item.exchange_rate_to_sar,
        bank_commission_pct: item.bank_commission_pct,
        apply_bank_commission: item.apply_bank_commission,
    };
}

function AccountCostCard({ item, draft, onChange, onSave, saving }) {
    const amount = 100;
    const rate = draft.native_currency === "SAR"
        ? 1
        : Number(draft.exchange_rate_to_sar) || 0;
    const baseSar = amount * rate;
    const commission = draft.apply_bank_commission
        ? baseSar * ((Number(draft.bank_commission_pct) || 0) / 100)
        : 0;
    const providerLabel = AD_COST_PROVIDER_LABELS[item.provider] || item.provider_label;

    return (
        <article
            className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
            data-testid={`mezan2-ad-cost-account-${item.mezan_integration_account_id}`}
        >
            <div className="border-b border-slate-100 bg-slate-50 px-4 py-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                            <h2 className="text-base font-black text-slate-950">{item.display_name}</h2>
                            <span className="rounded-full bg-emerald-100 px-2 py-1 text-[10px] font-black text-emerald-800">
                                {providerLabel}
                            </span>
                            <span className="rounded-full bg-slate-200 px-2 py-1 text-[10px] font-bold text-slate-700">
                                {statusLabel(item.connection_status)}
                            </span>
                        </div>
                        <div className="mt-1 break-all font-mono text-[11px] text-slate-500">
                            {item.external_account_id}
                        </div>
                        <div className="mt-1 text-[11px] font-semibold text-slate-500">
                            توقيت الحساب: {item.timezone || "غير محدد"}
                        </div>
                    </div>
                    <span className={`rounded-full border px-3 py-1 text-xs font-black ${item.configured ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-amber-200 bg-amber-50 text-amber-700"}`}>
                        {item.configured ? "محفوظ في ميزان 2" : "إعداد افتراضي"}
                    </span>
                </div>
            </div>

            <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(260px,0.7fr)]">
                <div className="grid gap-3 sm:grid-cols-2">
                    <label className="block">
                        <span className="mb-1 block text-xs font-black text-slate-700">عملة السحب</span>
                        <select
                            value={draft.native_currency}
                            onChange={(event) => {
                                const currency = event.target.value;
                                onChange({
                                    ...draft,
                                    native_currency: currency,
                                    exchange_rate_to_sar: currency === "SAR"
                                        ? 1
                                        : draft.exchange_rate_to_sar || 3.7544,
                                });
                            }}
                            className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-black outline-none focus:border-emerald-400"
                            data-testid={`mezan2-ad-cost-currency-${item.mezan_integration_account_id}`}
                        >
                            <option value="SAR">SAR — ريال سعودي</option>
                            <option value="USD">USD — دولار أمريكي</option>
                        </select>
                    </label>

                    <label className="block">
                        <span className="mb-1 block text-xs font-black text-slate-700">سعر الصرف إلى الريال</span>
                        <input
                            type="number"
                            min="0.0001"
                            max="20"
                            step="0.0001"
                            value={draft.native_currency === "SAR" ? 1 : draft.exchange_rate_to_sar}
                            disabled={draft.native_currency === "SAR"}
                            onChange={(event) => onChange({
                                ...draft,
                                exchange_rate_to_sar: event.target.value,
                            })}
                            className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 font-mono text-sm font-black outline-none focus:border-emerald-400 disabled:bg-slate-100 disabled:text-slate-400"
                            data-testid={`mezan2-ad-cost-fx-${item.mezan_integration_account_id}`}
                        />
                    </label>

                    <label className="block">
                        <span className="mb-1 block text-xs font-black text-slate-700">عمولة البنك (%)</span>
                        <input
                            type="number"
                            min="0"
                            max="20"
                            step="0.01"
                            value={draft.bank_commission_pct}
                            onChange={(event) => onChange({
                                ...draft,
                                bank_commission_pct: event.target.value,
                            })}
                            className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 font-mono text-sm font-black outline-none focus:border-emerald-400"
                            data-testid={`mezan2-ad-cost-fee-${item.mezan_integration_account_id}`}
                        />
                    </label>

                    <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3">
                        <span>
                            <span className="block text-xs font-black text-slate-700">تطبيق العمولة</span>
                            <span className="block text-[10px] font-semibold text-slate-500">تُضاف إلى تكلفة السحب الفعلية</span>
                        </span>
                        <input
                            type="checkbox"
                            checked={draft.apply_bank_commission}
                            onChange={(event) => onChange({
                                ...draft,
                                apply_bank_commission: event.target.checked,
                            })}
                            className="h-5 w-5 accent-emerald-600"
                            data-testid={`mezan2-ad-cost-apply-fee-${item.mezan_integration_account_id}`}
                        />
                    </label>
                </div>

                <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
                    <div className="flex items-center gap-2 text-sm font-black text-emerald-950">
                        <Coins size={20} weight="duotone" />
                        معاينة لكل {amount} {draft.native_currency}
                    </div>
                    <div className="mt-3 space-y-2 text-xs font-bold text-slate-700">
                        <div className="flex justify-between gap-3"><span>الصرف بالريال</span><span className="font-mono">{money(baseSar)} ر.س</span></div>
                        <div className="flex justify-between gap-3 text-rose-700"><span>عمولة البنك</span><span className="font-mono">{money(commission)} ر.س</span></div>
                        <div className="flex justify-between gap-3 border-t border-emerald-200 pt-2 text-sm font-black text-emerald-950"><span>التكلفة الفعلية</span><span className="font-mono">{money(baseSar + commission)} ر.س</span></div>
                    </div>
                    <button
                        type="button"
                        onClick={onSave}
                        disabled={saving}
                        className="mt-4 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-emerald-700 px-4 text-sm font-black text-white transition hover:bg-emerald-800 disabled:opacity-60"
                        data-testid={`mezan2-ad-cost-save-${item.mezan_integration_account_id}`}
                    >
                        {saving
                            ? <ArrowClockwise size={18} className="animate-spin" weight="bold" />
                            : <FloppyDisk size={18} weight="bold" />}
                        {saving ? "جاري الحفظ…" : "حفظ إعداد الحساب"}
                    </button>
                </div>
            </div>
        </article>
    );
}

export default function AdsCostSettingsV2() {
    const [data, setData] = useState(null);
    const [drafts, setDrafts] = useState({});
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [savingId, setSavingId] = useState("");
    const [error, setError] = useState("");
    const [providerFilter, setProviderFilter] = useState("all");

    const load = async ({ silent = false } = {}) => {
        if (silent) setRefreshing(true);
        else setLoading(true);
        setError("");
        try {
            const response = await getAdAccountCostSettingsV2();
            setData(response);
            setDrafts(Object.fromEntries(
                response.items.map((item) => [
                    item.mezan_integration_account_id,
                    draftFromItem(item),
                ]),
            ));
        } catch (loadError) {
            const detail = loadError?.response?.data?.detail;
            setError(detail?.message || detail || "تعذّر تحميل إعدادات الحسابات الإعلانية.");
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    };

    useEffect(() => {
        load();
    }, []);

    const visibleItems = useMemo(() => {
        const items = data?.items || [];
        return providerFilter === "all"
            ? items
            : items.filter((item) => item.provider === providerFilter);
    }, [data?.items, providerFilter]);

    const save = async (item) => {
        const id = item.mezan_integration_account_id;
        const draft = drafts[id];
        setSavingId(id);
        try {
            const saved = await saveAdAccountCostSettingsV2(id, draft);
            setData((current) => ({
                ...current,
                items: current.items.map((row) => row.mezan_integration_account_id === id ? saved : row),
                summary: {
                    ...current.summary,
                    configured: current.items.filter((row) => row.configured || row.mezan_integration_account_id === id).length,
                    fee_enabled: current.items.filter((row) => row.mezan_integration_account_id === id ? saved.apply_bank_commission : row.apply_bank_commission).length,
                },
            }));
            setDrafts((current) => ({ ...current, [id]: draftFromItem(saved) }));
            toast.success(`تم حفظ إعداد ${item.display_name}`);
        } catch (saveError) {
            const detail = saveError?.response?.data?.detail;
            toast.error(detail?.message || detail || "تعذّر حفظ إعداد الحساب.");
        } finally {
            setSavingId("");
        }
    };

    const summary = data?.summary || {};
    const policySafe = data && !data.policy.legacy_counterparties_read
        && !data.policy.legacy_ads_currency_settings_read;

    return (
        <div className="space-y-5" dir="rtl" data-testid="mezan2-ad-cost-settings-page">
            <header className="overflow-hidden rounded-3xl border border-emerald-950 bg-[#033d2f] text-white shadow-xl">
                <div className="grid gap-5 p-5 sm:p-7 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                    <div>
                        <div className="text-xs font-black tracking-wide text-emerald-300">Mezan 2 · التسويق</div>
                        <h1 className="mt-1 text-2xl font-black sm:text-3xl">العمولات البنكية وسعر الصرف</h1>
                        <p className="mt-2 max-w-3xl text-sm leading-6 text-emerald-100">
                            إعداد مستقل لكل حساب إعلاني مرتبط داخل ميزان 2 لحساب التكلفة الفعلية بعد تحويل العملة ورسوم البنك.
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={() => load({ silent: true })}
                        disabled={refreshing}
                        className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-emerald-200 px-5 text-sm font-black text-slate-950 hover:bg-emerald-100 disabled:opacity-60"
                    >
                        <ArrowClockwise size={19} weight="bold" className={refreshing ? "animate-spin" : ""} />
                        تحديث الحسابات
                    </button>
                </div>
                <div className="border-t border-white/10 bg-black/10 px-5 py-3 text-xs font-bold text-emerald-100 sm:px-7">
                    المصدر: الحسابات المرتبطة في ميزان 2 فقط · لا قراءة من الأطراف أو إعدادات العملات القديمة.
                </div>
            </header>

            {error && (
                <div className="flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-800">
                    <WarningCircle size={21} weight="fill" className="mt-0.5 shrink-0" />
                    {error}
                </div>
            )}

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5" aria-label="ملخص إعدادات الحسابات">
                {[
                    ["الحسابات", summary.accounts_total || 0, CurrencyCircleDollar],
                    ["محفوظة", summary.configured || 0, CheckCircle],
                    ["تطبق العمولة", summary.fee_enabled || 0, Bank],
                    ["حسابات USD", summary.usd_accounts || 0, Coins],
                    ["عزل ميزان القديم", policySafe ? "مكتمل" : "يحتاج مراجعة", ShieldCheck],
                ].map(([label, value, Icon]) => (
                    <article key={label} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <div className="text-xs font-black text-slate-500">{label}</div>
                                <div className="mt-2 text-2xl font-black text-slate-950">{value}</div>
                            </div>
                            <span className="rounded-xl bg-emerald-50 p-2 text-emerald-700"><Icon size={22} weight="duotone" /></span>
                        </div>
                    </article>
                ))}
            </section>

            <div className="flex flex-wrap gap-2 rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
                <button
                    type="button"
                    onClick={() => setProviderFilter("all")}
                    className={`rounded-full px-4 py-2 text-xs font-black ${providerFilter === "all" ? "bg-slate-950 text-white" : "bg-slate-100 text-slate-600"}`}
                >
                    كل المنصات
                </button>
                {AD_COST_PROVIDER_ORDER.map((provider) => (
                    <button
                        type="button"
                        key={provider}
                        onClick={() => setProviderFilter(provider)}
                        className={`rounded-full px-4 py-2 text-xs font-black ${providerFilter === provider ? "bg-emerald-700 text-white" : "bg-emerald-50 text-emerald-800"}`}
                    >
                        {AD_COST_PROVIDER_LABELS[provider]}
                    </button>
                ))}
            </div>

            {loading ? (
                <div className="flex min-h-72 items-center justify-center rounded-2xl border border-slate-200 bg-white text-emerald-700">
                    <ArrowClockwise size={34} className="animate-spin" />
                </div>
            ) : visibleItems.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center">
                    <CurrencyCircleDollar size={42} weight="duotone" className="mx-auto text-slate-300" />
                    <div className="mt-3 font-black text-slate-700">لا توجد حسابات مرتبطة في هذه المنصة.</div>
                    <p className="mt-1 text-sm text-slate-500">اربط الحساب من صفحة التطبيقات والحسابات الإعلانية في ميزان 2.</p>
                </div>
            ) : (
                <div className="grid gap-4 xl:grid-cols-2">
                    {visibleItems.map((item) => (
                        <AccountCostCard
                            key={item.mezan_integration_account_id}
                            item={item}
                            draft={drafts[item.mezan_integration_account_id] || draftFromItem(item)}
                            onChange={(next) => setDrafts((current) => ({
                                ...current,
                                [item.mezan_integration_account_id]: next,
                            }))}
                            onSave={() => save(item)}
                            saving={savingId === item.mezan_integration_account_id}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}
