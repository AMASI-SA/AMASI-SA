import { useEffect, useMemo, useState } from "react";
import {
    Bank,
    CheckCircle,
    LockKey,
    Truck,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    getAccountingCourierBankBindings,
    getAccountingSettlementContext,
    saveAccountingCourierBankBinding,
} from "../../services/accountingModule";

function errorText(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    return detail?.message || fallback;
}

function initialEdits(items) {
    return Object.fromEntries((items || []).map((item) => [
        item.courier_key,
        {
            bank_account_id: item.bank_account_id || "",
            evidence_ref: item.evidence_ref || "",
            notes: item.notes || "",
        },
    ]));
}

export default function AccountingCourierBankBindings({ accountingPermissions = [] }) {
    const [items, setItems] = useState([]);
    const [banks, setBanks] = useState([]);
    const [edits, setEdits] = useState({});
    const [loading, setLoading] = useState(true);
    const [busyKey, setBusyKey] = useState("");

    const canManageRules = accountingPermissions.includes("accounting.rules.manage");
    const verifiedCount = useMemo(
        () => items.filter((item) => item.verification_status === "verified").length,
        [items],
    );

    const load = async () => {
        setLoading(true);
        try {
            const [couriers, context] = await Promise.all([
                getAccountingCourierBankBindings(),
                getAccountingSettlementContext(),
            ]);
            const nextItems = couriers?.items || [];
            setItems(nextItems);
            setBanks(context?.banks || []);
            setEdits(initialEdits(nextItems));
        } catch (error) {
            toast.error(errorText(error, "تعذر تحميل بنوك شركات الشحن"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const patch = (courierKey, field, value) => {
        setEdits((old) => ({
            ...old,
            [courierKey]: {
                ...(old[courierKey] || {}),
                [field]: value,
            },
        }));
    };

    const save = async (item) => {
        if (!canManageRules) {
            toast.error("لا تملك صلاحية تعديل قواعد الحسابات");
            return;
        }
        const edit = edits[item.courier_key] || {};
        if (!edit.bank_account_id) {
            toast.error(`اختر بنك تسوية ${item.display_name}`);
            return;
        }
        if (!window.confirm(
            `اعتماد البنك المختار كبنك التسوية الحالي لشركة ${item.display_name}؟ `
            + "هذا الربط لا يسجل تكلفة أو رصيد COD.",
        )) return;

        setBusyKey(item.courier_key);
        try {
            const result = await saveAccountingCourierBankBinding(
                item.courier_key,
                {
                    bank_account_id: edit.bank_account_id,
                    source_kind: edit.evidence_ref ? "provider_statement" : "owner_confirmed",
                    confirmed: true,
                    evidence_ref: edit.evidence_ref || null,
                    notes: edit.notes || "اعتماد البنك الحالي لشركة الشحن",
                },
            );
            toast.success(`تم اعتماد بنك ${result.display_name}`);
            await load();
        } catch (error) {
            toast.error(errorText(error, "تعذر حفظ بنك شركة الشحن"));
        } finally {
            setBusyKey("");
        }
    };

    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="accounting-courier-bank-bindings">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex items-start gap-3">
                    <div className="rounded-xl bg-sky-100 p-2 text-sky-800">
                        <Truck size={26} weight="duotone" />
                    </div>
                    <div>
                        <h3 className="text-lg font-black text-slate-950">بنوك تسوية شركات الشحن الخارجية</h3>
                        <p className="mt-1 text-xs font-semibold leading-5 text-slate-500">
                            تأسيس البنك فقط ضمن P01. تكلفة الشحن، شرائح COD، أرصدة الشركات ومناديب المتجر تبقى مقفلة إلى P02.
                        </p>
                    </div>
                </div>
                <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-extrabold text-slate-700 num">
                    {verifiedCount}/{items.length} معتمد
                </div>
            </div>

            <div className="mt-4 flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-900">
                <LockKey size={18} weight="fill" />
                المندوبون الداخليون والاستلام المباشر مستبعدون من هذه القائمة؛ كل موصل سيُربط منفردًا في P02.
            </div>

            {loading ? (
                <div className="py-10 text-center text-sm font-bold text-slate-500">جاري تحميل شركات الشحن…</div>
            ) : items.length === 0 ? (
                <div className="mt-4 rounded-xl border border-dashed border-slate-300 p-8 text-center">
                    <Bank size={34} className="mx-auto text-slate-400" />
                    <div className="mt-2 font-black text-slate-700">لا توجد شركة شحن خارجية مضافة</div>
                    <p className="mt-1 text-xs font-semibold text-slate-500">لا يُنشأ سجل وهمي. أضف الشركة الفعلية فقط ثم عُد لاعتماد بنكها.</p>
                </div>
            ) : (
                <div className="mt-4 space-y-3">
                    {items.map((item) => {
                        const edit = edits[item.courier_key] || {};
                        const verified = item.verification_status === "verified";
                        return (
                            <article key={item.courier_key} className={`rounded-xl border p-4 ${verified ? "border-emerald-200 bg-emerald-50/50" : "border-amber-200 bg-amber-50/50"}`} data-testid={`courier-bank-${item.courier_key}`}>
                                <div className="grid gap-3 xl:grid-cols-[minmax(0,.7fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.2fr)_auto] xl:items-end">
                                    <div>
                                        <div className="flex items-center gap-2">
                                            <div className="font-black text-slate-950">{item.display_name}</div>
                                            {verified
                                                ? <CheckCircle size={19} weight="fill" className="text-emerald-700" />
                                                : <WarningCircle size={19} weight="fill" className="text-amber-700" />}
                                        </div>
                                        <div className="mt-1 text-[11px] font-bold text-slate-500">
                                            {item.payment_mode === "deferred" ? "دفع آجل" : "دفع مقدم"} · {item.active ? "نشطة" : "متوقفة"}
                                        </div>
                                    </div>
                                    <label className="text-[11px] font-extrabold text-slate-600">البنك الحالي
                                        <select value={edit.bank_account_id || ""} disabled={!canManageRules}
                                            onChange={(event) => patch(item.courier_key, "bank_account_id", event.target.value)}
                                            className="mt-1 min-h-10 w-full rounded-lg border bg-white px-2 text-xs font-bold disabled:bg-slate-100">
                                            <option value="">اختر البنك</option>
                                            {banks.map((bank) => <option key={bank.id} value={bank.id}>{bank.name}</option>)}
                                        </select>
                                    </label>
                                    <label className="text-[11px] font-extrabold text-slate-600">مرجع الكشف/الدليل
                                        <input value={edit.evidence_ref || ""} disabled={!canManageRules}
                                            onChange={(event) => patch(item.courier_key, "evidence_ref", event.target.value)}
                                            placeholder="اختياري في هذه المرحلة"
                                            className="mt-1 min-h-10 w-full rounded-lg border bg-white px-2 text-xs disabled:bg-slate-100" />
                                    </label>
                                    <label className="text-[11px] font-extrabold text-slate-600">ملاحظات
                                        <input value={edit.notes || ""} disabled={!canManageRules}
                                            onChange={(event) => patch(item.courier_key, "notes", event.target.value)}
                                            placeholder="مثال: بنك التسوية الحالي"
                                            className="mt-1 min-h-10 w-full rounded-lg border bg-white px-2 text-xs disabled:bg-slate-100" />
                                    </label>
                                    <button type="button" onClick={() => save(item)} disabled={!canManageRules || busyKey === item.courier_key}
                                        className="min-h-10 rounded-lg bg-sky-800 px-4 text-xs font-extrabold text-white disabled:opacity-40">
                                        {busyKey === item.courier_key ? "جاري الحفظ…" : verified ? "تحديث البنك" : "اعتماد البنك"}
                                    </button>
                                </div>
                                {verified && (
                                    <div className="mt-2 text-[11px] font-bold text-emerald-800">
                                        معتمد على {item.bank_account_name} · لا يوجد قيد أو رصيد ناتج من هذا الربط.
                                    </div>
                                )}
                            </article>
                        );
                    })}
                </div>
            )}
        </section>
    );
}
