import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    Bank,
    CheckCircle,
    FileText,
    LinkSimple,
    MagnifyingGlass,
    Receipt,
    WarningCircle,
    X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    getAccountingSettlementBankCandidates,
    getAccountingSettlementRegister,
    getAccountingSettlementRegisterDetail,
    saveAccountingSettlementBankMatch,
} from "../../services/accountingModule";

const PROVIDERS = [
    ["salla", "سلة"],
    ["tamara", "تمارا"],
    ["tabby", "تابي"],
    ["emkan", "إمكان"],
];

const STATUS = {
    draft: ["مسودة", "border-sky-200 bg-sky-50 text-sky-800"],
    needs_review: ["تحتاج معالجة", "border-amber-200 bg-amber-50 text-amber-900"],
    matched: ["تمت المطابقة", "border-violet-200 bg-violet-50 text-violet-900"],
    ready_for_review: ["تمت المطابقة", "border-violet-200 bg-violet-50 text-violet-900"],
    reviewed: ["تمت المراجعة", "border-emerald-200 bg-emerald-50 text-emerald-900"],
    posting: ["جاري الترحيل", "border-slate-200 bg-slate-100 text-slate-700"],
    posted: ["مرحّلة", "border-emerald-300 bg-emerald-100 text-emerald-950"],
    rejected: ["معادة للمعالجة", "border-rose-200 bg-rose-50 text-rose-900"],
    reversed: ["معكوسة", "border-rose-300 bg-rose-100 text-rose-950"],
};

const AMOUNTS = [
    ["gross_sales", "إجمالي المبيعات"],
    ["refund_full", "استرداد كامل"],
    ["refund_partial", "استرداد جزئي"],
    ["commission", "العمولة"],
    ["commission_vat", "ضريبة العمولة"],
    ["settlement_fee", "رسم التسوية"],
    ["settlement_fee_vat", "ضريبة رسم التسوية"],
    ["wallet_purchases", "مشتريات محفظة سلة"],
    ["other_deductions", "خصومات أخرى"],
    ["rebates", "رد رسوم لصالح المتجر"],
    ["reported_net", "صافي التحويل"],
];

const EMPTY_FILTERS = {
    q: "",
    provider: "",
    status: "",
    bank_account_id: "",
    period_from: "",
    period_to: "",
};

const editable = (status) => ["draft", "needs_review", "rejected"].includes(status);

function errorText(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    if (Array.isArray(detail?.reasons)) {
        return detail.reasons.map((item) => item.message || item.code).join(" · ");
    }
    return fallback;
}

function money(value, currency = "SAR") {
    const number = Number(value || 0);
    return `${number.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ${currency || ""}`.trim();
}

function dateText(value) {
    if (!value) return "—";
    return String(value).slice(0, 10);
}

function StatusBadge({ value }) {
    const [label, classes] = STATUS[value] || [value || "—", STATUS.draft[1]];
    return (
        <span className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-extrabold ${classes}`}>
            {label}
        </span>
    );
}

function Summary({ label, value, hint, tone = "slate" }) {
    const tones = {
        slate: "border-slate-200 bg-slate-50 text-slate-900",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
        rose: "border-rose-200 bg-rose-50 text-rose-950",
    };
    return (
        <div className={`rounded-xl border p-3 ${tones[tone]}`}>
            <div className="text-[11px] font-extrabold opacity-70">{label}</div>
            <div className="mt-1 font-mono text-lg font-black" dir="ltr">{value}</div>
            {hint && <div className="mt-1 text-[10px] font-bold opacity-65">{hint}</div>}
        </div>
    );
}

export default function AccountingSettlementRegister({ accountingPermissions = [] }) {
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [appliedFilters, setAppliedFilters] = useState(EMPTY_FILTERS);
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [selectedId, setSelectedId] = useState("");
    const [detail, setDetail] = useState(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [candidates, setCandidates] = useState([]);
    const [candidatesLoading, setCandidatesLoading] = useState(false);
    const [bankSelection, setBankSelection] = useState("");
    const [bankNotes, setBankNotes] = useState("");
    const [savingBank, setSavingBank] = useState(false);

    const canCreate = accountingPermissions.includes("accounting.drafts.create");
    const selectedDraft = detail?.draft || null;
    const canEditBank = canCreate && editable(selectedDraft?.status);

    const bankOptions = useMemo(() => {
        const unique = new Map();
        items.forEach((item) => {
            if (item.bank_account_id) {
                unique.set(item.bank_account_id, item.bank_account_name || item.bank_account_id);
            }
        });
        return Array.from(unique.entries());
    }, [items]);

    const loadRegister = useCallback(async () => {
        setLoading(true);
        try {
            const result = await getAccountingSettlementRegister({
                ...appliedFilters,
                limit: 300,
            });
            const nextItems = result?.items || [];
            setItems(nextItems);
            setTotal(Number(result?.total_filtered ?? result?.count ?? nextItems.length));
            if (selectedId && !nextItems.some((item) => item.id === selectedId)) {
                setSelectedId("");
                setDetail(null);
                setCandidates([]);
            }
        } catch (error) {
            toast.error(errorText(error, "تعذر تحميل سجل التسويات"));
            setItems([]);
            setTotal(0);
        } finally {
            setLoading(false);
        }
    }, [appliedFilters, selectedId]);

    const openDetail = useCallback(async (draftId) => {
        if (!draftId) return;
        setSelectedId(draftId);
        setDetailLoading(true);
        setCandidates([]);
        try {
            const result = await getAccountingSettlementRegisterDetail(draftId);
            setDetail(result);
            setBankSelection(result?.draft?.bank_transaction_id || "");
            setBankNotes(result?.draft?.bank_match_notes || "");
        } catch (error) {
            toast.error(errorText(error, "تعذر فتح تفاصيل التسوية"));
            setDetail(null);
        } finally {
            setDetailLoading(false);
        }
    }, []);

    useEffect(() => { loadRegister(); }, [loadRegister]);

    const applyFilters = (event) => {
        event.preventDefault();
        setAppliedFilters({ ...filters });
        setSelectedId("");
        setDetail(null);
        setCandidates([]);
    };

    const clearFilters = () => {
        setFilters(EMPTY_FILTERS);
        setAppliedFilters(EMPTY_FILTERS);
        setSelectedId("");
        setDetail(null);
        setCandidates([]);
    };

    const loadCandidates = async () => {
        if (!selectedId || !canEditBank) return;
        setCandidatesLoading(true);
        try {
            const result = await getAccountingSettlementBankCandidates(selectedId, { limit: 100 });
            setCandidates(result?.items || []);
            if (!bankSelection && result?.selected_bank_transaction_id) {
                setBankSelection(result.selected_bank_transaction_id);
            }
            if (!(result?.items || []).length) {
                toast.info("لا توجد حركة واردة متاحة في نافذة تاريخ الكشف");
            }
        } catch (error) {
            toast.error(errorText(error, "تعذر تحميل حركات البنك المرشحة"));
        } finally {
            setCandidatesLoading(false);
        }
    };

    const saveBankMatch = async (clear = false) => {
        if (!selectedId || !canEditBank) return;
        const transactionId = clear ? "" : bankSelection;
        if (!clear && !transactionId) return toast.error("اختر حركة البنك المراد مطابقتها");
        setSavingBank(true);
        try {
            await saveAccountingSettlementBankMatch(selectedId, {
                bank_transaction_id: transactionId || null,
                confirmed: Boolean(transactionId),
                notes: bankNotes,
            });
            toast.success(transactionId ? "تم حفظ مطابقة حركة البنك" : "تم إلغاء مطابقة حركة البنك");
            await Promise.all([loadRegister(), openDetail(selectedId)]);
        } catch (error) {
            toast.error(errorText(error, "تعذر حفظ مطابقة حركة البنك"));
        } finally {
            setSavingBank(false);
        }
    };

    const itemCurrency = selectedDraft?.currency || detail?.register_item?.currency || "";
    const currencySupported = detail?.register_item?.currency_supported !== false && itemCurrency === "SAR";
    const evidenceEntries = detail?.evidence?.entries || [];
    const ledgerEntries = detail?.ledger?.entries || [];
    const selectedCandidate = candidates.find((item) => item.id === bankSelection);

    return (
        <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5"
            data-testid="accounting-settlement-register">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex gap-3">
                    <span className="rounded-xl bg-emerald-50 p-2 text-emerald-800">
                        <Receipt size={25} weight="duotone" />
                    </span>
                    <div>
                        <h3 className="text-lg font-black text-slate-950">السجل المحاسبي للتسويات</h3>
                        <p className="mt-1 text-xs font-semibold leading-5 text-slate-500">
                            بحث موحد حسب المزود والفترة والحالة والبنك، مع الكشف وحركة البنك وأرجل القيد في شاشة واحدة.
                        </p>
                    </div>
                </div>
                <button type="button" onClick={loadRegister} disabled={loading}
                    className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 px-3 text-xs font-extrabold text-slate-700 disabled:opacity-40">
                    <ArrowClockwise size={17} weight="bold" /> {loading ? "جاري التحديث…" : "تحديث السجل"}
                </button>
            </div>

            <form onSubmit={applyFilters} className="grid gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3 lg:grid-cols-7"
                data-testid="settlement-register-filter-form">
                <label className="text-[11px] font-extrabold text-slate-600 lg:col-span-2">بحث
                    <div className="relative mt-1">
                        <MagnifyingGlass size={16} className="absolute right-3 top-3 text-slate-400" />
                        <input value={filters.q} onChange={(event) => setFilters((old) => ({ ...old, q: event.target.value }))}
                            placeholder="مرجع الكشف، الملف، البنك أو رقم القيد"
                            className="min-h-10 w-full rounded-lg border border-slate-200 bg-white pr-9 pl-3 text-xs font-semibold" />
                    </div>
                </label>
                <label className="text-[11px] font-extrabold text-slate-600">المزود
                    <select value={filters.provider} onChange={(event) => setFilters((old) => ({ ...old, provider: event.target.value }))}
                        className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs font-bold">
                        <option value="">الكل</option>
                        {PROVIDERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                </label>
                <label className="text-[11px] font-extrabold text-slate-600">الحالة
                    <select value={filters.status} onChange={(event) => setFilters((old) => ({ ...old, status: event.target.value }))}
                        className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs font-bold">
                        <option value="">الكل</option>
                        {Object.entries(STATUS).filter(([key]) => key !== "ready_for_review").map(([value, [label]]) => (
                            <option key={value} value={value}>{label}</option>
                        ))}
                    </select>
                </label>
                <label className="text-[11px] font-extrabold text-slate-600">البنك
                    <select value={filters.bank_account_id} onChange={(event) => setFilters((old) => ({ ...old, bank_account_id: event.target.value }))}
                        className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs font-bold">
                        <option value="">الكل</option>
                        {bankOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                </label>
                <label className="text-[11px] font-extrabold text-slate-600">من
                    <input type="date" value={filters.period_from} onChange={(event) => setFilters((old) => ({ ...old, period_from: event.target.value }))}
                        className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs num" />
                </label>
                <label className="text-[11px] font-extrabold text-slate-600">إلى
                    <input type="date" value={filters.period_to} onChange={(event) => setFilters((old) => ({ ...old, period_to: event.target.value }))}
                        className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs num" />
                </label>
                <div className="flex items-end gap-2 lg:col-span-7 lg:justify-end">
                    <button type="button" onClick={clearFilters}
                        className="inline-flex min-h-10 items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 text-xs font-extrabold text-slate-600">
                        <X size={15} /> مسح
                    </button>
                    <button type="submit" className="min-h-10 rounded-lg bg-slate-900 px-5 text-xs font-extrabold text-white">
                        تطبيق البحث
                    </button>
                </div>
            </form>

            <div className="grid gap-4 xl:grid-cols-[minmax(0,.85fr)_minmax(0,1.45fr)]">
                <div className="overflow-hidden rounded-xl border border-slate-200">
                    <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-4 py-3">
                        <div className="text-sm font-black text-slate-900">النتائج</div>
                        <div className="font-mono text-xs font-black text-slate-600" dir="ltr">{total}</div>
                    </div>
                    <div className="max-h-[780px] space-y-2 overflow-y-auto p-3" data-testid="settlement-register-list">
                        {loading && <div className="p-8 text-center text-sm font-bold text-slate-500">جاري تحميل السجل…</div>}
                        {!loading && !items.length && <div className="rounded-xl border border-dashed p-8 text-center text-sm font-bold text-slate-500">لا توجد تسويات مطابقة للبحث.</div>}
                        {!loading && items.map((item) => (
                            <button key={item.id} type="button" onClick={() => openDetail(item.id)}
                                className={`w-full rounded-xl border p-3 text-right transition ${selectedId === item.id ? "border-emerald-500 bg-emerald-50" : "border-slate-200 bg-white hover:bg-slate-50"}`}>
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <div className="truncate text-sm font-black text-slate-950">{item.provider_label || item.provider} · {item.statement_reference || "بدون مرجع"}</div>
                                        <div className="mt-1 truncate text-[11px] font-semibold text-slate-500">{item.bank_account_name || "بلا بنك"} · {dateText(item.period_from || item.statement_date)} إلى {dateText(item.period_to || item.statement_date)}</div>
                                        <div className="mt-2 font-mono text-sm font-black text-emerald-800" dir="ltr">{money(item.reported_net, item.currency || "")}</div>
                                    </div>
                                    <StatusBadge value={item.status} />
                                </div>
                            </button>
                        ))}
                    </div>
                </div>

                <div className="min-h-[440px] rounded-xl border border-slate-200 p-4" data-testid="settlement-register-detail">
                    {!selectedId && (
                        <div className="flex min-h-[420px] flex-col items-center justify-center text-center text-slate-500">
                            <FileText size={42} weight="duotone" />
                            <div className="mt-3 font-black text-slate-800">اختر تسوية من النتائج</div>
                            <div className="mt-1 text-xs font-semibold">سيظهر الكشف وحركة البنك والقيد المرحّل دون إخفاء الفروقات.</div>
                        </div>
                    )}
                    {selectedId && detailLoading && <div className="p-12 text-center text-sm font-bold text-slate-500">جاري تحميل تفاصيل التسوية…</div>}

                    {selectedId && !detailLoading && selectedDraft && (
                        <div className="space-y-5">
                            <div className="flex flex-wrap items-start justify-between gap-3">
                                <div>
                                    <div className="text-xs font-extrabold text-emerald-700">{selectedDraft.provider_label || selectedDraft.provider}</div>
                                    <h4 className="mt-1 text-xl font-black text-slate-950">{selectedDraft.statement_reference || "تسوية بلا مرجع"}</h4>
                                    <div className="mt-1 text-[11px] font-semibold text-slate-500">{detail?.evidence?.file?.filename || selectedDraft.source_snapshot?.filename || "—"}</div>
                                </div>
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className={`rounded-full border px-2.5 py-1 font-mono text-[11px] font-black ${currencySupported ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800"}`} dir="ltr">
                                        {itemCurrency || "NO CURRENCY"}
                                    </span>
                                    <StatusBadge value={selectedDraft.status} />
                                </div>
                            </div>

                            {!currencySupported && (
                                <div className="flex gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-extrabold leading-5 text-rose-900">
                                    <WarningCircle size={20} weight="fill" className="shrink-0" />
                                    عملة التسوية غير محفوظة أو غير مدعومة. يمنع النظام انتقالها إلى المراجعة أو الترحيل.
                                </div>
                            )}

                            <div className="grid gap-2 sm:grid-cols-3">
                                <Summary label="إجمالي المبيعات" value={money(selectedDraft.amounts?.gross_sales, itemCurrency)} tone="slate" />
                                <Summary label="صافي التحويل" value={money(selectedDraft.amounts?.reported_net, itemCurrency)} tone="emerald" />
                                <Summary label="فرق حركة البنك" value={money(selectedDraft.bank_transaction_difference, itemCurrency)}
                                    tone={Math.abs(Number(selectedDraft.bank_transaction_difference || 0)) > 0.01 ? "rose" : "slate"}
                                    hint={selectedDraft.bank_transaction_id ? "مقارنة بالحركة المختارة" : "لم تُختر حركة بنك"} />
                            </div>

                            {!!selectedDraft.review_reasons?.length && (
                                <div className="rounded-xl border border-amber-200 bg-amber-50 p-3">
                                    <div className="flex items-center gap-2 text-xs font-black text-amber-950"><WarningCircle size={18} weight="fill" /> أسباب المراجعة المفتوحة</div>
                                    <div className="mt-2 space-y-1 text-[11px] font-bold leading-5 text-amber-900">
                                        {selectedDraft.review_reasons.map((reason) => <div key={`${reason.code}-${reason.message}`}>• {reason.message}</div>)}
                                    </div>
                                </div>
                            )}

                            <div>
                                <h5 className="text-sm font-black text-slate-950">مكونات الكشف</h5>
                                <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                                    {AMOUNTS.map(([key, label]) => (
                                        <div key={key} className="rounded-lg border border-slate-200 bg-slate-50 p-2.5">
                                            <div className="text-[10px] font-extrabold text-slate-500">{label}</div>
                                            <div className="mt-1 font-mono text-sm font-black text-slate-900" dir="ltr">{money(selectedDraft.amounts?.[key], itemCurrency)}</div>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className="rounded-xl border border-slate-200 p-3">
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                    <div className="flex gap-2">
                                        <Bank size={21} weight="duotone" className="text-sky-700" />
                                        <div>
                                            <h5 className="text-sm font-black text-slate-950">دليل وصول صافي التسوية إلى البنك</h5>
                                            <p className="mt-1 text-[11px] font-semibold text-slate-500">اختيار الحركة دليل فقط؛ لا ينشئ قيدًا ثانيًا ولا يغير الرصيد.</p>
                                        </div>
                                    </div>
                                    {canEditBank && (
                                        <button type="button" onClick={loadCandidates} disabled={candidatesLoading}
                                            className="min-h-9 rounded-lg border border-sky-200 bg-sky-50 px-3 text-[11px] font-extrabold text-sky-800">
                                            {candidatesLoading ? "جاري البحث…" : "بحث في حركات البنك"}
                                        </button>
                                    )}
                                </div>

                                {detail?.bank_movement && (
                                    <div className="mt-3 grid gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 sm:grid-cols-3">
                                        <div><div className="text-[10px] font-bold text-emerald-700">التاريخ</div><div className="mt-1 text-xs font-black num">{dateText(detail.bank_movement.transaction_date)}</div></div>
                                        <div><div className="text-[10px] font-bold text-emerald-700">المبلغ</div><div className="mt-1 font-mono text-xs font-black" dir="ltr">{money(detail.bank_movement.amount, itemCurrency)}</div></div>
                                        <div><div className="text-[10px] font-bold text-emerald-700">المرجع</div><div className="mt-1 truncate text-xs font-black">{detail.bank_movement.reference || detail.bank_movement.description || "—"}</div></div>
                                    </div>
                                )}

                                {canEditBank && (
                                    <div className="mt-3 space-y-3" data-testid="settlement-bank-candidates">
                                        <label className="block text-[11px] font-extrabold text-slate-600">حركة البنك
                                            <select value={bankSelection} onChange={(event) => setBankSelection(event.target.value)}
                                                className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs font-bold">
                                                <option value="">بدون حركة بنك مرتبطة</option>
                                                {candidates.map((candidate) => (
                                                    <option key={candidate.id} value={candidate.id}>
                                                        {dateText(candidate.transaction_date)} · {money(candidate.amount, itemCurrency)} · فرق {money(candidate.difference, itemCurrency)} · {candidate.reference || candidate.description || candidate.id}
                                                    </option>
                                                ))}
                                            </select>
                                        </label>
                                        {selectedCandidate && Math.abs(Number(selectedCandidate.difference || 0)) > 0.01 && (
                                            <div className="rounded-lg border border-rose-200 bg-rose-50 p-2 text-[11px] font-extrabold text-rose-900">
                                                هذه الحركة تختلف عن صافي الكشف بمقدار {money(selectedCandidate.difference, itemCurrency)}، وستعيد المسودة إلى «تحتاج معالجة».
                                            </div>
                                        )}
                                        <label className="block text-[11px] font-extrabold text-slate-600">ملاحظة المطابقة
                                            <input value={bankNotes} onChange={(event) => setBankNotes(event.target.value)}
                                                placeholder="مثال: تم التحقق من مرجع التحويل في كشف البنك"
                                                className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 px-3 text-xs" />
                                        </label>
                                        <div className="flex flex-wrap justify-end gap-2">
                                            {!!selectedDraft.bank_transaction_id && (
                                                <button type="button" onClick={() => saveBankMatch(true)} disabled={savingBank}
                                                    className="min-h-9 rounded-lg border border-rose-200 px-3 text-[11px] font-extrabold text-rose-700">
                                                    إلغاء الربط
                                                </button>
                                            )}
                                            <button type="button" onClick={() => saveBankMatch(false)} disabled={savingBank || !bankSelection}
                                                className="min-h-9 rounded-lg bg-sky-700 px-4 text-[11px] font-extrabold text-white disabled:opacity-40"
                                                data-testid="save-settlement-bank-match">
                                                {savingBank ? "جاري الحفظ…" : "حفظ مطابقة البنك"}
                                            </button>
                                        </div>
                                    </div>
                                )}
                            </div>

                            <div className="grid gap-3 lg:grid-cols-2">
                                <div className="rounded-xl border border-slate-200 p-3">
                                    <div className="flex items-center justify-between gap-2">
                                        <div className="flex items-center gap-2 text-sm font-black"><FileText size={19} /> دليل المزود</div>
                                        {detail?.evidence?.file_locked && <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-extrabold text-slate-600">مقفل بعد الربط</span>}
                                    </div>
                                    <div className="mt-2 text-[11px] font-semibold leading-5 text-slate-600">
                                        <div>الملف: <span className="font-black text-slate-900">{detail?.evidence?.file?.filename || selectedDraft.source_snapshot?.filename || "—"}</span></div>
                                        <div>عدد السطور: <span className="font-mono font-black text-slate-900" dir="ltr">{detail?.evidence?.entry_count || 0}</span></div>
                                        <div>مرجع الملف: <span className="font-mono text-[10px]" dir="ltr">{selectedDraft.source_file_id || "—"}</span></div>
                                    </div>
                                    {!!evidenceEntries.length && (
                                        <div className="mt-3 max-h-44 overflow-y-auto rounded-lg border border-slate-100">
                                            {evidenceEntries.slice(0, 20).map((entry) => (
                                                <div key={entry.id} className="flex items-center justify-between gap-2 border-b border-slate-100 px-2 py-1.5 text-[10px] last:border-0">
                                                    <span className="truncate font-bold">{entry.order_number || entry.provider_order_id || entry.event_type || "—"}</span>
                                                    <span className="font-mono font-black" dir="ltr">{money(entry.actual_net_amount, itemCurrency)}</span>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                <div className="rounded-xl border border-slate-200 p-3">
                                    <div className="flex items-center justify-between gap-2">
                                        <div className="flex items-center gap-2 text-sm font-black"><Receipt size={19} /> القيد المحاسبي</div>
                                        {detail?.ledger?.journal_href && (
                                            <a href={detail.ledger.journal_href}
                                                className="inline-flex items-center gap-1 rounded-lg border border-emerald-200 bg-emerald-50 px-2 py-1 text-[10px] font-extrabold text-emerald-800"
                                                data-testid="settlement-register-journal-link">
                                                <LinkSimple size={13} /> فتح القيد
                                            </a>
                                        )}
                                    </div>
                                    {!ledgerEntries.length && <div className="mt-4 rounded-lg border border-dashed p-5 text-center text-xs font-bold text-slate-500">لم يُرحّل قيد لهذه التسوية بعد.</div>}
                                    {!!ledgerEntries.length && (
                                        <div className="mt-3 overflow-x-auto rounded-lg border border-slate-100">
                                            <table className="w-full text-[10px]">
                                                <thead className="bg-slate-50"><tr><th className="p-2 text-right">الحساب</th><th className="p-2 text-right">الجانب</th><th className="p-2 text-left">المبلغ</th></tr></thead>
                                                <tbody>{ledgerEntries.map((entry) => (
                                                    <tr key={entry.id || `${entry.entity_type}-${entry.entity_id}-${entry.side}`} className="border-t border-slate-100">
                                                        <td className="p-2 font-bold">{entry.entity_name || entry.account_name || entry.sub_account || entry.entity_type || "—"}</td>
                                                        <td className="p-2 font-extrabold">{entry.side === "debit" ? "مدين" : "دائن"}</td>
                                                        <td className="p-2 text-left font-mono font-black" dir="ltr">{money(entry.amount, itemCurrency)}</td>
                                                    </tr>
                                                ))}</tbody>
                                            </table>
                                        </div>
                                    )}
                                    {detail?.ledger?.txn_group_id && <div className="mt-2 truncate font-mono text-[9px] text-slate-500" dir="ltr">{detail.ledger.txn_group_id}</div>}
                                </div>
                            </div>

                            <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 text-[11px] font-bold text-slate-600">
                                {selectedDraft.status === "posted"
                                    ? <CheckCircle size={18} weight="fill" className="text-emerald-700" />
                                    : <WarningCircle size={18} weight="duotone" className="text-amber-700" />}
                                {selectedDraft.status === "posted"
                                    ? "القيد المرحّل محفوظ مع Snapshot البنك والدليل ولا يُعدل بأثر رجعي."
                                    : "هذه الشاشة لا ترحّل القيد؛ الترحيل يبقى داخل دورة المسودة والمراجعة وبصلاحية مستقلة."}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </section>
    );
}
