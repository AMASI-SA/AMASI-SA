import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
    activateAccountingP02,
    getAccountingShippingWorkspace,
    postAccountingCourierFee,
    postAccountingShippingSettlement,
    postAccountingStoreDriverCod,
    previewAccountingCourierFee,
    previewAccountingShippingSettlement,
    previewAccountingStoreDriverCod,
    saveAccountingShippingRate,
} from "../../services/accountingModule";
import { formatMoney } from "./AccountingShared";

const REASONS = {
    shipping_rate_not_configured: "لا يوجد سعر شحن معتمد لهذا الاسم والتاريخ.",
    shipping_rate_ambiguous: "يوجد أكثر من سعر صالح لنفس شركة الشحن؛ راجع سياسة الأسعار.",
    p02_shipping_cod_locked: "مسار الشحن وCOD ما زال مقفلاً في هذه البيئة.",
    accounting_period_closed: "الفترة المحاسبية لهذه الحركة مقفلة.",
    shipping_counterparty_missing: "الطرف غير موجود ضمن إعدادات الشحن الحالية.",
    shipping_settlement_requires_inflow: "تحصيل COD يحتاج حركة واردة إلى البنك.",
    shipping_fee_payment_requires_outflow: "سداد أجرة الشحن يحتاج حركة خارجة من البنك.",
    shipping_net_offset_required: "التسوية الصافية تحتاج مبلغ المقاصة.",
};

function message(error, fallback) {
    const detail = error?.response?.data?.detail;
    const code = typeof detail === "string" ? detail : detail?.code;
    return REASONS[code] || detail?.message || code || fallback;
}

function rejected(result) {
    const code = result?.reasons?.[0];
    return REASONS[code] || code || "غير مؤهل";
}

function StateBox({ result }) {
    if (!result) return null;
    if (result.state === "eligible") {
        return <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[11px] font-bold text-emerald-900">المعاينة سليمة ويمكن الاعتماد.</div>;
    }
    if (result.state === "already_posted" || result.state === "posted") {
        return <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-[11px] font-bold text-sky-900">مرحّل مسبقًا؛ لن يتكرر القيد.</div>;
    }
    return <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] font-bold text-rose-900">{rejected(result)}</div>;
}

export default function AccountingShippingCod({
    accountingPermissions = [],
    isOwner = false,
}) {
    const [workspace, setWorkspace] = useState(null);
    const [activationRef, setActivationRef] = useState("");
    const [busy, setBusy] = useState("");
    const [previewByKey, setPreviewByKey] = useState({});
    const [settlementById, setSettlementById] = useState({});
    const [rate, setRate] = useState({
        courier_id: "",
        name: "",
        aliases: "",
        total_fee: "",
        effective_at: new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh" }).format(new Date()),
        evidence_ref: "",
        reason: "",
    });
    const canPost = accountingPermissions.includes("accounting.settlements.post");
    const canManageRules = accountingPermissions.includes("accounting.rules.manage");
    const phase = workspace?.phase || {};
    const p02Active = phase.p02_shipping_cod_enabled === true;

    async function refresh() {
        const next = await getAccountingShippingWorkspace();
        setWorkspace(next);
    }

    useEffect(() => {
        refresh().catch((error) => toast.error(message(error, "تعذر تحميل الشحن والتحصيل")));
    }, []);

    const couriers = useMemo(
        () => (workspace?.counterparties || []).filter((row) => row.type === "courier"),
        [workspace],
    );
    const drivers = useMemo(
        () => (workspace?.counterparties || []).filter((row) => row.type === "store_driver"),
        [workspace],
    );

    async function activateP02() {
        if (!isOwner) return toast.error("تفعيل P02 متاح لمالك ميزان فقط");
        if (!canManageRules) return toast.error("لا تملك صلاحية قواعد المحاسبة");
        if (activationRef.trim().length < 3) return toast.error("أدخل مرجع اعتماد P02");
        setBusy("activate-p02");
        try {
            await activateAccountingP02(activationRef.trim());
            toast.success("تم تفعيل P02 للشحن والتحصيل في MZ2.");
            setActivationRef("");
            await refresh();
        } catch (error) {
            toast.error(message(error, "تعذر تفعيل P02"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    async function saveRate(event) {
        event.preventDefault();
        if (!canManageRules) return toast.error("لا تملك صلاحية تعديل أسعار الشحن");
        const aliases = rate.aliases.split(/[،,\n]/).map((value) => value.trim()).filter(Boolean);
        if (!rate.courier_id.trim() || !rate.name.trim() || !aliases.length) return toast.error("أدخل الشركة وأسماء المطابقة");
        if (!(Number(rate.total_fee) > 0)) return toast.error("أدخل تكلفة الشحن");
        if (rate.evidence_ref.trim().length < 3 || rate.reason.trim().length < 3) return toast.error("أدخل مرجع الاعتماد وسببه");
        setBusy("rate");
        try {
            await saveAccountingShippingRate({
                courier_id: rate.courier_id.trim(),
                name: rate.name.trim(),
                aliases,
                total_fee: rate.total_fee,
                effective_at: `${rate.effective_at}T00:00:00+03:00`,
                evidence_ref: rate.evidence_ref.trim(),
                revision: Number(workspace?.rate_policy?.revision || 0),
                reason: rate.reason.trim(),
                tax_treatment: "gross_expense_no_input_vat",
            });
            toast.success("تم حفظ نسخة جديدة من سعر الشحن في MZ2.");
            setRate((current) => ({ ...current, evidence_ref: "", reason: "" }));
            await refresh();
        } catch (error) {
            toast.error(message(error, "تعذر حفظ سعر الشحن"));
        } finally {
            setBusy("");
        }
    }

    async function previewCourier(row) {
        const key = "courier:" + row.id;
        setBusy(key);
        try {
            const result = await previewAccountingCourierFee(row.id);
            setPreviewByKey((current) => ({ ...current, [key]: result }));
        } catch (error) {
            toast.error(message(error, "تعذر معاينة تكلفة الشحن"));
        } finally {
            setBusy("");
        }
    }

    async function postCourier(row) {
        const key = "courier:" + row.id;
        setBusy("post:" + key);
        try {
            await postAccountingCourierFee(row.id);
            toast.success("تم ترحيل تكلفة شركة الشحن من السعر المعتمد.");
            setPreviewByKey((current) => ({ ...current, [key]: null }));
            await refresh();
        } catch (error) {
            toast.error(message(error, "تعذر ترحيل تكلفة الشحن"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    async function previewDriver(row) {
        const key = "driver:" + row.assignment_id;
        setBusy(key);
        try {
            const result = await previewAccountingStoreDriverCod(row.assignment_id);
            setPreviewByKey((current) => ({ ...current, [key]: result }));
        } catch (error) {
            toast.error(message(error, "تعذر معاينة COD المندوب"));
        } finally {
            setBusy("");
        }
    }

    async function postDriver(row) {
        const key = "driver:" + row.assignment_id;
        setBusy("post:" + key);
        try {
            await postAccountingStoreDriverCod(row.assignment_id);
            toast.success("تم إثبات بيع COD وأجرة موصل المتجر في مجموعتين مستقلتين.");
            setPreviewByKey((current) => ({ ...current, [key]: null }));
            await refresh();
        } catch (error) {
            toast.error(message(error, "تعذر ترحيل COD المندوب"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    function updateSettlement(row, patch) {
        const defaults = {
            counterparty_type: "",
            counterparty_id: "",
            settlement_type: row.direction === "in" ? "cod_remittance" : "fee_payment",
            offset_amount: "",
            reason: "",
        };
        setSettlementById((current) => ({
            ...current,
            [row.id]: { ...defaults, ...(current[row.id] || {}), ...patch, preview: null },
        }));
    }

    function settlementPayload(row) {
        const draft = settlementById[row.id] || {};
        return {
            movement_id: row.id,
            counterparty_type: draft.counterparty_type,
            counterparty_id: draft.counterparty_id,
            settlement_type: draft.settlement_type || (row.direction === "in" ? "cod_remittance" : "fee_payment"),
            offset_amount: draft.settlement_type === "net_settlement" ? (draft.offset_amount || "0") : "0",
            reason: String(draft.reason || "").trim(),
        };
    }

    async function previewSettlement(row) {
        const payload = settlementPayload(row);
        if (!payload.counterparty_type || !payload.counterparty_id) return toast.error("اختر شركة الشحن أو المندوب");
        if (payload.reason.length < 3) return toast.error("اكتب سبب التسوية");
        if (payload.settlement_type === "net_settlement" && !(Number(payload.offset_amount) > 0)) return toast.error("أدخل مبلغ المقاصة");
        const key = "settlement:" + row.id;
        setBusy(key);
        try {
            const result = await previewAccountingShippingSettlement(payload);
            setSettlementById((current) => ({
                ...current,
                [row.id]: { ...(current[row.id] || {}), preview: result, previewPayload: payload },
            }));
        } catch (error) {
            toast.error(message(error, "تعذر معاينة التسوية"));
        } finally {
            setBusy("");
        }
    }

    async function postSettlement(row) {
        const draft = settlementById[row.id] || {};
        if (draft.preview?.state !== "eligible" || !draft.previewPayload) return;
        const key = "post:settlement:" + row.id;
        setBusy(key);
        try {
            await postAccountingShippingSettlement(draft.previewPayload);
            toast.success("تم ترحيل تسوية الشحن من حركة البنك نفسها.");
            setSettlementById((current) => {
                const next = { ...current };
                delete next[row.id];
                return next;
            });
            await refresh();
        } catch (error) {
            toast.error(message(error, "تعذر ترحيل تسوية الشحن"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    if (!workspace) {
        return <div className="rounded-2xl border border-slate-200 bg-white p-6 text-sm font-bold text-slate-500">جاري تحميل الشحن والتحصيل…</div>;
    }

    return (
        <div className="space-y-5" dir="rtl" data-testid="accounting-shipping-cod">
            <section className={`rounded-2xl border p-5 ${p02Active ? "border-emerald-200 bg-emerald-50/60" : "border-amber-200 bg-amber-50/60"}`}>
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <h2 className="text-lg font-black text-slate-950">P02 — الشحن والتحصيل</h2>
                        <p className="mt-1 max-w-3xl text-xs font-semibold leading-6 text-slate-700">
                            الكتابة المالية تبقى مقفلة حتى يعتمد المالك P02 صراحة. عند التفعيل يتحقق ميزان أن شركات الشحن والموصلين الحاليين داخل نطاق الأرصدة الافتتاحية المعتمد.
                        </p>
                    </div>
                    <span className={`rounded-full border bg-white px-3 py-1 text-xs font-black ${p02Active ? "border-emerald-300 text-emerald-800" : "border-amber-300 text-amber-800"}`}>
                        {p02Active ? "P02 مفعّل" : "P02 مقفل"}
                    </span>
                </div>
                {!p02Active && (
                    <div className="mt-4 grid gap-2 border-t border-amber-200 pt-4 md:grid-cols-[1fr_auto]">
                        <input
                            value={activationRef}
                            onChange={(event) => setActivationRef(event.target.value)}
                            placeholder="مرجع اعتماد P02 — Preview/UAT"
                            className="min-h-11 rounded-xl border border-amber-200 bg-white px-3 text-sm"
                        />
                        <button
                            type="button"
                            onClick={activateP02}
                            disabled={!isOwner || !canManageRules || busy === "activate-p02"}
                            className="min-h-11 rounded-xl bg-amber-800 px-5 text-sm font-black text-white disabled:opacity-40"
                        >
                            {busy === "activate-p02" ? "جاري التفعيل…" : "تفعيل P02"}
                        </button>
                    </div>
                )}
            </section>

            <section className="rounded-2xl border border-emerald-200 bg-emerald-50/60 p-5">
                <h2 className="text-lg font-black text-emerald-950">أسعار شركات الشحن المعتمدة — MZ2</h2>
                <p className="mt-1 text-xs font-semibold leading-6 text-emerald-900">
                    السعر هنا هو تكلفة شركة الشحن على المتجر، وليس مبلغ الشحن الذي دفعه العميل في سلة. كل تعديل ينشئ نسخة مؤرخة ولا يعدّل التاريخ السابق.
                </p>
                <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    {(workspace.latest_rates || []).map((item) => (
                        <div key={item.id} className="rounded-xl border border-emerald-200 bg-white p-3">
                            <div className="font-black text-slate-900">{item.name}</div>
                            <div className="mt-1 text-lg font-black text-emerald-800">{formatMoney(item.total_fee)}</div>
                            <div className="mt-1 text-[10px] font-semibold text-slate-500">من {String(item.effective_at || "").slice(0, 10)} · {item.evidence_ref}</div>
                        </div>
                    ))}
                    {!(workspace.latest_rates || []).length && <div className="text-xs font-bold text-amber-800">لا توجد أسعار معتمدة بعد.</div>}
                </div>
                <form onSubmit={saveRate} className="mt-4 grid gap-3 border-t border-emerald-200 pt-4 md:grid-cols-2 xl:grid-cols-4">
                    <input value={rate.courier_id} onChange={(e) => setRate((x) => ({ ...x, courier_id: e.target.value }))} placeholder="معرف الشركة: smsa / imile" className="min-h-10 rounded-xl border border-slate-200 px-3 text-xs" />
                    <input value={rate.name} onChange={(e) => setRate((x) => ({ ...x, name: e.target.value }))} placeholder="اسم الشركة" className="min-h-10 rounded-xl border border-slate-200 px-3 text-xs" />
                    <input value={rate.aliases} onChange={(e) => setRate((x) => ({ ...x, aliases: e.target.value }))} placeholder="أسماء سلة المطابقة، مفصولة بفاصلة" className="min-h-10 rounded-xl border border-slate-200 px-3 text-xs" />
                    <input type="number" min="0.01" step="0.01" value={rate.total_fee} onChange={(e) => setRate((x) => ({ ...x, total_fee: e.target.value }))} placeholder="التكلفة الإجمالية" className="min-h-10 rounded-xl border border-slate-200 px-3 text-xs" />
                    <input type="date" value={rate.effective_at} onChange={(e) => setRate((x) => ({ ...x, effective_at: e.target.value }))} className="min-h-10 rounded-xl border border-slate-200 px-3 text-xs" />
                    <input value={rate.evidence_ref} onChange={(e) => setRate((x) => ({ ...x, evidence_ref: e.target.value }))} placeholder="مرجع العقد/الاعتماد" className="min-h-10 rounded-xl border border-slate-200 px-3 text-xs" />
                    <input value={rate.reason} onChange={(e) => setRate((x) => ({ ...x, reason: e.target.value }))} placeholder="سبب اعتماد السعر" className="min-h-10 rounded-xl border border-slate-200 px-3 text-xs" />
                    <button disabled={!canManageRules || busy === "rate"} className="min-h-10 rounded-xl bg-emerald-800 px-4 text-xs font-black text-white disabled:opacity-40">
                        {busy === "rate" ? "جاري الحفظ…" : "حفظ نسخة سعر"}
                    </button>
                </form>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5">
                <div className="flex flex-wrap items-end justify-between gap-2">
                    <div>
                        <h2 className="text-lg font-black text-slate-950">تكلفة شركات الشحن للطلبات المسلّمة</h2>
                        <p className="mt-1 text-xs font-semibold text-slate-500">المعاينة تختار السعر المعتمد بحسب اسم الشركة وتاريخ التسليم، ثم الاعتماد ينشئ مصروف الشحن وذمة شركة الشحن.</p>
                    </div>
                    <div className="text-xs font-black text-slate-500">{(workspace.courier_candidates || []).length} مرشح</div>
                </div>
                <div className="mt-4 space-y-3">
                    {(workspace.courier_candidates || []).map((row) => {
                        const key = "courier:" + row.id;
                        const preview = previewByKey[key];
                        return (
                            <div key={row.id} className="grid gap-3 rounded-xl border border-slate-200 p-3 lg:grid-cols-[1fr_auto] lg:items-center">
                                <div>
                                    <div className="font-black text-slate-900">طلب {row.order_number} · {row.shipping_company}</div>
                                    <div className="mt-1 text-[11px] font-semibold text-slate-500">بوليصة {row.waybill} · تسليم {String(row.delivery_source_text || "").slice(0, 16)} · شحن سلة للمراجعة {formatMoney(row.shipping_cost_source || 0)}</div>
                                    {preview?.facts && <div className="mt-2 text-xs font-black text-emerald-800">تكلفة المتجر المعتمدة: {formatMoney(preview.facts.total_fee)} · {preview.facts.courier_name}</div>}
                                    <div className="mt-2"><StateBox result={preview} /></div>
                                </div>
                                <div className="flex gap-2">
                                    <button type="button" onClick={() => previewCourier(row)} disabled={busy === key} className="min-h-9 rounded-lg border border-slate-300 px-3 text-[11px] font-black">معاينة</button>
                                    <button type="button" onClick={() => postCourier(row)} disabled={!canPost || !p02Active || preview?.state !== "eligible" || busy === "post:" + key} className="min-h-9 rounded-lg bg-emerald-800 px-3 text-[11px] font-black text-white disabled:opacity-40">اعتماد التكلفة</button>
                                </div>
                            </div>
                        );
                    })}
                    {!(workspace.courier_candidates || []).length && <div className="rounded-xl bg-slate-50 p-4 text-xs font-bold text-slate-500">لا توجد تكاليف شحن معلقة في النطاق الحالي.</div>}
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5">
                <div className="flex flex-wrap items-end justify-between gap-2">
                    <div>
                        <h2 className="text-lg font-black text-slate-950">COD — موصل المتجر</h2>
                        <p className="mt-1 text-xs font-semibold text-slate-500">يعتمد فقط تحصيلًا نقديًا تشغيليًا مطابقًا لطلب COD في سلة. البيع وأجرة المندوب يرحّلان كمجموعتين مستقلتين.</p>
                    </div>
                    <div className="text-xs font-black text-slate-500">{(workspace.driver_candidates || []).length} مرشح</div>
                </div>
                <div className="mt-4 space-y-3">
                    {(workspace.driver_candidates || []).map((row) => {
                        const key = "driver:" + row.assignment_id;
                        const preview = previewByKey[key];
                        return (
                            <div key={row.assignment_id} className="grid gap-3 rounded-xl border border-slate-200 p-3 lg:grid-cols-[1fr_auto] lg:items-center">
                                <div>
                                    <div className="font-black text-slate-900">طلب {row.order_number} · {row.driver_name}</div>
                                    <div className="mt-1 text-[11px] font-semibold text-slate-500">تحصيل {formatMoney(row.cod_custody_amount || row.amount)} · {String(row.collected_at || "").slice(0, 16)}</div>
                                    {preview?.facts && <div className="mt-2 text-xs font-black text-emerald-800">COD {formatMoney(preview.facts.gross)} · أجرة المندوب {formatMoney(preview.facts.delivery_fee)}</div>}
                                    <div className="mt-2"><StateBox result={preview} /></div>
                                </div>
                                <div className="flex gap-2">
                                    <button type="button" onClick={() => previewDriver(row)} disabled={busy === key} className="min-h-9 rounded-lg border border-slate-300 px-3 text-[11px] font-black">معاينة</button>
                                    <button type="button" onClick={() => postDriver(row)} disabled={!canPost || preview?.state !== "eligible" || busy === "post:" + key} className="min-h-9 rounded-lg bg-emerald-800 px-3 text-[11px] font-black text-white disabled:opacity-40">اعتماد COD</button>
                                </div>
                            </div>
                        );
                    })}
                    {!(workspace.driver_candidates || []).length && <div className="rounded-xl bg-slate-50 p-4 text-xs font-bold text-slate-500">لا توجد تحصيلات مندوب نقدية معلقة في النطاق الحالي.</div>}
                </div>
            </section>

            <section className="rounded-2xl border border-violet-200 bg-violet-50/50 p-5">
                <div>
                    <h2 className="text-lg font-black text-violet-950">تسويات شركات الشحن والمندوبين مع البنك</h2>
                    <p className="mt-1 text-xs font-semibold leading-6 text-violet-900">
                        اختر حركة البنك الفعلية ثم الطرف ونوع التسوية. التحصيل الوارد يخفض COD المستحق، والسداد الخارج يخفض المبلغ المستحق للطرف، والتسوية الصافية تجمع الاثنين دون إنشاء مصروف جديد.
                    </p>
                </div>
                <div className="mt-4 space-y-3">
                    {(workspace.bank_movements || []).map((row) => {
                        const draft = settlementById[row.id] || {
                            settlement_type: row.direction === "in" ? "cod_remittance" : "fee_payment",
                        };
                        const options = draft.counterparty_type === "courier" ? couriers : draft.counterparty_type === "store_driver" ? drivers : [];
                        return (
                            <div key={row.id} className="rounded-xl border border-violet-200 bg-white p-3">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div>
                                        <span className="font-black text-slate-900">{row.direction === "in" ? "وارد" : "خارج"} {formatMoney(row.amount)}</span>
                                        <span className="mr-2 text-[11px] font-semibold text-slate-500">{row.movement_date} · {row.reference || row.description || "بلا مرجع"}</span>
                                    </div>
                                    <span className="text-[10px] font-bold text-slate-400">{row.bank_account_name}</span>
                                </div>
                                <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-5">
                                    <select value={draft.counterparty_type || ""} onChange={(e) => updateSettlement(row, { counterparty_type: e.target.value, counterparty_id: "" })} className="min-h-9 rounded-lg border border-slate-200 px-2 text-[11px]">
                                        <option value="">نوع الطرف</option>
                                        <option value="courier">شركة شحن</option>
                                        <option value="store_driver">موصل المتجر</option>
                                    </select>
                                    <select value={draft.counterparty_id || ""} onChange={(e) => updateSettlement(row, { counterparty_id: e.target.value })} className="min-h-9 rounded-lg border border-slate-200 px-2 text-[11px]">
                                        <option value="">اختر الطرف</option>
                                        {options.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
                                    </select>
                                    <select value={draft.settlement_type || (row.direction === "in" ? "cod_remittance" : "fee_payment")} onChange={(e) => updateSettlement(row, { settlement_type: e.target.value })} className="min-h-9 rounded-lg border border-slate-200 px-2 text-[11px]">
                                        {row.direction === "in" ? (
                                            <>
                                                <option value="cod_remittance">تحويل COD للبنك</option>
                                                <option value="net_settlement">تسوية صافية بعد خصم أجرة</option>
                                            </>
                                        ) : (
                                            <option value="fee_payment">سداد أجرة/ذمة للطرف</option>
                                        )}
                                    </select>
                                    {(draft.settlement_type === "net_settlement") ? (
                                        <input type="number" min="0.01" step="0.01" value={draft.offset_amount || ""} onChange={(e) => updateSettlement(row, { offset_amount: e.target.value })} placeholder="مبلغ الأجرة المقاصة" className="min-h-9 rounded-lg border border-slate-200 px-2 text-[11px]" />
                                    ) : (
                                        <div className="hidden xl:block" />
                                    )}
                                    <input value={draft.reason || ""} onChange={(e) => updateSettlement(row, { reason: e.target.value })} placeholder="سبب التسوية" className="min-h-9 rounded-lg border border-slate-200 px-2 text-[11px]" />
                                </div>
                                <div className="mt-3 flex flex-wrap items-center gap-2">
                                    <button type="button" onClick={() => previewSettlement(row)} disabled={busy === "settlement:" + row.id} className="min-h-9 rounded-lg border border-violet-300 px-3 text-[11px] font-black text-violet-900">معاينة التسوية</button>
                                    <button type="button" onClick={() => postSettlement(row)} disabled={!canPost || !p02Active || draft.preview?.state !== "eligible" || busy === "post:settlement:" + row.id} className="min-h-9 rounded-lg bg-violet-800 px-3 text-[11px] font-black text-white disabled:opacity-40">اعتماد التسوية</button>
                                    <StateBox result={draft.preview} />
                                </div>
                            </div>
                        );
                    })}
                    {!(workspace.bank_movements || []).length && <div className="rounded-xl bg-white p-4 text-xs font-bold text-violet-700">لا توجد حركات بنك غير مصنفة متاحة لتسوية الشحن.</div>}
                </div>
            </section>
        </div>
    );
}
