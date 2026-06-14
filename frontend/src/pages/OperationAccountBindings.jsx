// Iter-184 — Operation → Accounts Binding Settings
//
// One settings page that lets the merchant restrict which accounts
// can be used for each operation type. Backed by
//   GET  /api/settings  (reads operation_account_bindings)
//   PUT  /api/settings  (writes the full settings doc back)
//
// UX rules
// --------
// * Per row (operation) the merchant either:
//     • keeps "السماح للكل"  ← empty list = legacy behaviour
//     • OR ticks one or more allowed accounts (any unchecked is denied)
// * Toggling the "السماح للكل" badge resets the row to an empty list.
// * A warning is shown if a row is in "allow specific" mode but has
//   zero accounts ticked — that would lock the operation completely.

import React, { useEffect, useMemo, useState } from "react";
import api from "../lib/api";
import { toast } from "sonner";

// Must mirror the keys in backend ACCOUNT_BOUND_OPS and frontend
// UnifiedEntryScreen.OP_TYPES.
const OPS = [
    { value: "advance_grant",    label: "💰 سلفة موظف",            section: "موظفون" },
    { value: "salary_settle",    label: "📅 صرف راتب",              section: "موظفون" },
    { value: "custody_grant",    label: "🎒 تسليم عهدة",             section: "موظفون" },
    { value: "custody_return",   label: "🔙 إرجاع عهدة نقداً",       section: "موظفون" },
    { value: "supplier_pay",     label: "💸 سداد مورد",              section: "موردون" },
    { value: "external_grant",   label: "🤝 سلفة لشخص خارجي",      section: "خارجيون" },
    { value: "external_collect", label: "💵 تحصيل من شخص خارجي",    section: "خارجيون" },
    { value: "expense_record",   label: "🛒 مصروف عام",               section: "عام" },
    { value: "bank_transfer",    label: "🔄 تحويل بين الحسابات",     section: "عام" },
];

const accountTypeLabel = (t) => ({
    bank:             "🏦 بنك",
    cash:             "💵 صندوق",
    payment_platform: "💳 بوابة دفع",
    courier:          "📦 شركة شحن",
}[t] || `📁 ${t || "أخرى"}`);

export default function OperationAccountBindings() {
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [accounts, setAccounts] = useState([]);
    const [settings, setSettings] = useState(null);
    const [bindings, setBindings] = useState({});

    const load = async () => {
        setLoading(true);
        try {
            const [accRes, banksRes, ppRes, courRes, setRes] = await Promise.all([
                api.get("/accounts?account_type=cash&limit=500"),
                api.get("/accounts?account_type=bank&limit=500"),
                api.get("/accounts?account_type=payment_platform&limit=500"),
                api.get("/accounts?account_type=courier&limit=500"),
                api.get("/settings"),
            ]);
            const dedupe = (arr) => {
                const seen = new Set();
                return arr.filter((a) => {
                    if (!a?.id || seen.has(a.id)) return false;
                    seen.add(a.id);
                    return true;
                });
            };
            const list = dedupe([
                ...(accRes.data?.items || accRes.data || []),
                ...(banksRes.data?.items || banksRes.data || []),
                ...(ppRes.data?.items || ppRes.data || []),
                ...(courRes.data?.items || courRes.data || []),
            ]);
            setAccounts(list);
            setSettings(setRes.data);
            setBindings(setRes.data?.operation_account_bindings || {});
        } catch (e) {
            toast.error("فشل تحميل الإعدادات");
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const isAllowAll = (op) => !Array.isArray(bindings[op]) || bindings[op].length === 0;

    const toggleAllowAll = (op) => {
        setBindings((prev) => {
            const next = { ...prev };
            if (isAllowAll(op)) {
                // Lock down to NO accounts; the merchant will then tick what they want.
                // We use a sentinel list with a single sentinel placeholder to mark
                // "restricted mode but nothing selected yet". Use a real list with
                // a value that does NOT match any account id.
                next[op] = ["__none__"];
            } else {
                delete next[op];
            }
            return next;
        });
    };

    const toggleAccount = (op, accId) => {
        setBindings((prev) => {
            const cur = (Array.isArray(prev[op]) ? prev[op] : [])
                .filter((x) => x !== "__none__");
            const next = { ...prev };
            if (cur.includes(accId)) {
                const remaining = cur.filter((x) => x !== accId);
                next[op] = remaining.length > 0 ? remaining : ["__none__"];
            } else {
                next[op] = [...cur, accId];
            }
            return next;
        });
    };

    const save = async () => {
        if (!settings) return;
        setSaving(true);
        try {
            // Strip out the "__none__" sentinel before persisting; the
            // backend treats empty list as "allow all", so a list with
            // only "__none__" intentionally remains as ["__none__"] —
            // meaning "restricted, nothing allowed yet".
            // We send the bindings as-is; "__none__" is harmless on the
            // backend (it's a string that won't match any real account_id)
            // but to keep the data clean we filter it out and KEEP the
            // sentinel ONLY when the merchant explicitly chose
            // restricted-with-none.
            const cleaned = {};
            for (const [op, accs] of Object.entries(bindings)) {
                if (!Array.isArray(accs) || accs.length === 0) continue;
                const realAccs = accs.filter((a) => a !== "__none__");
                if (realAccs.length === 0) {
                    // Keep an unmatched sentinel so backend still rejects
                    // every account. Stable, non-conflicting id.
                    cleaned[op] = ["__none__"];
                } else {
                    cleaned[op] = realAccs;
                }
            }
            await api.put("/settings", {
                ...settings,
                operation_account_bindings: cleaned,
            });
            toast.success("تم حفظ إعدادات ربط العمليات بالحسابات");
            setBindings(cleaned);
            setSettings({ ...settings, operation_account_bindings: cleaned });
        } catch (e) {
            console.error(e);
            toast.error("فشل الحفظ");
        } finally {
            setSaving(false);
        }
    };

    const sections = useMemo(() => {
        const map = {};
        for (const op of OPS) {
            if (!map[op.section]) map[op.section] = [];
            map[op.section].push(op);
        }
        return map;
    }, []);

    const accountsByType = useMemo(() => {
        const order = ["bank", "cash", "payment_platform", "courier"];
        const out = [];
        for (const t of order) {
            const items = accounts.filter((a) => a.account_type === t);
            if (items.length) out.push({ type: t, items });
        }
        const handled = new Set(order);
        const others = accounts.filter((a) => !handled.has(a.account_type));
        if (others.length) out.push({ type: "other", items: others });
        return out;
    }, [accounts]);

    if (loading) {
        return <div className="p-6 text-center text-slate-500">جاري التحميل…</div>;
    }

    return (
        <div className="p-6 max-w-7xl mx-auto" data-testid="op-account-bindings-page">
            <div className="bg-white rounded-2xl shadow-lg p-6">
                <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                    <div>
                        <h1 className="text-2xl font-extrabold text-slate-900">
                            🔗 إعدادات ربط العمليات بالحسابات المالية
                        </h1>
                        <p className="text-sm text-slate-500 mt-1 leading-relaxed max-w-3xl">
                            لكل نوع عملية، حدِّد الحسابات أو طرق الدفع المسموح استخدامها معها.
                            عند إنشاء حركة مالية ستظهر للمستخدم فقط الحسابات المُسموحة لتلك العملية.
                            «السماح للكل» هو الإعداد الافتراضي للعمليات القديمة — لن يتأثر شيء حتى تختار
                            تقييد عملية بعينها. التحقق مطبَّق في الـ Backend أيضاً، فلن يتمكن أحد من
                            تجاوزه عبر الـ API.
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={save}
                        disabled={saving}
                        className="px-6 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white font-extrabold rounded-lg disabled:opacity-50 shadow-md"
                        data-testid="op-bindings-save-btn"
                    >{saving ? "جاري الحفظ…" : "💾 حفظ الإعدادات"}</button>
                </div>

                {accounts.length === 0 && (
                    <div className="mb-4 bg-amber-50 border-2 border-amber-200 rounded-xl p-4 text-sm text-amber-800"
                         data-testid="op-bindings-no-accounts">
                        ⚠️ لا توجد حسابات مالية بعد. أضف حساب بنكي أو صندوق نقدي من «الأصول والحسابات»
                        قبل ضبط الربط.
                    </div>
                )}

                <div className="space-y-6">
                    {Object.entries(sections).map(([secLabel, ops]) => (
                        <div key={secLabel} className="border-2 border-slate-200 rounded-xl overflow-hidden">
                            <div className="bg-slate-100 px-4 py-2.5 text-sm font-extrabold text-slate-800">
                                {secLabel}
                            </div>
                            <div className="divide-y divide-slate-100">
                                {ops.map((op) => {
                                    const allowAll = isAllowAll(op.value);
                                    const selected = (bindings[op.value] || [])
                                        .filter((x) => x !== "__none__");
                                    const isEmptyRestricted = !allowAll && selected.length === 0;
                                    return (
                                        <div key={op.value} className="p-4"
                                             data-testid={`op-row-${op.value}`}>
                                            <div className="flex flex-wrap items-center justify-between gap-3 mb-2">
                                                <div className="text-sm font-extrabold text-slate-800">
                                                    {op.label}
                                                </div>
                                                <button
                                                    type="button"
                                                    onClick={() => toggleAllowAll(op.value)}
                                                    className={`text-xs font-bold px-3 py-1.5 rounded-lg border-2 transition-colors ${
                                                        allowAll
                                                            ? "bg-emerald-50 border-emerald-300 text-emerald-800 hover:bg-emerald-100"
                                                            : "bg-slate-50 border-slate-300 text-slate-700 hover:bg-slate-100"
                                                    }`}
                                                    data-testid={`op-allow-all-${op.value}`}
                                                >
                                                    {allowAll
                                                        ? "✅ السماح للكل (الافتراضي)"
                                                        : "🔒 وضع التقييد — اضغط للعودة للسماح للكل"}
                                                </button>
                                            </div>

                                            {!allowAll && (
                                                <>
                                                    {isEmptyRestricted && (
                                                        <div className="mb-2 text-[12px] text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-1.5 font-bold"
                                                             data-testid={`op-empty-warn-${op.value}`}>
                                                            ⚠️ هذه العملية مقيَّدة ولا يوجد لها أي حساب مرتبط —
                                                            سيُرفَض كل محاولات إنشائها. اختر حساباً واحداً على الأقل.
                                                        </div>
                                                    )}
                                                    <div className="space-y-3">
                                                        {accountsByType.map(({ type, items }) => (
                                                            <div key={type}>
                                                                <div className="text-[11px] font-bold text-slate-500 mb-1">
                                                                    {accountTypeLabel(type)} ({items.length})
                                                                </div>
                                                                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
                                                                    {items.map((acc) => {
                                                                        const checked = selected.includes(acc.id);
                                                                        return (
                                                                            <label key={acc.id}
                                                                                className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border-2 cursor-pointer transition-colors ${
                                                                                    checked
                                                                                        ? "bg-emerald-50 border-emerald-300"
                                                                                        : "bg-white border-slate-200 hover:border-slate-400"
                                                                                }`}
                                                                                data-testid={`op-acc-${op.value}-${acc.id}`}
                                                                            >
                                                                                <input
                                                                                    type="checkbox"
                                                                                    checked={checked}
                                                                                    onChange={() => toggleAccount(op.value, acc.id)}
                                                                                    className="w-4 h-4 accent-emerald-600"
                                                                                />
                                                                                <span className="text-xs font-bold text-slate-800 truncate">
                                                                                    {acc.name}
                                                                                </span>
                                                                            </label>
                                                                        );
                                                                    })}
                                                                </div>
                                                            </div>
                                                        ))}
                                                    </div>
                                                </>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    ))}
                </div>

                <div className="mt-6 p-4 bg-blue-50 border-2 border-blue-200 rounded-xl text-xs text-blue-900 leading-relaxed">
                    💡 <strong>ملاحظات:</strong>
                    <ul className="list-disc pr-5 mt-1 space-y-1">
                        <li>«السماح للكل» يعني أي حساب مسموح به (السلوك الافتراضي).</li>
                        <li>عند تفعيل وضع التقييد، تظهر في شاشة «حركة مالية جديدة» فقط الحسابات الموسومة هنا.</li>
                        <li>التحقق مطبَّق على مستوى الـ Backend — حتى لو حاول أحد استدعاء الـ API مباشرة بحساب غير مسموح، سيُرفض الطلب.</li>
                        <li>لا يتم حذف أي بيانات قديمة؛ القيود السابقة في الأستاذ تبقى كما هي.</li>
                    </ul>
                </div>
            </div>
        </div>
    );
}
