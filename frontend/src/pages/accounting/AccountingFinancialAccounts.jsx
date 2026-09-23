import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
    advanceFinancialAccountsTransition,
    archiveFinancialAccount,
    createFinancialAccount,
    createOpeningBalanceDraft,
    getFinancialAccountDefinitions,
    getFinancialAccounts,
    getFinancialAccountsTransition,
    getOpeningBalanceDrafts,
    postOpeningBalanceDraft,
    reverseOpeningBalanceDraft,
    reviewOpeningBalanceDraft,
    updateFinancialAccount,
    uploadOpeningBalanceEvidence,
} from "../../services/accountingModule";
import { LoadingBlock } from "./AccountingShared";

const PERMISSIONS = {
    view: "accounting.financial_accounts.view",
    manage: "accounting.financial_accounts.manage",
    openingView: "accounting.opening_balances.view",
    drafts: "accounting.opening_balances.drafts.manage",
    review: "accounting.opening_balances.review",
    post: "accounting.opening_balances.post",
    reverse: "accounting.journals.reverse",
};

const ACCOUNT_TYPE_LABELS = {
    bank: "حساب بنكي",
    cash: "صندوق نقدي",
    ad_prepaid_wallet: "محفظة إعلانية مقدمة",
    ad_payable: "ذمة منصة إعلانية",
    overdraft: "سحب على المكشوف",
};

const CATEGORY_LABELS = {
    banks_cash: "البنوك والصندوق",
    providers: "مزودو الدفع والتمويل",
    couriers_cod: "الشحن والتحصيل",
    suppliers: "الموردون",
    payroll: "الرواتب والالتزامات",
    inventory: "المخزون",
    equity: "حقوق الملكية",
    customers: "العملاء",
    tax: "الضرائب",
    other: "أخرى",
};

const TRANSITION_LABELS = {
    legacy_active: "الكاتب القديم نشط",
    transition_blocked: "الكتابات متوقفة للانتقال",
    v2_active: "كاتب ميزان 2 نشط",
};

function token(prefix) {
    const suffix = globalThis.crypto?.randomUUID?.()
        || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `${prefix}-${suffix}`;
}

function errorText(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    return detail?.message || detail?.code || fallback;
}

function emptyLine(category = "banks_cash") {
    return {
        local_id: token("line"),
        category,
        entity_type: "financial_account",
        entity_id: "",
        sub_account: "",
        side: "debit",
        amount: "",
        currency: "SAR",
        fx_rate: "1",
        evidence_file_id: "",
    };
}

function formatAmount(value) {
    const amount = Number(value || 0);
    return Number.isFinite(amount)
        ? new Intl.NumberFormat("ar-SA", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount)
        : "0.00";
}

export default function AccountingFinancialAccounts({ accountingPermissions = [] }) {
    const permissionSet = useMemo(() => new Set(accountingPermissions || []), [accountingPermissions]);
    const can = (permission) => permissionSet.has(permission);
    const [definitions, setDefinitions] = useState(null);
    const [accounts, setAccounts] = useState([]);
    const [drafts, setDrafts] = useState([]);
    const [transition, setTransition] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");
    const [accountForm, setAccountForm] = useState({
        name: "", account_type: "bank", currency: "SAR", external_ref: "",
    });
    const [editing, setEditing] = useState(null);
    const [evidenceBySection, setEvidenceBySection] = useState({});
    const [cutoverAt, setCutoverAt] = useState("");
    const [lines, setLines] = useState([emptyLine(), emptyLine("equity")]);
    const [selectedDraftId, setSelectedDraftId] = useState("");
    const [actionNote, setActionNote] = useState("");
    const [reversalAt, setReversalAt] = useState("");
    const [activationRef, setActivationRef] = useState("");

    const selectedDraft = drafts.find((draft) => draft.id === selectedDraftId) || null;

    async function refresh() {
        setLoading(true);
        try {
            const [nextDefinitions, nextAccounts, nextTransition] = await Promise.all([
                getFinancialAccountDefinitions(),
                getFinancialAccounts(),
                getFinancialAccountsTransition(),
            ]);
            setDefinitions(nextDefinitions);
            setAccounts(nextAccounts?.items || []);
            setTransition(nextTransition);
            if (can(PERMISSIONS.openingView)) {
                const nextDrafts = await getOpeningBalanceDrafts();
                setDrafts(nextDrafts?.items || []);
                setSelectedDraftId((current) => (
                    (nextDrafts?.items || []).some((item) => item.id === current)
                        ? current
                        : (nextDrafts?.items?.[0]?.id || "")
                ));
            } else {
                setDrafts([]);
                setSelectedDraftId("");
            }
        } catch (error) {
            toast.error(errorText(error, "تعذر تحميل الصناديق والحسابات المالية"));
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

    async function addAccount(event) {
        event.preventDefault();
        setBusy("account-create");
        try {
            await createFinancialAccount({
                ...accountForm,
                external_ref: accountForm.external_ref.trim() || null,
                idempotency_key: token("financial-account"),
            });
            setAccountForm({ name: "", account_type: "bank", currency: "SAR", external_ref: "" });
            toast.success("تمت إضافة الحساب المالي");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر إضافة الحساب"));
        } finally {
            setBusy("");
        }
    }

    async function saveAccount(account) {
        if (!editing || editing.id !== account.id) return;
        setBusy(`account-${account.id}`);
        try {
            await updateFinancialAccount(account.id, {
                version: account.version,
                name: editing.name.trim(),
                external_ref: editing.external_ref.trim() || null,
            });
            setEditing(null);
            toast.success("تم تحديث الحساب");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر تحديث الحساب"));
        } finally {
            setBusy("");
        }
    }

    async function archive(account) {
        if (!globalThis.confirm?.(`أرشفة الحساب «${account.name}»؟`)) return;
        setBusy(`account-${account.id}`);
        try {
            await archiveFinancialAccount(account.id, {
                version: account.version,
                reason: "أرشفة الحساب من صفحة الصناديق والحسابات المالية",
            });
            setEditing(null);
            toast.success("تمت أرشفة الحساب دون حذف سجله");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر أرشفة الحساب"));
        } finally {
            setBusy("");
        }
    }

    async function uploadEvidence(sectionId, file) {
        if (!file) return;
        setBusy(`evidence-${sectionId}`);
        try {
            const saved = await uploadOpeningBalanceEvidence({ sectionId, file });
            setEvidenceBySection((current) => ({ ...current, [sectionId]: saved }));
            toast.success("حُفظ أصل الدليل وبصمته بصورة غير قابلة للاستبدال");
        } catch (error) {
            toast.error(errorText(error, "تعذر حفظ الدليل"));
        } finally {
            setBusy("");
        }
    }

    function patchLine(localId, patch) {
        setLines((current) => current.map((line) => (
            line.local_id === localId ? { ...line, ...patch } : line
        )));
    }

    async function saveDraft(event) {
        event.preventDefault();
        const normalized = lines.map(({ local_id, ...line }) => ({
            ...line,
            sub_account: line.sub_account.trim() || null,
            amount: String(line.amount),
            fx_rate: String(line.fx_rate || "1"),
            sar_amount: (Number(line.amount || 0) * Number(line.fx_rate || 1)).toFixed(2),
        }));
        setBusy("draft-create");
        try {
            const created = await createOpeningBalanceDraft({
                idempotency_key: token("opening-draft"),
                cutover_at: new Date(cutoverAt).toISOString(),
                lines: normalized,
                ...(drafts.find((item) => item.status === "draft" && item.active_slot === "opening")
                    ? { replaces_draft_id: drafts.find((item) => item.status === "draft" && item.active_slot === "opening").id }
                    : {}),
            });
            toast.success("حُفظت مسودة الافتتاحية دون إنشاء قيد");
            await refresh();
            setSelectedDraftId(created.id);
        } catch (error) {
            toast.error(errorText(error, "تعذر حفظ مسودة الافتتاحية"));
        } finally {
            setBusy("");
        }
    }

    async function runDraftAction(action) {
        if (!selectedDraft || actionNote.trim().length < 3) return;
        const common = {
            version: selectedDraft.version,
            idempotency_key: token(`opening-${action}`),
            note: actionNote.trim(),
        };
        setBusy(`draft-${action}`);
        try {
            if (action === "review") await reviewOpeningBalanceDraft(selectedDraft.id, common);
            if (action === "post") await postOpeningBalanceDraft(selectedDraft.id, common);
            if (action === "reverse") {
                await reverseOpeningBalanceDraft(selectedDraft.id, {
                    ...common,
                    effective_at: new Date(reversalAt).toISOString(),
                });
            }
            toast.success(action === "review" ? "تمت مراجعة المسودة وقفل لقطة الأدلة" : action === "post" ? "تم ترحيل الافتتاحية عبر دفتر ميزان 2" : "أُضيف قيد العكس دون تعديل القيد الأصلي");
            setActionNote("");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر تنفيذ الإجراء"));
        } finally {
            setBusy("");
        }
    }

    async function advanceTransition(target) {
        setBusy(`transition-${target}`);
        try {
            await advanceFinancialAccountsTransition({
                target,
                expected_revision: transition.state_revision,
                activation_ref: target === "v2_active" ? activationRef.trim() : "",
            });
            toast.success(target === "transition_blocked" ? "توقفت الكتابات القديمة والجديدة أثناء الانتقال" : "تم تفعيل كاتب ميزان 2 فقط");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر تحديث حالة الانتقال"));
        } finally {
            setBusy("");
        }
    }

    if (loading && !definitions) return <LoadingBlock label="جاري تحميل الصناديق والحسابات المالية…" />;

    return (
        <div className="space-y-5" dir="rtl" data-testid="financial-accounts-page">
            <section className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
                <h2 className="text-xl font-black text-emerald-950">الصناديق والحسابات المالية</h2>
                <p className="mt-2 text-sm font-semibold leading-6 text-emerald-900">
                    عرّف حسابات البنك والصندوق أولًا، ثم جهّز افتتاحية موحدة بأدلة محفوظة. لا ينشأ أي قيد قبل المراجعة والترحيل المنفصلين.
                </p>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="financial-account-list">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h3 className="font-black text-slate-950">دليل الحسابات المالية</h3>
                        <p className="mt-1 text-xs font-semibold text-slate-500">الحساب المؤرشف يبقى في السجل ولا يُحذف.</p>
                    </div>
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-black text-slate-700">{accounts.length} حساب</span>
                </div>

                {can(PERMISSIONS.manage) && (
                    <form onSubmit={addAccount} className="mt-4 grid gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 md:grid-cols-5" data-testid="financial-account-create-form">
                        <input required minLength={2} value={accountForm.name} onChange={(event) => setAccountForm({ ...accountForm, name: event.target.value })} className="min-h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm" placeholder="اسم الحساب" aria-label="اسم الحساب المالي" />
                        <select value={accountForm.account_type} onChange={(event) => setAccountForm({ ...accountForm, account_type: event.target.value })} className="min-h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm" aria-label="نوع الحساب المالي">
                            {(definitions?.account_types || []).map((type) => <option key={type} value={type}>{ACCOUNT_TYPE_LABELS[type] || type}</option>)}
                        </select>
                        <input required maxLength={3} value={accountForm.currency} onChange={(event) => setAccountForm({ ...accountForm, currency: event.target.value.toUpperCase() })} className="min-h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm" placeholder="SAR" aria-label="عملة الحساب" />
                        <input value={accountForm.external_ref} onChange={(event) => setAccountForm({ ...accountForm, external_ref: event.target.value })} className="min-h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm" placeholder="مرجع البنك (اختياري)" aria-label="مرجع الحساب الخارجي" />
                        <button disabled={busy === "account-create"} className="min-h-10 rounded-lg bg-emerald-800 px-4 text-sm font-black text-white disabled:opacity-40">إضافة الحساب</button>
                    </form>
                )}

                <div className="mt-4 overflow-x-auto">
                    <table className="w-full min-w-[720px] text-right text-sm">
                        <thead className="bg-slate-50 text-xs text-slate-500"><tr><th className="p-3">الاسم</th><th className="p-3">النوع</th><th className="p-3">العملة</th><th className="p-3">المرجع</th><th className="p-3">الحالة</th><th className="p-3">الإجراء</th></tr></thead>
                        <tbody>
                            {accounts.map((account) => {
                                const isEditing = editing?.id === account.id;
                                return (
                                    <tr key={account.id} className="border-t border-slate-100">
                                        <td className="p-3">{isEditing ? <input aria-label={`تعديل اسم ${account.name}`} value={editing.name} onChange={(event) => setEditing({ ...editing, name: event.target.value })} className="rounded border px-2 py-1" /> : <span className="font-bold">{account.name}</span>}</td>
                                        <td className="p-3">{ACCOUNT_TYPE_LABELS[account.account_type] || account.account_type}</td>
                                        <td className="p-3 font-mono">{account.currency}</td>
                                        <td className="p-3">{isEditing ? <input aria-label={`تعديل مرجع ${account.name}`} value={editing.external_ref} onChange={(event) => setEditing({ ...editing, external_ref: event.target.value })} className="rounded border px-2 py-1" /> : (account.external_ref || "—")}</td>
                                        <td className="p-3"><span className={`rounded-full px-2 py-1 text-xs font-bold ${account.status === "active" ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"}`}>{account.status === "active" ? "نشط" : "مؤرشف"}</span></td>
                                        <td className="p-3">
                                            {can(PERMISSIONS.manage) && account.status === "active" && (
                                                <div className="flex gap-2">
                                                    {isEditing ? <button type="button" onClick={() => saveAccount(account)} className="font-bold text-emerald-800">حفظ</button> : <button type="button" onClick={() => setEditing({ id: account.id, name: account.name, external_ref: account.external_ref || "" })} className="font-bold text-slate-700">تعديل</button>}
                                                    <button type="button" onClick={() => archive(account)} className="font-bold text-rose-700">أرشفة</button>
                                                </div>
                                            )}
                                        </td>
                                    </tr>
                                );
                            })}
                            {!accounts.length && <tr><td colSpan={6} className="p-6 text-center text-sm font-semibold text-slate-500">لم تُضف حسابات مالية بعد.</td></tr>}
                        </tbody>
                    </table>
                </div>
            </section>

            {can(PERMISSIONS.openingView) && (
                <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5" data-testid="unified-opening-balances">
                    <div>
                        <h3 className="font-black text-slate-950">الأرصدة الافتتاحية الموحدة</h3>
                        <p className="mt-1 text-xs font-semibold text-slate-500">المسودة والمراجعة والترحيل صلاحيات منفصلة، وتُقفل الأدلة عند المراجعة.</p>
                    </div>

                    {can(PERMISSIONS.drafts) && (
                        <>
                            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" data-testid="opening-evidence-grid">
                                {(definitions?.evidence_sections || []).map((section) => (
                                    <label key={section.id} className="rounded-xl border border-slate-200 p-3 text-xs font-bold text-slate-700">
                                        <span>{section.label}</span>
                                        <input type="file" className="mt-2 block w-full text-xs" aria-label={`دليل ${section.label}`} onChange={(event) => uploadEvidence(section.id, event.target.files?.[0])} disabled={busy === `evidence-${section.id}`} />
                                        {evidenceBySection[section.id] && <span className="mt-2 block text-emerald-700">محفوظ · {evidenceBySection[section.id].size} بايت · SHA256 مثبت</span>}
                                    </label>
                                ))}
                            </div>

                            <form onSubmit={saveDraft} className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4" data-testid="opening-draft-form">
                                <label className="block text-xs font-bold text-slate-700">تاريخ القطع<input required type="datetime-local" value={cutoverAt} onChange={(event) => setCutoverAt(event.target.value)} className="mt-1 min-h-10 w-full max-w-sm rounded-lg border border-slate-200 bg-white px-3" /></label>
                                <div className="space-y-2">
                                    {lines.map((line, index) => (
                                        <div key={line.local_id} className="grid gap-2 rounded-lg border border-slate-200 bg-white p-3 md:grid-cols-4 xl:grid-cols-9">
                                            <select aria-label={`فئة السطر ${index + 1}`} value={line.category} onChange={(event) => patchLine(line.local_id, { category: event.target.value })} className="rounded border px-2 py-2 text-xs">{(definitions?.opening_categories || []).map((category) => <option key={category} value={category}>{CATEGORY_LABELS[category] || category}</option>)}</select>
                                            <input required aria-label={`نوع كيان السطر ${index + 1}`} value={line.entity_type} onChange={(event) => patchLine(line.local_id, { entity_type: event.target.value })} className="rounded border px-2 py-2 text-xs" placeholder="نوع الكيان" />
                                            <input required aria-label={`معرف كيان السطر ${index + 1}`} value={line.entity_id} onChange={(event) => patchLine(line.local_id, { entity_id: event.target.value })} className="rounded border px-2 py-2 text-xs" placeholder="معرف الحساب/الكيان" />
                                            <input aria-label={`الحساب الفرعي ${index + 1}`} value={line.sub_account} onChange={(event) => patchLine(line.local_id, { sub_account: event.target.value })} className="rounded border px-2 py-2 text-xs" placeholder="حساب فرعي" />
                                            <select aria-label={`جانب السطر ${index + 1}`} value={line.side} onChange={(event) => patchLine(line.local_id, { side: event.target.value })} className="rounded border px-2 py-2 text-xs"><option value="debit">مدين</option><option value="credit">دائن</option></select>
                                            <input required type="number" min="0.01" step="0.01" aria-label={`مبلغ السطر ${index + 1}`} value={line.amount} onChange={(event) => patchLine(line.local_id, { amount: event.target.value })} className="rounded border px-2 py-2 text-xs" placeholder="المبلغ" />
                                            <input required aria-label={`عملة السطر ${index + 1}`} value={line.currency} onChange={(event) => patchLine(line.local_id, { currency: event.target.value.toUpperCase() })} className="rounded border px-2 py-2 text-xs" placeholder="SAR" />
                                            <input required type="number" min="0.000001" step="0.000001" aria-label={`سعر صرف السطر ${index + 1}`} value={line.fx_rate} onChange={(event) => patchLine(line.local_id, { fx_rate: event.target.value })} className="rounded border px-2 py-2 text-xs" placeholder="سعر الصرف" />
                                            <select required aria-label={`دليل السطر ${index + 1}`} value={line.evidence_file_id} onChange={(event) => patchLine(line.local_id, { evidence_file_id: event.target.value })} className="rounded border px-2 py-2 text-xs"><option value="">الدليل</option>{Object.values(evidenceBySection).map((evidence) => <option key={evidence.id} value={evidence.id}>{evidence.section_id} · {evidence.filename}</option>)}</select>
                                        </div>
                                    ))}
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <button type="button" onClick={() => setLines((current) => [...current, emptyLine()])} className="rounded-lg border border-slate-300 px-4 py-2 text-xs font-black">إضافة سطر</button>
                                    {lines.length > 2 && <button type="button" onClick={() => setLines((current) => current.slice(0, -1))} className="rounded-lg border border-rose-200 px-4 py-2 text-xs font-black text-rose-700">حذف آخر سطر</button>}
                                    <button disabled={busy === "draft-create"} className="rounded-lg bg-emerald-800 px-5 py-2 text-xs font-black text-white disabled:opacity-40">حفظ مسودة فقط</button>
                                </div>
                            </form>
                        </>
                    )}

                    <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
                        <div className="space-y-2" data-testid="opening-drafts-list">
                            {drafts.map((draft) => <button type="button" key={draft.id} onClick={() => setSelectedDraftId(draft.id)} className={`w-full rounded-xl border p-3 text-right text-xs ${selectedDraftId === draft.id ? "border-emerald-500 bg-emerald-50" : "border-slate-200"}`}><span className="block font-black">{draft.status}</span><span className="mt-1 block text-slate-500">نسخة {draft.version} · {formatAmount(draft.debit_total)} ر.س</span></button>)}
                            {!drafts.length && <div className="rounded-xl border border-dashed p-4 text-center text-xs font-semibold text-slate-500">لا توجد مسودات افتتاحية.</div>}
                        </div>
                        {selectedDraft && (
                            <div className="rounded-xl border border-slate-200 p-4" data-testid="opening-draft-actions">
                                <div className="grid gap-2 sm:grid-cols-3"><div><span className="text-xs text-slate-500">الحالة</span><strong className="block">{selectedDraft.status}</strong></div><div><span className="text-xs text-slate-500">المدين</span><strong className="block">{formatAmount(selectedDraft.debit_total)}</strong></div><div><span className="text-xs text-slate-500">الدائن</span><strong className="block">{formatAmount(selectedDraft.credit_total)}</strong></div></div>
                                <textarea value={actionNote} onChange={(event) => setActionNote(event.target.value)} className="mt-3 min-h-20 w-full rounded-lg border border-slate-200 p-3 text-sm" placeholder="ملاحظة الإجراء (مطلوبة)" aria-label="ملاحظة إجراء الافتتاحية" />
                                {selectedDraft.status === "reversed" && <p className="mt-2 text-xs font-bold text-slate-600">القيد الأصلي محفوظ، وقيد العكس مستقل: <span dir="ltr">{selectedDraft.reversal_txn_group_id}</span></p>}
                                <div className="mt-3 flex flex-wrap gap-2">
                                    {selectedDraft.status === "draft" && can(PERMISSIONS.review) && <button type="button" onClick={() => runDraftAction("review")} disabled={actionNote.trim().length < 3 || busy === "draft-review"} className="rounded-lg bg-amber-700 px-4 py-2 text-xs font-black text-white disabled:opacity-40">مراجعة وقفل الأدلة</button>}
                                    {selectedDraft.status === "reviewed" && can(PERMISSIONS.post) && <button type="button" onClick={() => runDraftAction("post")} disabled={actionNote.trim().length < 3 || busy === "draft-post"} className="rounded-lg bg-emerald-800 px-4 py-2 text-xs font-black text-white disabled:opacity-40">ترحيل عبر ميزان 2</button>}
                                    {selectedDraft.status === "posted" && can(PERMISSIONS.reverse) && <><input type="datetime-local" value={reversalAt} onChange={(event) => setReversalAt(event.target.value)} className="rounded-lg border border-slate-200 px-3 text-xs" aria-label="تاريخ قيد العكس" /><button type="button" onClick={() => runDraftAction("reverse")} disabled={!reversalAt || actionNote.trim().length < 3 || busy === "draft-reverse"} className="rounded-lg bg-rose-800 px-4 py-2 text-xs font-black text-white disabled:opacity-40">إنشاء قيد عكس إلحاقي</button></>}
                                </div>
                            </div>
                        )}
                    </div>
                </section>
            )}

            <section className="rounded-2xl border border-violet-200 bg-violet-50 p-5" data-testid="writer-transition-state">
                <h3 className="font-black text-violet-950">حاجز انتقال الكاتب المحاسبي</h3>
                <p className="mt-2 text-sm font-bold text-violet-900">{TRANSITION_LABELS[transition?.state] || "حالة غير معروفة"} · مراجعة {transition?.state_revision ?? "—"}</p>
                <p className="mt-1 text-xs font-semibold leading-6 text-violet-800">في transition_blocked تُمنع كتابات القديم وميزان 2 معًا، ولا يوجد رجوع تلقائي إلى الكاتب القديم عند فشل ميزان 2.</p>
                {can(PERMISSIONS.manage) && transition?.state === "legacy_active" && <button type="button" onClick={() => advanceTransition("transition_blocked")} className="mt-3 rounded-lg bg-violet-800 px-4 py-2 text-xs font-black text-white">إيقاف الكتابات للانتقال</button>}
                {can(PERMISSIONS.manage) && transition?.state === "transition_blocked" && <div className="mt-3 flex flex-wrap gap-2"><input value={activationRef} onChange={(event) => setActivationRef(event.target.value)} className="min-h-10 flex-1 rounded-lg border border-violet-200 bg-white px-3 text-xs" placeholder="مرجع قرار التفعيل" aria-label="مرجع تفعيل ميزان 2" /><button type="button" onClick={() => advanceTransition("v2_active")} disabled={activationRef.trim().length < 3 || busy === "transition-v2_active"} className="rounded-lg bg-violet-800 px-4 py-2 text-xs font-black text-white disabled:opacity-40">تفعيل ميزان 2 فقط</button></div>}
            </section>
        </div>
    );
}
