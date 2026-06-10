/**
 * مركز الإدخال المالي — Financial Input Hub
 *
 * Iter-97 — One-stop entry point so the data-entry user no longer has to
 * navigate between Liabilities / Operating-Expenses / Shipping / Transfers
 * pages. Each tab below is a thin wrapper around an EXISTING endpoint:
 *
 *   1. التزام جديد            → POST /api/liabilities
 *   2. سداد التزام            → POST /api/liabilities/{id}/pay
 *   3. مصروف يومي             → POST /api/operating-expenses/daily   (Iter-94)
 *   4. مديونية على الغير      → POST /api/liabilities (kind=receivable, Iter-97)
 *   5. سلفة موظف              → POST /api/liabilities (kind=salary_advance)
 *   6. دفعة شركة شحن          → POST /api/shipping-accounts/{co}/payments (Iter-95)
 *   7. تحويل COD              → POST /api/transfers (Iter-96)
 *
 * No new collections. No new screens. All seven flows share one shell.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
    Receipt, CreditCard, Truck, Wallet, ArrowsLeftRight,
    HandCoins, Coins, PaperPlaneRight, ListChecks, Bank,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";


const today = () => new Date().toISOString().slice(0, 10);


function fmt(v) {
    return Number(v || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
}


function Field({ label, required, children, full, hint }) {
    return (
        <div className={full ? "sm:col-span-2" : ""}>
            <label className="block text-xs font-bold text-slate-700 mb-1.5">
                {label}{required && <span className="text-rose-600 mr-1">*</span>}
            </label>
            {children}
            {hint && <div className="text-[11px] text-slate-500 mt-1">{hint}</div>}
        </div>
    );
}


const inputCls =
    "w-full px-3 py-2.5 text-sm border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-violet-500";


function SectionCard({ title, Icon, hint, children, onSubmit, busy, submitLabel = "حفظ" }) {
    return (
        <form
            onSubmit={(e) => { e.preventDefault(); onSubmit(); }}
            className="bg-white border border-slate-200 rounded-xl p-5 space-y-4"
        >
            <div className="flex items-center gap-2 pb-3 border-b border-slate-100">
                {Icon && <Icon size={22} weight="duotone" className="text-violet-700" />}
                <div>
                    <h2 className="font-bold text-base text-slate-900">{title}</h2>
                    {hint && <p className="text-xs text-slate-500 mt-0.5">{hint}</p>}
                </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">{children}</div>
            <div className="flex justify-end pt-2">
                <button
                    type="submit"
                    disabled={busy}
                    className="px-5 py-2.5 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-800 disabled:opacity-50"
                    data-testid="hub-submit-btn"
                >
                    {busy ? "جاري الحفظ…" : submitLabel}
                </button>
            </div>
        </form>
    );
}


// ── Tab 1: New liability (ad / supplier) ────────────────────────────
function NewLiabilityForm({ counterparties, onSaved }) {
    const [form, setForm] = useState({
        kind: "supplier",
        ad_provider: "snapchat",
        counterparty_id: "",
        expected_amount: "",
        due_date: today(),
        description: "",
        notes: "",
    });
    const [busy, setBusy] = useState(false);
    // inline create state
    const [newName, setNewName] = useState("");
    const [creating, setCreating] = useState(false);
    const [duplicateSuggestion, setDuplicateSuggestion] = useState(null);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    const cpKind = form.kind === "ad_account" ? "ad_account" : "supplier";
    const filteredCps = counterparties.filter((c) =>
        c.kind === cpKind || (cpKind === "supplier" && c.kind === "general")
    ).filter((c) =>
        form.kind === "ad_account" ? c.ad_provider === form.ad_provider : true
    );

    const createCounterparty = async (force = false) => {
        if (!newName.trim()) { toast.error("أدخل اسماً"); return; }
        setCreating(true);
        try {
            const body = {
                kind: cpKind,
                name: newName.trim(),
                force,
            };
            if (form.kind === "ad_account") body.ad_provider = form.ad_provider;
            const { data } = await api.post("/counterparties", body);
            toast.success(`تم إنشاء ${data.name}`);
            setNewName(""); setDuplicateSuggestion(null);
            set("counterparty_id", data.id);
            onSaved();   // refresh parent list
        } catch (e) {
            const d = e.response?.data?.detail;
            if (typeof d === "object" && d?.message === "similar_name_exists") {
                setDuplicateSuggestion(d.suggestion);
                toast.warning(`اسم مشابه موجود: ${d.suggestion?.name}`);
            } else if (typeof d === "object" && d?.message === "duplicate") {
                toast.error(`هذا الاسم موجود مسبقاً: ${d.existing?.name}`);
                set("counterparty_id", d.existing.id);
            } else {
                toast.error(formatApiErrorDetail(d));
            }
        } finally { setCreating(false); }
    };

    const submit = async () => {
        if (!form.counterparty_id) {
            toast.error("اختر طرفاً من القائمة أو أنشئ واحداً جديداً");
            return;
        }
        if (!form.expected_amount || Number(form.expected_amount) <= 0) {
            toast.error("أدخل مبلغاً صحيحاً"); return;
        }
        setBusy(true);
        try {
            const body = {
                kind: form.kind,
                counterparty_id: form.counterparty_id,
                expected_amount: Number(form.expected_amount),
                due_date: form.due_date,
                description: form.description || "",
                notes: form.notes || "",
            };
            if (form.kind === "ad_account") body.ad_provider = form.ad_provider;
            await api.post("/liabilities", body);
            toast.success("تم تسجيل الالتزام");
            setForm({ ...form, counterparty_id: "", expected_amount: "", description: "", notes: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر الحفظ");
        } finally { setBusy(false); }
    };

    return (
        <SectionCard title="تسجيل التزام جديد" Icon={Receipt} hint="اختر الطرف من القائمة الموحَّدة — لا تكتب الاسم يدوياً" onSubmit={submit} busy={busy}>
            <Field label="نوع الالتزام" required>
                <select value={form.kind} onChange={(e) => { set("kind", e.target.value); set("counterparty_id", ""); }} className={inputCls} data-testid="liab-kind">
                    <option value="supplier">مورد / جهة عامة</option>
                    <option value="ad_account">حساب إعلاني</option>
                </select>
            </Field>
            {form.kind === "ad_account" && (
                <Field label="المنصة" required>
                    <select value={form.ad_provider} onChange={(e) => { set("ad_provider", e.target.value); set("counterparty_id", ""); }} className={inputCls} data-testid="liab-ad-provider">
                        <option value="snapchat">Snapchat</option>
                        <option value="tiktok">TikTok</option>
                        <option value="meta">Meta</option>
                    </select>
                </Field>
            )}
            <Field label={form.kind === "ad_account" ? "اختر الحساب الإعلاني" : "اختر المورد/الجهة"} required full>
                <select value={form.counterparty_id} onChange={(e) => set("counterparty_id", e.target.value)} className={inputCls} data-testid="liab-counterparty">
                    <option value="">— اختر من القائمة الموحَّدة —</option>
                    {filteredCps.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
            </Field>
            <div className="sm:col-span-2 border border-dashed border-slate-300 rounded-lg p-3 bg-slate-50">
                <label className="block text-[11px] font-bold text-slate-700 mb-2">
                    أو إضافة طرف جديد (يُحفظ في القائمة الموحَّدة)
                </label>
                <div className="flex gap-2">
                    <input
                        value={newName}
                        onChange={(e) => { setNewName(e.target.value); setDuplicateSuggestion(null); }}
                        placeholder={form.kind === "ad_account" ? "مثال: Snapchat Account 1" : "اسم المورد"}
                        className={inputCls}
                        data-testid="liab-new-cp-name"
                    />
                    <button type="button" onClick={() => createCounterparty(false)} disabled={creating}
                            className="px-3 py-2 rounded-lg bg-violet-700 text-white text-sm font-bold disabled:opacity-50"
                            data-testid="liab-new-cp-create">
                        إضافة
                    </button>
                </div>
                {duplicateSuggestion && (
                    <div className="mt-2 p-2 bg-amber-50 border border-amber-200 rounded text-xs">
                        ⚠️ قد يكون هذا الطرف موجوداً مسبقاً: <b>{duplicateSuggestion.name}</b>.{" "}
                        <button type="button" onClick={() => { set("counterparty_id", duplicateSuggestion.id); setDuplicateSuggestion(null); setNewName(""); }}
                                className="text-violet-700 underline font-bold">
                            استخدمه
                        </button>{" "}
                        أو{" "}
                        <button type="button" onClick={() => createCounterparty(true)} className="text-rose-700 underline font-bold">
                            أنشئ جديداً رغم التشابه
                        </button>
                    </div>
                )}
            </div>
            <Field label="المبلغ (ر.س)" required>
                <input type="number" step="0.01" min="0" value={form.expected_amount} onChange={(e) => set("expected_amount", e.target.value)} className={`${inputCls} num`} data-testid="liab-amount" />
            </Field>
            <Field label="تاريخ الاستحقاق" required>
                <input type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} className={inputCls} data-testid="liab-due-date" />
            </Field>
            <Field label="الوصف" full>
                <input value={form.description} onChange={(e) => set("description", e.target.value)} className={inputCls} data-testid="liab-desc" />
            </Field>
            <Field label="ملاحظات" full>
                <input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="liab-notes" />
            </Field>
        </SectionCard>
    );
}


// ── Tab 2: Pay liability ────────────────────────────────────────────
function PayLiabilityForm({ openLiabilities, banks, onSaved }) {
    const [form, setForm] = useState({
        liability_id: "", paid_from_account_id: "", amount: "", payment_date: today(), notes: "",
    });
    const [busy, setBusy] = useState(false);
    // Iter-118 — searchable counterparty picker (replaces big dropdown)
    const [query, setQuery] = useState("");
    const [showResults, setShowResults] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const selected = openLiabilities.find((l) => l.id === form.liability_id);

    // Filter liabilities by counterparty name / description / kind.
    // Limited to 8 results to keep the dropdown compact and fast.
    const searchResults = (() => {
        const q = (query || "").trim().toLowerCase();
        if (!q) return [];
        return openLiabilities.filter((l) => {
            const haystack = [
                l.counterparty_name, l.description, l.kind,
                l.notes, l.counterparty_id,
            ].filter(Boolean).join(" ").toLowerCase();
            return haystack.includes(q);
        }).slice(0, 8);
    })();

    const KIND_LABEL = {
        salary: "راتب موظف",
        salary_advance: "سلفة موظف",
        supplier: "مورد",
        ad_account: "حساب إعلاني",
        receivable: "مستحقات لنا",
        general: "عام",
    };

    const pickLiability = (l) => {
        set("liability_id", l.id);
        setQuery(l.counterparty_name || l.description || l.kind || "");
        setShowResults(false);
    };

    const clearSelection = () => {
        set("liability_id", "");
        setQuery("");
        setShowResults(false);
    };
    // Iter-102 — show inline days-worked editor for salary kind only.
    // REMOVED in Iter-118 (user request): no longer compute salary
    // based on actual work days; merchant pays the fixed monthly amount.

    const submit = async () => {
        if (!form.liability_id) { toast.error("اختر الالتزام"); return; }
        if (!form.paid_from_account_id) { toast.error("اختر الحساب البنكي"); return; }
        const amt = Number(form.amount);
        if (!amt || amt <= 0) { toast.error("أدخل مبلغاً صحيحاً"); return; }
        if (selected && amt > Number(selected.remaining_amount) + 0.01) {
            toast.error(`المبلغ أكبر من المتبقي (${fmt(selected.remaining_amount)})`);
            return;
        }
        setBusy(true);
        try {
            await api.post(`/liabilities/${form.liability_id}/pay`, {
                amount: amt,
                paid_from_account_id: form.paid_from_account_id,
                payment_date: form.payment_date,
                notes: form.notes,
            });
            toast.success("تم تسجيل السداد وخصمه من البنك");
            setForm({ ...form, amount: "", notes: "", liability_id: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };

    return (
        <SectionCard
            title="سداد التزام قائم"
            Icon={CreditCard}
            hint="يخصم تلقائياً من رصيد البنك المختار ويُحدِّث المركز المالي"
            onSubmit={submit}
            busy={busy}
            submitLabel="تسجيل السداد"
        >
            <Field label="الالتزام (ابحث باسم الموظف / المورد)" required full>
                <div className="relative">
                    <input
                        type="text"
                        value={query}
                        onChange={(e) => { setQuery(e.target.value); setShowResults(true); if (form.liability_id && e.target.value !== (selected?.counterparty_name || selected?.description || "")) set("liability_id", ""); }}
                        onFocus={() => setShowResults(true)}
                        onBlur={() => setTimeout(() => setShowResults(false), 200)}
                        placeholder="اكتب اسم الموظف أو المورد أو العميل…"
                        className={inputCls}
                        data-testid="pay-liability-search"
                        autoComplete="off"
                    />
                    {form.liability_id && (
                        <button
                            type="button"
                            onClick={clearSelection}
                            className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-rose-600 text-lg leading-none"
                            data-testid="pay-liability-clear"
                            title="إلغاء الاختيار"
                        >
                            ✕
                        </button>
                    )}

                    {/* Results dropdown */}
                    {showResults && query.trim() && (
                        <div
                            className="absolute z-20 mt-1 left-0 right-0 bg-white border border-slate-200 rounded-lg shadow-lg max-h-72 overflow-y-auto"
                            data-testid="pay-liability-results"
                        >
                            {searchResults.length === 0 ? (
                                <div className="p-3 text-xs text-slate-500 text-center">
                                    لا توجد نتائج تطابق «{query}». تأكد من وجود التزام مفتوح لهذا الاسم.
                                </div>
                            ) : (
                                searchResults.map((l) => (
                                    <button
                                        key={l.id}
                                        type="button"
                                        onMouseDown={(e) => e.preventDefault()}
                                        onClick={() => pickLiability(l)}
                                        className="w-full text-right p-3 hover:bg-slate-50 border-b border-slate-100 last:border-0 flex items-center justify-between gap-3"
                                        data-testid={`pay-liability-result-${l.id}`}
                                    >
                                        <div className="flex-1 min-w-0">
                                            <div className="font-bold text-slate-900 text-sm truncate">
                                                {l.counterparty_name || l.description || "—"}
                                            </div>
                                            <div className="text-[11px] text-slate-500 mt-0.5 flex items-center gap-2 flex-wrap">
                                                <span className="px-1.5 py-0.5 bg-slate-100 rounded text-slate-700">
                                                    {KIND_LABEL[l.kind] || l.kind}
                                                </span>
                                                {l.description && l.counterparty_name && (
                                                    <span className="truncate">{l.description}</span>
                                                )}
                                                {l.is_overdue && (
                                                    <span className="text-rose-600 font-bold">⚠ متأخر</span>
                                                )}
                                            </div>
                                        </div>
                                        <div className="text-end flex-shrink-0">
                                            <div className="text-[10px] text-slate-400">متبقٍ</div>
                                            <div className={`font-extrabold num text-sm ${
                                                Number(l.remaining_amount) > 0 ? "text-rose-700" : "text-emerald-700"
                                            }`}>
                                                {fmt(l.remaining_amount)} ر.س
                                            </div>
                                        </div>
                                    </button>
                                ))
                            )}
                        </div>
                    )}
                </div>
            </Field>

            {/* Iter-118 — balance card shows what's owed / paid for the picked counterparty */}
            {selected && (() => {
                // Aggregate ALL open liabilities for the SAME counterparty so
                // the merchant sees the cumulative balance, not just one row.
                const cpKey = (selected.counterparty_name || "").trim().toLowerCase();
                const sameCounterparty = cpKey
                    ? openLiabilities.filter((l) =>
                        (l.counterparty_name || "").trim().toLowerCase() === cpKey
                      )
                    : [selected];
                const sumExpected = sameCounterparty.reduce((s, l) => s + (Number(l.expected_amount) || 0), 0);
                const sumPaid = sameCounterparty.reduce((s, l) => s + (Number(l.paid_amount) || 0), 0);
                const sumRemaining = sameCounterparty.reduce((s, l) => s + (Number(l.remaining_amount) || 0), 0);
                const overdueCount = sameCounterparty.filter((l) => l.is_overdue).length;
                const hasMultiple = sameCounterparty.length > 1;

                return (
                <div
                    className="sm:col-span-2 p-3 rounded-lg bg-emerald-50 border-2 border-emerald-200"
                    data-testid="pay-liability-balance-card"
                >
                    <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
                        <div>
                            <div className="text-xs text-slate-500">تم اختيار:</div>
                            <div className="text-base font-extrabold text-slate-900">
                                {selected.counterparty_name || selected.description || "—"}
                                <span className="ms-2 text-xs text-slate-600 font-normal">
                                    ({KIND_LABEL[selected.kind] || selected.kind})
                                </span>
                            </div>
                        </div>
                        {(selected.is_overdue || overdueCount > 0) && (
                            <span className="px-2 py-1 bg-rose-600 text-white text-[11px] font-bold rounded-full">
                                ⚠ {overdueCount > 1 ? `${overdueCount} مستحقات متأخرة` : "متأخر السداد"}
                            </span>
                        )}
                    </div>

                    {/* Per-liability breakdown (the one selected) */}
                    <div className="text-[11px] text-slate-500 mb-1">
                        الالتزام المحدد:
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs mb-3">
                        <div className="bg-white border border-slate-200 rounded p-2 text-center">
                            <div className="text-[10px] text-slate-500">المبلغ المتوقع</div>
                            <div className="num font-extrabold text-slate-900 text-sm">{fmt(selected.expected_amount)}</div>
                        </div>
                        <div className="bg-white border border-slate-200 rounded p-2 text-center">
                            <div className="text-[10px] text-slate-500">المدفوع حتى الآن</div>
                            <div className="num font-extrabold text-emerald-700 text-sm">{fmt(selected.paid_amount)}</div>
                        </div>
                        <div className="bg-white border-2 border-rose-300 rounded p-2 text-center">
                            <div className="text-[10px] text-rose-700">المتبقي</div>
                            <div className="num font-extrabold text-rose-700 text-base">{fmt(selected.remaining_amount)}</div>
                        </div>
                        <div className="bg-white border border-slate-200 rounded p-2 text-center">
                            <div className="text-[10px] text-slate-500">تاريخ الاستحقاق</div>
                            <div className="num font-bold text-slate-700 text-sm">{selected.due_date || "—"}</div>
                        </div>
                    </div>

                    {/* Iter-118 — Cumulative totals across ALL open liabilities of this counterparty */}
                    {hasMultiple && (
                        <>
                            <div className="text-[11px] font-bold text-slate-700 mb-1 mt-2 flex items-center gap-1">
                                <span className="px-1.5 py-0.5 bg-violet-100 text-violet-800 rounded text-[10px] font-bold">
                                    {sameCounterparty.length} التزامات مفتوحة
                                </span>
                                📊 الرصيد التراكمي لـ {selected.counterparty_name}:
                            </div>
                            <div className="grid grid-cols-3 gap-2 text-xs">
                                <div className="bg-slate-900 text-white rounded p-2 text-center">
                                    <div className="text-[10px] text-slate-300">إجمالي مستحق</div>
                                    <div className="num font-extrabold text-base">{fmt(sumExpected)}</div>
                                </div>
                                <div className="bg-emerald-700 text-white rounded p-2 text-center">
                                    <div className="text-[10px] text-emerald-100">إجمالي مدفوع</div>
                                    <div className="num font-extrabold text-base">{fmt(sumPaid)}</div>
                                </div>
                                <div className="bg-rose-700 text-white rounded p-2 text-center">
                                    <div className="text-[10px] text-rose-100">إجمالي المتبقي</div>
                                    <div className="num font-extrabold text-base">{fmt(sumRemaining)}</div>
                                </div>
                            </div>

                            {/* List of all open liabilities for this counterparty */}
                            <div className="mt-2 text-[11px] bg-white border border-slate-200 rounded p-2 max-h-32 overflow-y-auto">
                                <div className="font-bold text-slate-700 mb-1">قائمة الالتزامات المفتوحة:</div>
                                {sameCounterparty.map((l) => (
                                    <div key={l.id} className={`flex items-center justify-between py-1 border-b border-slate-100 last:border-0 ${l.id === selected.id ? "bg-amber-50 -mx-2 px-2" : ""}`}>
                                        <span className="text-slate-600">
                                            {KIND_LABEL[l.kind] || l.kind} · {l.description || l.due_date || "—"}
                                            {l.id === selected.id && <span className="ms-1 text-[9px] text-amber-700 font-bold">(محدد)</span>}
                                        </span>
                                        <span className="num font-bold text-rose-700">{fmt(l.remaining_amount)}</span>
                                    </div>
                                ))}
                            </div>
                        </>
                    )}

                    {selected.description && selected.counterparty_name && (
                        <div className="mt-2 text-[11px] text-slate-600 bg-white/60 p-2 rounded">
                            📝 {selected.description}
                        </div>
                    )}
                </div>
                );
            })()}

            {/* Iter-118 — Removed: salary calculation by actual work days
                (per user request: ماله حاجة). Merchant pays the fixed
                monthly amount directly. */}

            <Field label="الحساب البنكي" required>
                <select value={form.paid_from_account_id} onChange={(e) => set("paid_from_account_id", e.target.value)} className={inputCls} data-testid="pay-account">
                    <option value="">— اختر بنكاً —</option>
                    {banks.map((b) => (
                        <option key={b.id} value={b.id}>{b.name} (الرصيد: {fmt(b.current_balance)} ر.س)</option>
                    ))}
                </select>
            </Field>
            <Field label="مبلغ السداد (ر.س)" required>
                <input type="number" step="0.01" min="0" value={form.amount} onChange={(e) => set("amount", e.target.value)} className={`${inputCls} num`} data-testid="pay-amount" />
            </Field>
            <Field label="تاريخ السداد" required>
                <input type="date" value={form.payment_date} onChange={(e) => set("payment_date", e.target.value)} className={inputCls} data-testid="pay-date" />
            </Field>
            <Field label="ملاحظات" full>
                <input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="pay-notes" />
            </Field>
        </SectionCard>
    );
}


// ── Tab 3: Daily expense (reuses Iter-94 endpoint) ───────────────────
function DailyExpenseForm({ banks, onSaved }) {
    const [form, setForm] = useState({
        date: today(), expense_type: "", description: "", amount: "",
        paid_from_account_id: "", payment_method: "", notes: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const submit = async () => {
        if (!form.expense_type.trim()) { toast.error("نوع المصروف مطلوب"); return; }
        if (!Number(form.amount)) { toast.error("أدخل المبلغ"); return; }
        setBusy(true);
        try {
            await api.post("/operating-expenses/daily", {
                date: form.date,
                expense_type: form.expense_type.trim(),
                description: form.description.trim(),
                amount: Number(form.amount),
                paid_from_account_id: form.paid_from_account_id || null,
                payment_method: form.payment_method.trim(),
                notes: form.notes.trim(),
            });
            if (form.paid_from_account_id) {
                toast.success("تم تسجيل المصروف وخصمه من البنك");
            } else {
                toast.warning("تم تسجيل المصروف نقداً (لن يُخصم من أي بنك).");
            }
            setForm({ ...form, expense_type: "", description: "", amount: "", notes: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };
    return (
        <SectionCard title="مصروف يومي" Icon={Wallet} hint="عند اختيار حساب بنكي يُخصم المبلغ تلقائياً" onSubmit={submit} busy={busy}>
            <Field label="التاريخ" required><input type="date" value={form.date} onChange={(e) => set("date", e.target.value)} className={inputCls} data-testid="dexp-date" /></Field>
            <Field label="نوع المصروف" required><input value={form.expense_type} onChange={(e) => set("expense_type", e.target.value)} className={inputCls} placeholder="وقود، صيانة، اشتراك…" data-testid="dexp-type" /></Field>
            <Field label="المبلغ (ر.س)" required><input type="number" step="0.01" min="0" value={form.amount} onChange={(e) => set("amount", e.target.value)} className={`${inputCls} num`} data-testid="dexp-amount" /></Field>
            <Field label="الحساب المدفوع منه">
                <select value={form.paid_from_account_id} onChange={(e) => set("paid_from_account_id", e.target.value)} className={inputCls} data-testid="dexp-account">
                    <option value="">— نقدي (بدون حساب) —</option>
                    {banks.map((b) => <option key={b.id} value={b.id}>{b.name} ({fmt(b.current_balance)} ر.س)</option>)}
                </select>
            </Field>
            <Field label="الوصف" full><input value={form.description} onChange={(e) => set("description", e.target.value)} className={inputCls} data-testid="dexp-desc" /></Field>
            <Field label="ملاحظات" full><input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="dexp-notes" /></Field>
        </SectionCard>
    );
}


// ── Tab 4: Receivable (Iter-97) ─────────────────────────────────────
function ReceivableForm({ onSaved }) {
    const [form, setForm] = useState({
        counterparty_name: "", counterparty_type: "person",
        expected_amount: "", due_date: today(),
        description: "", notes: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const submit = async () => {
        if (!form.counterparty_name.trim()) { toast.error("اسم الجهة مطلوب"); return; }
        if (!Number(form.expected_amount)) { toast.error("أدخل المبلغ"); return; }
        setBusy(true);
        try {
            await api.post("/liabilities", {
                kind: "receivable",
                counterparty_name: form.counterparty_name.trim(),
                counterparty_type: form.counterparty_type,
                expected_amount: Number(form.expected_amount),
                due_date: form.due_date,
                description: form.description,
                notes: form.notes,
            });
            toast.success("تم تسجيل المديونية ضمن الأصول المستحقة التحصيل");
            setForm({ ...form, counterparty_name: "", expected_amount: "", description: "", notes: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };
    return (
        <SectionCard title="مديونية على شخص أو جهة" Icon={HandCoins} hint="مبلغ مستحق على الغير — يُحسب كأصل مستحق التحصيل" onSubmit={submit} busy={busy}>
            <Field label="نوع الجهة" required>
                <select value={form.counterparty_type} onChange={(e) => set("counterparty_type", e.target.value)} className={inputCls} data-testid="rec-type">
                    <option value="customer">عميل</option>
                    <option value="employee">موظف (عهدة)</option>
                    <option value="person">شخص</option>
                    <option value="company">جهة/شركة</option>
                </select>
            </Field>
            <Field label="الاسم" required>
                <input value={form.counterparty_name} onChange={(e) => set("counterparty_name", e.target.value)} className={inputCls} data-testid="rec-name" />
            </Field>
            <Field label="المبلغ (ر.س)" required>
                <input type="number" step="0.01" min="0" value={form.expected_amount} onChange={(e) => set("expected_amount", e.target.value)} className={`${inputCls} num`} data-testid="rec-amount" />
            </Field>
            <Field label="تاريخ الاستحقاق" required>
                <input type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} className={inputCls} data-testid="rec-due" />
            </Field>
            <Field label="الوصف" full>
                <input value={form.description} onChange={(e) => set("description", e.target.value)} className={inputCls} placeholder="مثال: قرض قصير، عهدة معدّات…" data-testid="rec-desc" />
            </Field>
            <Field label="ملاحظات" full><input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="rec-notes" /></Field>
        </SectionCard>
    );
}


// ── Tab 5: Salary advance ───────────────────────────────────────────
function AdvanceForm({ employees, banks, openLiabilities, onSaved }) {
    const [form, setForm] = useState({
        employee_salary_id: "", paid_from_account_id: "", expected_amount: "",
        due_date: today(), description: "", notes: "",
    });
    // Iter-125 — search-based employee picker (mirrors Pay Liability UX).
    const [empQuery, setEmpQuery] = useState("");
    const [empOpen, setEmpOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    const selectedEmployee = employees.find((e) => e.id === form.employee_salary_id) || null;

    const empResults = (() => {
        const q = empQuery.trim().toLowerCase();
        if (!q) return [];
        return employees
            .filter((e) => (e.name || "").toLowerCase().includes(q))
            .slice(0, 8);
    })();

    // Cumulative open salary_advance balance for the selected employee.
    const openAdvances = selectedEmployee
        ? (openLiabilities || []).filter(
              (l) => l.kind === "salary_advance"
                  && l.counterparty_name === selectedEmployee.name
                  && (Number(l.remaining_amount) || 0) > 0,
          )
        : [];
    const sumAdvanceRemaining = openAdvances.reduce(
        (s, l) => s + (Number(l.remaining_amount) || 0), 0,
    );
    const sumAdvanceExpected = openAdvances.reduce(
        (s, l) => s + (Number(l.expected_amount) || 0), 0,
    );
    const sumAdvancePaid = openAdvances.reduce(
        (s, l) => s + (Number(l.paid_amount) || 0), 0,
    );

    const submit = async () => {
        if (!form.employee_salary_id) { toast.error("اختر الموظف"); return; }
        if (!form.paid_from_account_id) { toast.error("اختر الحساب البنكي"); return; }
        if (!Number(form.expected_amount)) { toast.error("أدخل المبلغ"); return; }
        setBusy(true);
        try {
            await api.post("/liabilities", {
                kind: "salary_advance",
                employee_salary_id: form.employee_salary_id,
                paid_from_account_id: form.paid_from_account_id,
                expected_amount: Number(form.expected_amount),
                due_date: form.due_date,
                description: form.description,
                notes: form.notes,
            });
            toast.success("تم صرف السلفة وخصمها من البنك. ستُخصم من راتب الشهر القادم تلقائياً.");
            setForm({ ...form, expected_amount: "", description: "", notes: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };
    return (
        <SectionCard title="سلفة موظف" Icon={Coins} hint="تُخصم فوراً من البنك وتُستهلَك تلقائياً من راتب الشهر القادم" onSubmit={submit} busy={busy}>
            <Field label="الموظف (ابحث بالاسم)" required full>
                <div className="relative">
                    <input
                        type="text"
                        value={selectedEmployee ? selectedEmployee.name : empQuery}
                        onChange={(e) => {
                            setEmpQuery(e.target.value);
                            setEmpOpen(true);
                            if (form.employee_salary_id) set("employee_salary_id", "");
                        }}
                        onFocus={() => setEmpOpen(true)}
                        placeholder="اكتب اسم الموظف…"
                        className={inputCls}
                        data-testid="adv-employee-search"
                    />
                    {selectedEmployee && (
                        <button
                            type="button"
                            onClick={() => {
                                set("employee_salary_id", "");
                                setEmpQuery("");
                                setEmpOpen(false);
                            }}
                            className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-rose-500 text-lg"
                            data-testid="adv-employee-clear"
                            title="إلغاء"
                        >✕</button>
                    )}
                    {empOpen && empResults.length > 0 && !selectedEmployee && (
                        <ul
                            className="absolute z-30 mt-1 w-full max-h-64 overflow-auto rounded-lg border-2 border-slate-200 bg-white shadow-lg"
                            data-testid="adv-employee-results"
                        >
                            {empResults.map((e) => (
                                <li key={e.id}>
                                    <button
                                        type="button"
                                        onClick={() => {
                                            set("employee_salary_id", e.id);
                                            setEmpQuery("");
                                            setEmpOpen(false);
                                        }}
                                        className="w-full text-right px-3 py-2 hover:bg-emerald-50 text-sm flex items-center justify-between gap-2"
                                        data-testid={`adv-employee-pick-${e.id}`}
                                    >
                                        <span className="font-bold">{e.name}</span>
                                        <span className="text-xs text-slate-500 num">{fmt(e.monthly_amount)} ر.س/شهر</span>
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}
                    {empOpen && empQuery.trim() && empResults.length === 0 && !selectedEmployee && (
                        <div className="absolute z-30 mt-1 w-full rounded-lg border-2 border-slate-200 bg-white shadow-lg px-3 py-2 text-xs text-slate-500">
                            لا توجد نتائج تطابق «{empQuery}».
                        </div>
                    )}
                </div>
            </Field>

            {/* Iter-125 — Cumulative advance balance card */}
            {selectedEmployee && (
                <div
                    className="md:col-span-2 mt-2 rounded-xl border-2 border-amber-300 bg-amber-50 p-3"
                    data-testid="adv-cumulative-card"
                >
                    <h5 className="text-xs font-extrabold text-amber-900 mb-2 flex items-center gap-2">
                        💰 الرصيد التراكمي لسلف {selectedEmployee.name}
                        <span className="text-[10px] font-normal text-amber-700">
                            (راتب شهري: <span className="num">{fmt(selectedEmployee.monthly_amount)}</span> ر.س)
                        </span>
                    </h5>
                    {openAdvances.length === 0 ? (
                        <div className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-3 py-2">
                            ✅ لا توجد سلف مفتوحة على هذا الموظف حالياً.
                        </div>
                    ) : (
                        <>
                            <div className="grid grid-cols-3 gap-2 mb-2">
                                <div className="bg-white rounded-lg p-2 text-center">
                                    <div className="text-[10px] text-slate-500 mb-1">إجمالي مستحق</div>
                                    <div className="num font-extrabold text-sm text-slate-800">{fmt(sumAdvanceExpected)}</div>
                                </div>
                                <div className="bg-white rounded-lg p-2 text-center">
                                    <div className="text-[10px] text-slate-500 mb-1">المخصوم</div>
                                    <div className="num font-extrabold text-sm text-emerald-700">{fmt(sumAdvancePaid)}</div>
                                </div>
                                <div className="bg-white rounded-lg p-2 text-center border-2 border-rose-300">
                                    <div className="text-[10px] text-slate-500 mb-1">متبقّي عليه</div>
                                    <div className="num font-extrabold text-sm text-rose-700">{fmt(sumAdvanceRemaining)}</div>
                                </div>
                            </div>
                            <div className="text-[10px] text-amber-800">
                                ⚠ لديه {openAdvances.length} سلفة مفتوحة — مجموع المتبقي سيُخصم تدريجياً من راتبه القادم.
                            </div>
                        </>
                    )}
                </div>
            )}

            <Field label="الحساب البنكي" required>
                <select value={form.paid_from_account_id} onChange={(e) => set("paid_from_account_id", e.target.value)} className={inputCls} data-testid="adv-bank">
                    <option value="">— اختر بنكاً —</option>
                    {banks.map((b) => <option key={b.id} value={b.id}>{b.name} ({fmt(b.current_balance)} ر.س)</option>)}
                </select>
            </Field>
            <Field label="المبلغ (ر.س)" required>
                <input type="number" step="0.01" min="0" value={form.expected_amount} onChange={(e) => set("expected_amount", e.target.value)} className={`${inputCls} num`} data-testid="adv-amount" />
            </Field>
            <Field label="تاريخ الصرف" required>
                <input type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} className={inputCls} data-testid="adv-date" />
            </Field>
            <Field label="الوصف" full><input value={form.description} onChange={(e) => set("description", e.target.value)} className={inputCls} data-testid="adv-desc" /></Field>
            <Field label="ملاحظات" full><input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="adv-notes" /></Field>
        </SectionCard>
    );
}


// ── Tab 6: Shipping payment ─────────────────────────────────────────
function ShippingPaymentForm({ banks, onSaved }) {
    const [form, setForm] = useState({
        company_name: "", amount: "", payment_date: today(),
        invoice_number: "", paid_from_account_id: "", note: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const submit = async () => {
        if (!form.company_name.trim()) { toast.error("اسم شركة الشحن مطلوب"); return; }
        if (!Number(form.amount)) { toast.error("أدخل المبلغ"); return; }
        setBusy(true);
        try {
            await api.post(`/shipping-accounts/${encodeURIComponent(form.company_name.trim())}/payments`, {
                amount: Number(form.amount),
                payment_date: form.payment_date,
                invoice_number: form.invoice_number,
                paid_from_account_id: form.paid_from_account_id || null,
                note: form.note,
            });
            if (form.paid_from_account_id) {
                toast.success("تم تسجيل الدفعة وخصمها من البنك");
            } else {
                toast.warning("تم تسجيل الدفعة بدون ربطها بحساب بنكي، لذلك لن تؤثر على رصيد البنك.");
            }
            setForm({ ...form, amount: "", invoice_number: "", note: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };
    return (
        <SectionCard title="دفعة شركة شحن" Icon={Truck} hint="تُسدَّد للمستحقات الآجلة للشركة وتُخصم من البنك المختار" onSubmit={submit} busy={busy}>
            <Field label="شركة الشحن" required>
                <input list="ship-co" value={form.company_name} onChange={(e) => set("company_name", e.target.value)} className={inputCls} placeholder="سمسا، أيميل، مندوب الرياض…" data-testid="ship-company" />
                <datalist id="ship-co">
                    <option value="سمسا" />
                    <option value="أيميل" />
                    <option value="مندوب الرياض" />
                    <option value="Aramex" />
                    <option value="SPL" />
                </datalist>
            </Field>
            <Field label="رقم الفاتورة">
                <input value={form.invoice_number} onChange={(e) => set("invoice_number", e.target.value)} className={inputCls} data-testid="ship-invoice" />
            </Field>
            <Field label="المبلغ (ر.س)" required>
                <input type="number" step="0.01" min="0" value={form.amount} onChange={(e) => set("amount", e.target.value)} className={`${inputCls} num`} data-testid="ship-amount" />
            </Field>
            <Field label="تاريخ الدفع" required>
                <input type="date" value={form.payment_date} onChange={(e) => set("payment_date", e.target.value)} className={inputCls} data-testid="ship-date" />
            </Field>
            <Field label="الحساب البنكي" hint="يُنصح بشدة باختيار حساب بنكي ليُخصم المبلغ ويظهر الأثر في المركز المالي">
                <select value={form.paid_from_account_id} onChange={(e) => set("paid_from_account_id", e.target.value)} className={inputCls} data-testid="ship-account">
                    <option value="">— بدون ربط بحساب —</option>
                    {banks.map((b) => <option key={b.id} value={b.id}>{b.name} ({fmt(b.current_balance)} ر.س)</option>)}
                </select>
            </Field>
            <Field label="ملاحظات" full><input value={form.note} onChange={(e) => set("note", e.target.value)} className={inputCls} data-testid="ship-note" /></Field>
        </SectionCard>
    );
}


// ── Tab 7: COD transfer ─────────────────────────────────────────────
function CodTransferForm({ accounts, banks, onSaved }) {
    const codAccounts = accounts.filter((a) => a.normalized_payment_method === "cash_on_delivery");
    const [companies, setCompanies] = useState([]);
    const [form, setForm] = useState({
        from_account_id: "", to_account_id: "", transfer_date: today(),
        reference: "", shipping_company: "", notes: "",
        // Iter-98 Net-COD
        cod_gross_collected: "", shipping_fee_deducted: "",
        shipping_fee_settles_against: "shipping_payable",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    useEffect(() => {
        api.get("/shipping-accounts/companies")
            .then((r) => setCompanies(r.data?.items || []))
            .catch(() => setCompanies([]));
    }, []);

    const gross = Number(form.cod_gross_collected) || 0;
    const fee = Number(form.shipping_fee_deducted) || 0;
    const net = Math.max(0, Math.round((gross - fee) * 100) / 100);
    const useNet = gross > 0;

    const submit = async () => {
        if (!form.from_account_id) return toast.error("اختر حساب الدفع عند الاستلام");
        if (!form.to_account_id) return toast.error("اختر البنك الوجهة");
        if (!form.shipping_company.trim()) return toast.error("اختر شركة الشحن");
        if (!gross) return toast.error("أدخل إجمالي COD المحصَّل");
        if (fee > gross) return toast.error("الرسوم أكبر من الإجمالي");
        setBusy(true);
        try {
            await api.post("/transfers", {
                from_account_id: form.from_account_id,
                to_account_id: form.to_account_id,
                amount: net,
                transfer_date: form.transfer_date,
                reference: form.reference,
                shipping_company: form.shipping_company.trim(),
                notes: form.notes,
                cod_gross_collected: gross,
                shipping_fee_deducted: fee,
                shipping_fee_settles_against: form.shipping_fee_settles_against,
            });
            toast.success(`تم: COD ${gross} − رسوم ${fee} = صافي ${net} ر.س محوَّل للبنك`);
            setForm({ ...form, cod_gross_collected: "", shipping_fee_deducted: "", reference: "", notes: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };

    return (
        <SectionCard title="تحويل من الدفع عند الاستلام إلى بنك" Icon={ArrowsLeftRight} hint="إجمالي ما حصَّلته شركة الشحن، رسومها المخصومة، والصافي المحوَّل" onSubmit={submit} busy={busy}>
            <Field label="من حساب الدفع عند الاستلام" required>
                <select value={form.from_account_id} onChange={(e) => set("from_account_id", e.target.value)} className={inputCls} data-testid="cod-from">
                    <option value="">— اختر حساب COD —</option>
                    {codAccounts.map((a) => <option key={a.id} value={a.id}>{a.name} (متوقَّع: {fmt(a.expected_orders_balance)} ر.س)</option>)}
                </select>
            </Field>
            <Field label="إلى بنك" required>
                <select value={form.to_account_id} onChange={(e) => set("to_account_id", e.target.value)} className={inputCls} data-testid="cod-to">
                    <option value="">— اختر بنكاً —</option>
                    {banks.map((b) => <option key={b.id} value={b.id}>{b.name} ({fmt(b.current_balance)} ر.س)</option>)}
                </select>
            </Field>
            <Field label="شركة الشحن" required full>
                <select value={form.shipping_company} onChange={(e) => set("shipping_company", e.target.value)} className={inputCls} data-testid="cod-shipping">
                    <option value="">— اختر شركة شحن —</option>
                    {companies.map((c) => (
                        <option key={c.canonical} value={c.display}>
                            {c.display}{c.usage_count > 0 ? `  (${c.usage_count} مرة)` : ""}
                        </option>
                    ))}
                </select>
                <div className="text-[11px] text-slate-500 mt-1">القائمة موحَّدة تلقائياً — لا تكرار في الأسماء.</div>
            </Field>
            <Field label="إجمالي COD المحصَّل (ر.س)" required hint="ما حصَّلته شركة الشحن من العملاء فعلاً">
                <input type="number" step="0.01" min="0" value={form.cod_gross_collected} onChange={(e) => set("cod_gross_collected", e.target.value)} className={`${inputCls} num`} data-testid="cod-gross" />
            </Field>
            <Field label="رسوم شركة الشحن المخصومة (ر.س)" hint="اتركها 0 إذا لم تخصم شركة الشحن أي رسوم">
                <input type="number" step="0.01" min="0" value={form.shipping_fee_deducted} onChange={(e) => set("shipping_fee_deducted", e.target.value)} className={`${inputCls} num`} data-testid="cod-fee" />
            </Field>
            <Field label="صافي المحوَّل للبنك (ر.س)" hint="يُحسب تلقائياً">
                <input type="number" value={net.toFixed(2)} readOnly className={`${inputCls} num bg-slate-100 font-bold`} data-testid="cod-net" />
            </Field>
            {fee > 0 && (
                <Field label="معالجة الرسوم" full>
                    <select value={form.shipping_fee_settles_against} onChange={(e) => set("shipping_fee_settles_against", e.target.value)} className={inputCls} data-testid="cod-settle-mode">
                        <option value="shipping_payable">يخصم من ديون شركة الشحن (الافتراضي — الأصح محاسبياً)</option>
                        <option value="expense">مصروف شحن جديد (إذا لم تكن هناك فاتورة سابقة)</option>
                    </select>
                </Field>
            )}
            <Field label="تاريخ التحويل" required>
                <input type="date" value={form.transfer_date} onChange={(e) => set("transfer_date", e.target.value)} className={inputCls} data-testid="cod-date" />
            </Field>
            <Field label="رقم المرجع">
                <input value={form.reference} onChange={(e) => set("reference", e.target.value)} className={inputCls} data-testid="cod-ref" />
            </Field>
            <Field label="ملاحظات" full>
                <input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="cod-notes" />
            </Field>
        </SectionCard>
    );
}


// ── Main Hub ────────────────────────────────────────────────────────
const TABS = [
    { id: "new-liab",   label: "التزام جديد",        Icon: Receipt,         testid: "hub-tab-new-liab" },
    { id: "pay-liab",   label: "سداد التزام",        Icon: CreditCard,      testid: "hub-tab-pay-liab" },
    { id: "daily-exp",  label: "مصروف يومي",         Icon: Wallet,          testid: "hub-tab-daily-exp" },
    { id: "receivable", label: "مديونية على الغير",   Icon: HandCoins,       testid: "hub-tab-receivable" },
    { id: "advance",    label: "سلفة موظف",          Icon: Coins,           testid: "hub-tab-advance" },
    { id: "shipping",   label: "دفعة شركة شحن",      Icon: Truck,           testid: "hub-tab-shipping" },
    { id: "cod",        label: "تحويل COD",          Icon: ArrowsLeftRight, testid: "hub-tab-cod" },
];


export default function FinancialInputHub() {
    const [tab, setTab] = useState("new-liab");
    const [accounts, setAccounts] = useState([]);
    const [employees, setEmployees] = useState([]);
    const [openLiabilities, setOpenLiabilities] = useState([]);
    const [counterparties, setCounterparties] = useState([]);
    const [loading, setLoading] = useState(true);

    const banks = useMemo(
        () => accounts.filter((a) => a.account_type === "bank" && a.status !== "hidden" && a.status !== "inactive"),
        [accounts],
    );

    const load = async () => {
        setLoading(true);
        try {
            const [accRes, empRes, unpaidRes, partialRes, cpsRes] = await Promise.all([
                api.get("/accounts"),
                api.get("/operating-expenses/salaries"),
                api.get("/liabilities?status=unpaid&limit=500"),
                api.get("/liabilities?status=partial&limit=500"),
                api.get("/counterparties"),
            ]);
            const raw = accRes.data?.accounts || accRes.data?.items || (Array.isArray(accRes.data) ? accRes.data : []);
            setAccounts(raw);
            // Iter-99 — filter to ONLY real employees (exclude household/charity).
            setEmployees((empRes.data?.items || []).filter(
                (e) => e.status === "active" && e.category === "employee"
            ));
            // Iter-99 — exclude any liability whose underlying employee is
            // household/charity from the "pay" picker.
            const empById = Object.fromEntries(
                (empRes.data?.items || []).map((e) => [e.id, e])
            );
            const isPayableKind = (l) => {
                // Iter-125 — include salary_advance so the "سلفة موظف"
                // tab can show each employee's cumulative open advances.
                if (!["salary", "ad_account", "supplier", "salary_advance"].includes(l.kind)) return false;
                if (l.kind === "salary" && l.employee_salary_id) {
                    const emp = empById[l.employee_salary_id];
                    return emp ? emp.category === "employee" : true;
                }
                return true;
            };
            const openL = [
                ...(unpaidRes.data?.items || []),
                ...(partialRes.data?.items || []),
            ].filter(isPayableKind);
            setOpenLiabilities(openL);
            setCounterparties(cpsRes.data?.items || []);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر تحميل البيانات");
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, []);

    return (
        <div dir="rtl" data-testid="financial-input-hub-page">
            <div className="mb-5">
                <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900">مركز الإدخال المالي</h1>
                <p className="text-sm text-slate-500 mt-1">
                    نقطة الإدخال الوحيدة لكل العمليات اليومية. كل ما تُدخله ينعكس فوراً على المركز المالي والأصول والالتزامات.
                </p>
            </div>

            {/* Tab bar */}
            <div className="mb-5 -mx-2 overflow-x-auto">
                <div className="px-2 flex gap-2 min-w-max">
                    {TABS.map((t) => {
                        const active = tab === t.id;
                        const Icon = t.Icon;
                        return (
                            <button
                                key={t.id}
                                onClick={() => setTab(t.id)}
                                data-testid={t.testid}
                                className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-bold transition ${
                                    active ? "bg-slate-900 text-white shadow-md" : "bg-white border border-slate-200 text-slate-700 hover:bg-slate-50"
                                }`}
                            >
                                <Icon size={16} weight={active ? "fill" : "duotone"} />
                                <span>{t.label}</span>
                            </button>
                        );
                    })}
                </div>
            </div>

            {loading ? (
                <div className="bg-white border border-slate-200 rounded-xl p-10 text-center text-slate-500">
                    جاري التحميل…
                </div>
            ) : (
                <>
                    {tab === "new-liab"   && <NewLiabilityForm counterparties={counterparties} onSaved={load} />}
                    {tab === "pay-liab"   && <PayLiabilityForm openLiabilities={openLiabilities} banks={banks} onSaved={load} />}
                    {tab === "daily-exp"  && <DailyExpenseForm banks={banks} onSaved={load} />}
                    {tab === "receivable" && <ReceivableForm onSaved={load} />}
                    {tab === "advance"    && <AdvanceForm employees={employees} banks={banks} openLiabilities={openLiabilities} onSaved={load} />}
                    {tab === "shipping"   && <ShippingPaymentForm banks={banks} onSaved={load} />}
                    {tab === "cod"        && <CodTransferForm accounts={accounts} banks={banks} onSaved={load} />}
                </>
            )}

            <div className="mt-4 text-center text-[11px] text-slate-400">
                <ListChecks size={14} className="inline-block mr-1" />
                كل العمليات تستخدم الـ APIs الموجودة — لا توجد طبقة جديدة أو تكرار في الحسابات.
            </div>
        </div>
    );
}
