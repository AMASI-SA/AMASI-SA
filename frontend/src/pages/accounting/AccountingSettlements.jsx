import { useEffect, useMemo, useRef, useState } from "react";
import {
    ArrowClockwise,
    Bank,
    CheckCircle,
    FileArrowUp,
    LockKey,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    getAccountingSettlementContext,
    getAccountingSettlementDrafts,
    matchAccountingSettlementEntry,
    postAccountingSettlementDraft,
    rejectAccountingSettlementDraft,
    reviewAccountingSettlementDraft,
    saveAccountingProviderBankBinding,
    submitAccountingSettlementDraft,
    updateAccountingSettlementDraft,
    uploadAccountingSettlementDraft,
} from "../../services/accountingModule";

const STATUS = {
    draft: ["مسودة", "border-sky-200 bg-sky-50 text-sky-800"],
    needs_review: ["تحتاج معالجة", "border-amber-200 bg-amber-50 text-amber-800"],
    ready_for_review: ["جاهزة للمراجعة", "border-violet-200 bg-violet-50 text-violet-800"],
    reviewed: ["تمت المراجعة", "border-emerald-200 bg-emerald-50 text-emerald-800"],
    posting: ["جاري الترحيل", "border-slate-200 bg-slate-100 text-slate-700"],
    posted: ["مرحّلة", "border-emerald-300 bg-emerald-100 text-emerald-900"],
    rejected: ["مرفوضة", "border-rose-200 bg-rose-50 text-rose-800"],
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
    ["reported_net", "صافي الكشف/التحويل"],
];

const money = (value) => Number(value || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
});

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

function Badge({ value }) {
    const [label, classes] = STATUS[value] || [value || "—", STATUS.draft[1]];
    return <span className={`rounded-full border px-2.5 py-1 text-[11px] font-extrabold ${classes}`}>{label}</span>;
}

function emptyEdit() {
    return {
        bank_account_id: "",
        statement_reference: "",
        statement_date: "",
        period_from: "",
        period_to: "",
        notes: "",
        manual_override_reason: "",
        source_review_acknowledged: false,
        amounts: {},
    };
}

export default function AccountingSettlements({ accountingPermissions = [] }) {
    const [context, setContext] = useState(null);
    const [drafts, setDrafts] = useState([]);
    const [selected, setSelected] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");
    const [provider, setProvider] = useState("salla");
    const [bankAccountId, setBankAccountId] = useState("");
    const [statementDate, setStatementDate] = useState("");
    const [notes, setNotes] = useState("");
    const [file, setFile] = useState(null);
    const [fileKey, setFileKey] = useState(0);
    const [edit, setEdit] = useState(emptyEdit);
    const [matchInputs, setMatchInputs] = useState({});
    const selectedIdRef = useRef("");

    const canCreate = context?.can_create_draft === true
        || accountingPermissions.includes("accounting.drafts.create");
    const canPost = context?.can_post === true
        || accountingPermissions.includes("accounting.settlements.post");
    const canManageRules = context?.can_manage_rules === true
        || accountingPermissions.includes("accounting.rules.manage");

    const binding = useMemo(
        () => (context?.bindings || []).find((item) => item.provider === provider),
        [context?.bindings, provider],
    );

    const load = async ({ keepSelection = true } = {}) => {
        setLoading(true);
        try {
            const [nextContext, result] = await Promise.all([
                getAccountingSettlementContext(),
                getAccountingSettlementDrafts({ limit: 200 }),
            ]);
            const nextDrafts = result?.items || [];
            setContext(nextContext);
            setDrafts(nextDrafts);
            if (keepSelection && selectedIdRef.current) {
                setSelected(nextDrafts.find((item) => item.id === selectedIdRef.current) || null);
            }
        } catch (error) {
            toast.error(errorText(error, "تعذر تحميل صفحة التسويات"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load({ keepSelection: false }); }, []);

    useEffect(() => {
        setBankAccountId(binding?.bank_account_id || "");
    }, [binding?.bank_account_id, provider]);

    useEffect(() => {
        if (!selected) {
            selectedIdRef.current = "";
            setEdit(emptyEdit());
            setMatchInputs({});
            return;
        }
        selectedIdRef.current = selected.id;
        setEdit({
            bank_account_id: selected.bank_account_id || "",
            statement_reference: selected.statement_reference || "",
            statement_date: selected.statement_date || "",
            period_from: selected.period_from || "",
            period_to: selected.period_to || "",
            notes: selected.notes || "",
            manual_override_reason: "",
            source_review_acknowledged: false,
            amounts: { ...(selected.amounts || {}) },
        });
        setMatchInputs({});
    }, [selected]);

    const refreshAfter = async (result) => {
        if (result?.id) {
            selectedIdRef.current = result.id;
            setSelected(result);
        }
        await load();
    };

    const confirmBank = async () => {
        if (!canManageRules) return toast.error("لا تملك صلاحية تعديل قواعد الحسابات");
        if (!bankAccountId) return toast.error("اختر البنك الحالي للمزود");
        setBusy("binding");
        try {
            const result = await saveAccountingProviderBankBinding(provider, {
                bank_account_id: bankAccountId,
                source_kind: "owner_confirmed",
                confirmed: true,
                notes: "اعتماد البنك الحالي من صفحة التسويات",
            });
            toast.success(`تم اعتماد بنك ${result.provider_label}`);
            await load();
        } catch (error) {
            toast.error(errorText(error, "تعذر اعتماد بنك التسوية"));
        } finally { setBusy(""); }
    };

    const uploadDraft = async (event) => {
        event.preventDefault();
        if (!canCreate) return toast.error("لا تملك صلاحية إنشاء مسودة مالية");
        if (!file) return toast.error("اختر ملف كشف التسوية");
        if (!bankAccountId) return toast.error("اختر البنك الذي يستقبل التسوية");
        setBusy("upload");
        try {
            const result = await uploadAccountingSettlementDraft({
                provider,
                bankAccountId,
                statementDate,
                notes,
                file,
            });
            const draft = result?.draft;
            if (draft?.duplicate) toast.info("الكشف موجود مسبقًا؛ تم فتح مسودته");
            else if (draft?.status === "needs_review") toast.warning("حُفظت المسودة وتحتاج معالجة");
            else toast.success("تم رفع الكشف وحفظ المسودة");
            setFile(null);
            setFileKey((value) => value + 1);
            setStatementDate("");
            setNotes("");
            await refreshAfter(draft);
        } catch (error) {
            toast.error(errorText(error, "تعذر رفع كشف التسوية"));
        } finally { setBusy(""); }
    };

    const matchEntry = async (entry) => {
        const values = matchInputs[entry.id] || {};
        const orderNumber = String(values.order_number || "").trim();
        const reason = String(values.reason || "").trim();
        if (!orderNumber) return toast.error("أدخل رقم الطلب الصحيح");
        if (reason.length < 3) return toast.error("اكتب سبب المطابقة اليدوية");
        setBusy(`match:${entry.id}`);
        try {
            const result = await matchAccountingSettlementEntry(selected.id, {
                settlement_entry_id: entry.id,
                order_number: orderNumber,
                reason,
            });
            toast.success("تم ربط السطر بالطلب وإعادة احتساب المسودة");
            await refreshAfter(result);
        } catch (error) {
            toast.error(errorText(error, "تعذر مطابقة سطر التسوية"));
        } finally { setBusy(""); }
    };

    const saveDraft = async () => {
        if (!selected || !editable(selected.status)) return;
        const amountsChanged = JSON.stringify(selected.amounts || {}) !== JSON.stringify(edit.amounts || {});
        const resolveSource = edit.source_review_acknowledged === true;
        if ((amountsChanged || resolveSource) && !edit.manual_override_reason.trim()) {
            return toast.error(resolveSource ? "اكتب سبب معالجة بند كشف المزود" : "اكتب سبب تعديل الأرقام");
        }
        const payload = {
            bank_account_id: edit.bank_account_id || null,
            statement_reference: edit.statement_reference,
            statement_date: edit.statement_date || null,
            period_from: edit.period_from || null,
            period_to: edit.period_to || null,
            notes: edit.notes,
        };
        if (amountsChanged) payload.amounts = edit.amounts;
        if (amountsChanged || resolveSource) payload.manual_override_reason = edit.manual_override_reason;
        if (resolveSource) payload.source_review_acknowledged = true;
        setBusy("save");
        try {
            const result = await updateAccountingSettlementDraft(selected.id, payload);
            toast.success("تم حفظ المسودة وإعادة احتساب المعاينة");
            await refreshAfter(result);
        } catch (error) {
            toast.error(errorText(error, "تعذر حفظ المسودة"));
        } finally { setBusy(""); }
    };

    const action = async (kind, call, success, fallback) => {
        setBusy(kind);
        try {
            const result = await call();
            toast.success(success);
            await refreshAfter(result);
        } catch (error) {
            toast.error(errorText(error, fallback));
        } finally { setBusy(""); }
    };

    const rejectDraft = async () => {
        const reason = window.prompt("سبب إعادة التسوية للمعالجة:");
        if (!reason?.trim()) return;
        await action(
            "reject",
            () => rejectAccountingSettlementDraft(selected.id, reason.trim()),
            "أُعيدت التسوية للمعالجة",
            "تعذر رفض التسوية",
        );
    };

    const postDraft = async () => {
        if (!window.confirm(
            `سيتم ترحيل تسوية ${selected?.provider_label || ""} بصافي ${money(selected?.amounts?.reported_net)} SAR. `
            + "القيد المرحّل لا يُعدل أو يُحذف. هل تعتمد الترحيل؟",
        )) return;
        await action(
            "post",
            () => postAccountingSettlementDraft(selected.id, "اعتماد صريح من شاشة التسويات"),
            "تم ترحيل قيد التسوية",
            "تعذر ترحيل التسوية",
        );
    };

    if (loading && !context) {
        return <div className="rounded-2xl border bg-white p-10 text-center font-bold text-slate-500">جاري تحميل التسويات…</div>;
    }

    const unmatched = selected?.source_snapshot?.unmatched_entries || [];

    return (
        <div className="space-y-5" data-testid="accounting-settlements-page">
            <section className="rounded-2xl bg-gradient-to-l from-emerald-950 to-emerald-800 p-5 text-white shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <div className="text-xs font-bold text-emerald-200">P01 · MZ2-FIN-CUTOVER-001</div>
                        <h2 className="mt-1 text-2xl font-black">التسويات وربط البنوك الفعلية</h2>
                        <p className="mt-2 max-w-3xl text-sm font-semibold text-emerald-100">
                            المزود والبنك ← رفع الكشف والمطابقة ← المعاينة والمسودة والمراجعة والترحيل.
                        </p>
                    </div>
                    <button type="button" onClick={() => load()} disabled={loading}
                        className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-white/30 bg-white/10 px-4 text-sm font-extrabold">
                        <ArrowClockwise size={18} weight="bold" /> تحديث
                    </button>
                </div>
            </section>

            <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" data-testid="provider-bank-bindings">
                {(context?.bindings || []).map((item) => (
                    <article key={item.provider} className={`rounded-2xl border p-4 ${item.verification_status === "verified" ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}>
                        <div className="flex justify-between gap-2">
                            <div>
                                <div className="font-black text-slate-950">{item.provider_label}</div>
                                <div className="mt-1 text-xs font-bold text-slate-600">{item.bank_account_name || "لم يُحدد بنك"}</div>
                            </div>
                            {item.verification_status === "verified"
                                ? <CheckCircle size={24} weight="fill" className="text-emerald-700" />
                                : <WarningCircle size={24} weight="fill" className="text-amber-700" />}
                        </div>
                        <div className="mt-3 text-[11px] font-bold text-slate-600">
                            {item.verification_status === "verified"
                                ? "بنك معتمد للتسويات الجديدة"
                                : item.source_kind === "legacy_copy" ? "منسوخ فقط؛ يلزم تأكيده" : "الربط غير مكتمل"}
                        </div>
                    </article>
                ))}
            </section>

            <form onSubmit={uploadDraft} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="settlement-upload-form">
                <div className="flex items-center gap-2">
                    <FileArrowUp size={24} weight="duotone" className="text-emerald-800" />
                    <div>
                        <h3 className="text-lg font-black">إنشاء تسوية جديدة</h3>
                        <p className="text-xs font-semibold text-slate-500">رفع الكشف يحفظ مسودة فقط، ولا يرحل قيدًا.</p>
                    </div>
                </div>
                <div className="mt-5 grid gap-4 lg:grid-cols-4">
                    <label className="text-xs font-extrabold text-slate-700">المزود
                        <select value={provider} onChange={(event) => setProvider(event.target.value)}
                            className="mt-1.5 min-h-11 w-full rounded-xl border px-3 text-sm font-bold" data-testid="settlement-provider">
                            {(context?.providers || []).map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                        </select>
                    </label>
                    <label className="text-xs font-extrabold text-slate-700">البنك المستلم
                        <select value={bankAccountId} onChange={(event) => setBankAccountId(event.target.value)}
                            className="mt-1.5 min-h-11 w-full rounded-xl border px-3 text-sm font-bold" data-testid="settlement-bank">
                            <option value="">اختر البنك</option>
                            {(context?.banks || []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                        </select>
                    </label>
                    <label className="text-xs font-extrabold text-slate-700">تاريخ الكشف عند غيابه
                        <input type="date" value={statementDate} onChange={(event) => setStatementDate(event.target.value)}
                            className="mt-1.5 min-h-11 w-full rounded-xl border px-3 text-sm num" />
                    </label>
                    <label className="text-xs font-extrabold text-slate-700">ملف Excel
                        <input key={fileKey} type="file" accept=".xlsx" onChange={(event) => setFile(event.target.files?.[0] || null)}
                            className="mt-1.5 block min-h-11 w-full rounded-xl border px-3 py-2 text-xs" data-testid="settlement-file" />
                    </label>
                </div>
                <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_auto_auto] lg:items-end">
                    <label className="text-xs font-extrabold text-slate-700">ملاحظات المسودة
                        <input value={notes} onChange={(event) => setNotes(event.target.value)}
                            className="mt-1.5 min-h-11 w-full rounded-xl border px-3 text-sm" />
                    </label>
                    <button type="button" onClick={confirmBank} disabled={!canManageRules || busy === "binding"}
                        className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-emerald-700 px-4 text-sm font-extrabold text-emerald-800 disabled:opacity-40" data-testid="confirm-provider-bank">
                        <Bank size={18} weight="bold" /> {binding?.verification_status === "verified" ? "تحديث البنك" : "اعتماد البنك"}
                    </button>
                    <button type="submit" disabled={!canCreate || busy === "upload"}
                        className="min-h-11 rounded-xl bg-emerald-800 px-6 text-sm font-extrabold text-white disabled:opacity-40" data-testid="create-settlement-draft">
                        {busy === "upload" ? "جاري الرفع…" : "رفع وحفظ مسودة"}
                    </button>
                </div>
            </form>

            <section className="grid gap-5 xl:grid-cols-[minmax(0,.9fr)_minmax(0,1.4fr)]">
                <div className="rounded-2xl border bg-white p-4">
                    <h3 className="font-black">سجل التسويات</h3>
                    <p className="text-xs font-semibold text-slate-500">{drafts.length} سجل</p>
                    <div className="mt-3 max-h-[760px] space-y-2 overflow-y-auto" data-testid="settlement-draft-list">
                        {!drafts.length && <div className="rounded-xl border border-dashed p-8 text-center text-sm font-bold text-slate-500">لا توجد مسودات.</div>}
                        {drafts.map((item) => (
                            <button key={item.id} type="button" onClick={() => setSelected(item)}
                                className={`w-full rounded-xl border p-3 text-right ${selected?.id === item.id ? "border-emerald-500 bg-emerald-50" : "border-slate-200"}`}>
                                <div className="flex justify-between gap-2">
                                    <div>
                                        <div className="font-extrabold">{item.provider_label} · {item.statement_reference || "بدون مرجع"}</div>
                                        <div className="mt-1 text-xs font-semibold text-slate-500">{item.bank_account_name || "بلا بنك"} · {money(item.amounts?.reported_net)} SAR</div>
                                    </div>
                                    <Badge value={item.status} />
                                </div>
                            </button>
                        ))}
                    </div>
                </div>

                <div className="rounded-2xl border bg-white p-5" data-testid="settlement-draft-detail">
                    {!selected && (
                        <div className="flex min-h-[360px] flex-col items-center justify-center text-center">
                            <LockKey size={40} className="text-slate-400" />
                            <h3 className="mt-3 font-black">اختر تسوية من السجل</h3>
                            <p className="mt-1 text-sm font-semibold text-slate-500">تظهر المطابقة والمعاينة والمدين والدائن هنا.</p>
                        </div>
                    )}

                    {selected && (
                        <div className="space-y-5">
                            <div className="flex flex-wrap justify-between gap-3">
                                <div>
                                    <div className="text-xs font-bold text-slate-500">{selected.provider_label}</div>
                                    <h3 className="text-xl font-black">{selected.statement_reference || "تسوية بلا مرجع"}</h3>
                                    <div className="mt-1 text-xs font-semibold text-slate-500">نسخة {selected.version || 1} · {selected.source_snapshot?.filename || "—"}</div>
                                </div>
                                <Badge value={selected.status} />
                            </div>

                            {!!selected.review_reasons?.length && (
                                <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                                    <div className="flex items-center gap-2 font-black text-amber-900"><WarningCircle size={20} weight="fill" /> أسباب تمنع الانتقال</div>
                                    <div className="mt-2 space-y-1 text-xs font-bold text-amber-800">
                                        {selected.review_reasons.map((item) => <div key={`${item.code}-${item.message}`}>• {item.message}</div>)}
                                    </div>
                                </div>
                            )}

                            <div className="grid gap-3 sm:grid-cols-3">
                                <label className="text-[11px] font-extrabold text-slate-600">البنك
                                    <select value={edit.bank_account_id} disabled={!editable(selected.status)}
                                        onChange={(event) => setEdit((old) => ({ ...old, bank_account_id: event.target.value }))}
                                        className="mt-1 min-h-10 w-full rounded-lg border px-2 text-xs font-bold disabled:bg-slate-100">
                                        <option value="">اختر البنك</option>
                                        {(context?.banks || []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                                    </select>
                                </label>
                                <label className="text-[11px] font-extrabold text-slate-600">مرجع الكشف
                                    <input value={edit.statement_reference} disabled={!editable(selected.status)}
                                        onChange={(event) => setEdit((old) => ({ ...old, statement_reference: event.target.value }))}
                                        className="mt-1 min-h-10 w-full rounded-lg border px-2 text-xs disabled:bg-slate-100" />
                                </label>
                                <label className="text-[11px] font-extrabold text-slate-600">تاريخ الكشف
                                    <input type="date" value={edit.statement_date} disabled={!editable(selected.status)}
                                        onChange={(event) => setEdit((old) => ({ ...old, statement_date: event.target.value }))}
                                        className="mt-1 min-h-10 w-full rounded-lg border px-2 text-xs num disabled:bg-slate-100" />
                                </label>
                            </div>

                            {!!unmatched.length && (
                                <div className="rounded-xl border border-rose-200 bg-rose-50 p-4" data-testid="settlement-unmatched-entries">
                                    <div className="flex items-center gap-2 font-black text-rose-900"><WarningCircle size={20} weight="fill" /> طلبات غير مطابقة</div>
                                    <p className="mt-1 text-xs font-semibold text-rose-700">اربط كل سطر بطلب موجود في سلة قبل إرسال المسودة.</p>
                                    <div className="mt-3 space-y-3">
                                        {unmatched.map((entry) => {
                                            const values = matchInputs[entry.id] || {};
                                            return (
                                                <div key={entry.id} className="grid gap-2 rounded-xl border bg-white p-3 lg:grid-cols-[1fr_1fr_1.3fr_auto] lg:items-end">
                                                    <div className="text-xs font-bold"><span className="text-[10px] text-slate-500">مرجع المزود</span><div className="num">{entry.provider_order_id || entry.order_number || "—"}</div><div className="text-[10px] text-slate-500">إجمالي {money(entry.actual_gross_amount)} · صافي {money(entry.actual_net_amount)}</div></div>
                                                    <label className="text-[10px] font-extrabold">رقم الطلب الصحيح
                                                        <input value={values.order_number || ""} placeholder="276628330"
                                                            onChange={(event) => setMatchInputs((old) => ({ ...old, [entry.id]: { ...(old[entry.id] || {}), order_number: event.target.value } }))}
                                                            className="mt-1 min-h-10 w-full rounded-lg border px-2 text-xs num" />
                                                    </label>
                                                    <label className="text-[10px] font-extrabold">سبب المطابقة
                                                        <input value={values.reason || ""} placeholder="تم التحقق من المرجع"
                                                            onChange={(event) => setMatchInputs((old) => ({ ...old, [entry.id]: { ...(old[entry.id] || {}), reason: event.target.value } }))}
                                                            className="mt-1 min-h-10 w-full rounded-lg border px-2 text-xs" />
                                                    </label>
                                                    <button type="button" onClick={() => matchEntry(entry)} disabled={!canCreate || busy === `match:${entry.id}`}
                                                        className="min-h-10 rounded-lg bg-rose-700 px-4 text-xs font-extrabold text-white disabled:opacity-40">
                                                        {busy === `match:${entry.id}` ? "جاري الربط…" : "ربط بالطلب"}
                                                    </button>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>
                            )}

                            <div>
                                <h4 className="font-black">مكونات التسوية</h4>
                                <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                                    {AMOUNTS.map(([key, label]) => (
                                        <label key={key} className="rounded-xl border bg-slate-50 p-3 text-[11px] font-extrabold text-slate-600">{label}
                                            <input type="number" step="0.01" value={edit.amounts?.[key] ?? 0} disabled={!editable(selected.status)}
                                                onChange={(event) => setEdit((old) => ({ ...old, amounts: { ...(old.amounts || {}), [key]: Number(event.target.value) } }))}
                                                className="mt-1 min-h-9 w-full rounded-lg border bg-white px-2 text-left text-sm font-black num disabled:bg-slate-100" />
                                        </label>
                                    ))}
                                </div>
                            </div>

                            {editable(selected.status) && (
                                <div className="grid gap-3 sm:grid-cols-2">
                                    {Number(selected.source_review_count || 0) > 0 && (
                                        <label className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-extrabold text-amber-900 sm:col-span-2">
                                            <span className="flex gap-2"><input type="checkbox" checked={edit.source_review_acknowledged}
                                                onChange={(event) => setEdit((old) => ({ ...old, source_review_acknowledged: event.target.checked }))} />
                                                راجعت البند الذي لم يفصل كشف المزود ضريبته أو مكوناته، وسأوثق سبب المعالجة.
                                            </span>
                                        </label>
                                    )}
                                    <label className="text-xs font-extrabold">سبب تعديل الأرقام/معالجة المصدر
                                        <input value={edit.manual_override_reason} onChange={(event) => setEdit((old) => ({ ...old, manual_override_reason: event.target.value }))}
                                            className="mt-1 min-h-10 w-full rounded-lg border px-3 text-xs" />
                                    </label>
                                    <label className="text-xs font-extrabold">ملاحظات
                                        <input value={edit.notes} onChange={(event) => setEdit((old) => ({ ...old, notes: event.target.value }))}
                                            className="mt-1 min-h-10 w-full rounded-lg border px-3 text-xs" />
                                    </label>
                                </div>
                            )}

                            {selected.calculation && (
                                <div className="grid gap-2 sm:grid-cols-3">
                                    {[["الصافي المحسوب", "calculated_net"], ["فرق المعادلة", "equation_difference"], ["إقفال ذمة المزود", "provider_receivable_close"]].map(([label, key]) => (
                                        <div key={key} className="rounded-xl border p-3"><div className="text-[11px] font-bold text-slate-500">{label}</div><div className="mt-1 text-lg font-black num">{money(selected.calculation[key])} SAR</div></div>
                                    ))}
                                </div>
                            )}

                            {!!selected.journal_preview?.entries?.length && (
                                <div><h4 className="font-black">معاينة القيد</h4>
                                    <div className="mt-2 overflow-hidden rounded-xl border"><table className="w-full text-xs"><thead className="bg-slate-50"><tr><th className="p-3 text-right">الحساب</th><th className="p-3 text-left">مدين</th><th className="p-3 text-left">دائن</th></tr></thead><tbody>
                                        {selected.journal_preview.entries.map((entry, index) => <tr key={`${entry.role}-${index}`} className="border-t"><td className="p-3 font-bold">{entry.label}</td><td className="p-3 text-left font-black num">{entry.side === "debit" ? money(entry.amount) : "—"}</td><td className="p-3 text-left font-black num">{entry.side === "credit" ? money(entry.amount) : "—"}</td></tr>)}
                                    </tbody></table></div>
                                </div>
                            )}

                            {selected.status === "posted" && <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm font-extrabold text-emerald-900">تم ترحيل القيد: <span className="num">{selected.ledger_txn_group_id}</span></div>}

                            <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
                                {editable(selected.status) && canCreate && <><button type="button" onClick={saveDraft} disabled={busy === "save"} className="min-h-10 rounded-lg border px-4 text-xs font-extrabold">حفظ التعديلات</button><button type="button" onClick={() => action("submit", () => submitAccountingSettlementDraft(selected.id), "أُرسلت للمراجعة", "تعذر الإرسال")} disabled={busy === "submit" || !!selected.review_reasons?.length} className="min-h-10 rounded-lg bg-sky-700 px-4 text-xs font-extrabold text-white disabled:opacity-40" data-testid="submit-settlement-draft">إرسال للمراجعة</button></>}
                                {selected.status === "ready_for_review" && canPost && <><button type="button" onClick={rejectDraft} className="min-h-10 rounded-lg border border-rose-300 px-4 text-xs font-extrabold text-rose-700">إعادة للمعالجة</button><button type="button" onClick={() => action("review", () => reviewAccountingSettlementDraft(selected.id), "تمت المراجعة", "تعذر اعتماد المراجعة")} disabled={busy === "review"} className="min-h-10 rounded-lg bg-violet-700 px-4 text-xs font-extrabold text-white" data-testid="review-settlement-draft">اعتماد المراجعة</button></>}
                                {selected.status === "reviewed" && canPost && <><button type="button" onClick={rejectDraft} className="min-h-10 rounded-lg border border-rose-300 px-4 text-xs font-extrabold text-rose-700">إعادة للمعالجة</button><button type="button" onClick={postDraft} disabled={busy === "post"} className="min-h-10 rounded-lg bg-emerald-800 px-5 text-xs font-extrabold text-white" data-testid="post-settlement-draft">{busy === "post" ? "جاري الترحيل…" : "ترحيل القيد"}</button></>}
                            </div>
                        </div>
                    )}
                </div>
            </section>
        </div>
    );
}
