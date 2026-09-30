import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
    advanceFinancialAccountsTransition,
    archiveFinancialAccount,
    createFinancialAccount,
    getFinancialAccountDefinitions,
    getFinancialAccounts,
    getFinancialAccountsTransition,
    getOpeningBalanceDrafts,
    updateFinancialAccount,
} from "../../services/accountingModule";
import { LoadingBlock } from "./AccountingShared";

const PERMISSIONS = {
    view: "accounting.financial_accounts.view",
    manage: "accounting.financial_accounts.manage",
    openingView: "accounting.opening_balances.view",
    transition: "accounting.ledger_transition.manage",
};

const ACCOUNT_TYPE_LABELS = {
    bank: "حساب بنكي",
    cash: "صندوق نقدي",
    ad_prepaid_wallet: "محفظة إعلانية مقدمة",
    ad_payable: "ذمة منصة إعلانية",
    overdraft: "سحب على المكشوف",
};

const TRANSITION_LABELS = {
    legacy_active: "الكاتب القديم نشط",
    transition_blocked: "الكتابات المالية متوقفة للانتقال",
    v2_active: "كاتب ميزان 2 نشط وحده",
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

function StoredOpeningReviewDetails({ draft }) {
    const entries = draft?.preview_entries || [];
    const zeroLines = (draft?.lines || []).filter((line) => line.meaning === "zero");
    if (!entries.length && !zeroLines.length) return null;

    return <div className="space-y-4 rounded-xl border border-slate-200 p-4" data-testid="opening-stored-review-details">
        {entries.length > 0 && <div className="overflow-x-auto" data-testid="opening-fx-review-table">
            <h4 className="mb-2 text-sm font-black">تفاصيل الحساب والتحويل المحفوظة</h4>
            <table className="w-full min-w-[900px] text-right text-xs"><thead className="bg-slate-50"><tr><th className="p-2">الحساب</th><th className="p-2">هوية الحساب/الطرف</th><th className="p-2">سعر التحويل</th><th className="p-2">وقت التحويل</th><th className="p-2">المصدر</th><th className="p-2">الدليل</th></tr></thead><tbody>{entries.map((entry) => <tr key={`fx-${entry.line_no}-${entry.entity_type}-${entry.entity_id}`} className="border-t"><td className="p-2">{entry.label}</td><td className="p-2" dir="ltr">{entry.entity_type}/{entry.entity_id}/{entry.sub_account}</td><td className="p-2">{entry.fx_snapshot?.rate_to_sar || "—"}</td><td className="p-2" dir="ltr">{entry.fx_snapshot?.fx_at || "—"}</td><td className="p-2">{entry.fx_snapshot?.source || "—"}</td><td className="p-2 font-mono text-[10px]" dir="ltr">{entry.fx_snapshot?.evidence_file_id || entry.evidence_file_id || "—"}</td></tr>)}</tbody></table>
        </div>}
        {zeroLines.length > 0 && <div className="overflow-x-auto" data-testid="opening-zero-balances">
            <h4 className="mb-2 text-sm font-black">الأرصدة الصفرية الموثقة</h4>
            <table className="w-full min-w-[760px] text-right text-xs"><thead className="bg-slate-50"><tr><th className="p-2">الحساب</th><th className="p-2">هوية الحساب/الطرف</th><th className="p-2">العملة</th><th className="p-2">سعر التحويل</th><th className="p-2">وقت التحويل</th><th className="p-2">الدليل</th></tr></thead><tbody>{zeroLines.map((line) => <tr key={`zero-${line.line_no}-${line.entity_type}-${line.entity_id}`} className="border-t"><td className="p-2">{line.label}</td><td className="p-2" dir="ltr">{line.entity_type}/{line.entity_id}/{line.sub_account}</td><td className="p-2">{line.original_currency}</td><td className="p-2">{line.fx_snapshot?.rate_to_sar || "—"}</td><td className="p-2" dir="ltr">{line.fx_snapshot?.fx_at || "—"}</td><td className="p-2 font-mono text-[10px]" dir="ltr">{line.evidence_file_id || "—"}</td></tr>)}</tbody></table>
        </div>}
    </div>;
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
    const [selectedDraftId, setSelectedDraftId] = useState("");
    const accountCreateRequest = useRef(null);

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
        const facts = {
            ...accountForm,
            name: accountForm.name.trim(),
            currency: accountForm.currency.toUpperCase(),
            external_ref: accountForm.external_ref.trim() || null,
        };
        const fingerprint = JSON.stringify(facts);
        if (accountCreateRequest.current?.fingerprint !== fingerprint) {
            accountCreateRequest.current = {
                fingerprint,
                idempotencyKey: token("financial-account"),
            };
        }
        setBusy("account-create");
        try {
            await createFinancialAccount({
                ...facts,
                idempotency_key: accountCreateRequest.current.idempotencyKey,
            });
            accountCreateRequest.current = null;
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

    async function advanceTransition(target) {
        setBusy(`transition-${target}`);
        try {
            await advanceFinancialAccountsTransition({
                target,
                expected_revision: transition.state_revision,
                activation_ref: "",
            });
            toast.success(target === "transition_blocked"
                ? "توقفت الكتابات المالية القديمة والجديدة أثناء الانتقال"
                : "تم تفعيل كاتب ميزان 2 فقط");
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
                <p className="mt-2 text-sm font-semibold leading-6 text-emerald-900">عرّف الحسابات المالية هنا، ثم أكمل الأرصدة والأدلة عبر مسار تهيئة ميزان 2.</p>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="financial-account-list">
                <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-black text-slate-950">دليل الحسابات المالية</h3><p className="mt-1 text-xs font-semibold text-slate-500">الحساب المؤرشف يبقى في السجل ولا يُحذف.</p></div><span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-black text-slate-700">{accounts.length} حساب</span></div>
                {can(PERMISSIONS.manage) && <form onSubmit={addAccount} className="mt-4 grid gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 md:grid-cols-5" data-testid="financial-account-create-form"><input required minLength={2} value={accountForm.name} onChange={(event) => setAccountForm({ ...accountForm, name: event.target.value })} className="min-h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm" placeholder="اسم الحساب" aria-label="اسم الحساب المالي" /><select value={accountForm.account_type} onChange={(event) => setAccountForm({ ...accountForm, account_type: event.target.value })} className="min-h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm" aria-label="نوع الحساب المالي">{(definitions?.account_types || []).map((type) => <option key={type} value={type}>{ACCOUNT_TYPE_LABELS[type] || type}</option>)}</select><input required maxLength={3} value={accountForm.currency} onChange={(event) => setAccountForm({ ...accountForm, currency: event.target.value.toUpperCase() })} className="min-h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm" placeholder="SAR" aria-label="عملة الحساب" /><input value={accountForm.external_ref} onChange={(event) => setAccountForm({ ...accountForm, external_ref: event.target.value })} className="min-h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm" placeholder="مرجع البنك (اختياري)" aria-label="مرجع الحساب الخارجي" /><button disabled={busy === "account-create"} className="min-h-10 rounded-lg bg-emerald-800 px-4 text-sm font-black text-white disabled:opacity-40">إضافة الحساب</button></form>}
                <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[720px] text-right text-sm"><thead className="bg-slate-50 text-xs text-slate-500"><tr><th className="p-3">الاسم</th><th className="p-3">النوع</th><th className="p-3">العملة</th><th className="p-3">المرجع</th><th className="p-3">الحالة</th><th className="p-3">الإجراء</th></tr></thead><tbody>{accounts.map((account) => { const isEditing = editing?.id === account.id; return <tr key={account.id} className="border-t border-slate-100"><td className="p-3">{isEditing ? <input aria-label={`تعديل اسم ${account.name}`} value={editing.name} onChange={(event) => setEditing({ ...editing, name: event.target.value })} className="rounded border px-2 py-1" /> : <span className="font-bold">{account.name}</span>}</td><td className="p-3">{ACCOUNT_TYPE_LABELS[account.account_type] || account.account_type}</td><td className="p-3 font-mono">{account.currency}</td><td className="p-3">{isEditing ? <input aria-label={`تعديل مرجع ${account.name}`} value={editing.external_ref} onChange={(event) => setEditing({ ...editing, external_ref: event.target.value })} className="rounded border px-2 py-1" /> : (account.external_ref || "—")}</td><td className="p-3"><span className={`rounded-full px-2 py-1 text-xs font-bold ${account.status === "active" ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"}`}>{account.status === "active" ? "نشط" : "مؤرشف"}</span></td><td className="p-3">{can(PERMISSIONS.manage) && account.status === "active" && <div className="flex gap-2">{isEditing ? <button type="button" onClick={() => saveAccount(account)} className="font-bold text-emerald-800">حفظ</button> : <button type="button" onClick={() => setEditing({ id: account.id, name: account.name, external_ref: account.external_ref || "" })} className="font-bold text-slate-700">تعديل</button>}<button type="button" onClick={() => archive(account)} className="font-bold text-rose-700">أرشفة</button></div>}</td></tr>; })}{!accounts.length && <tr><td colSpan={6} className="p-6 text-center text-sm font-semibold text-slate-500">لم تُضف حسابات مالية بعد.</td></tr>}</tbody></table></div>
            </section>

            {can(PERMISSIONS.openingView) && <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5" data-testid="unified-opening-balances">
                <h3 className="font-black text-slate-950">الأرصدة الافتتاحية</h3>
                <p className="text-sm text-slate-700">إعداد ومراجعة الأرصدة يتم عبر مسار التهيئة المكون من 16 مرحلة. الترحيل والتفعيل مقفلان حتى استكمال التحقق المستقل.</p>
                <a className="font-bold text-emerald-800 underline" href="/integrations-v2?workspace=financial&amp;page=opening-balances" data-testid="opening-onboarding-link">فتح تهيئة ميزان 2</a>
                {drafts.length > 0 && <div data-testid="opening-historical-evidence">
                    <label className="text-sm">دليل افتتاحي محفوظ — للقراءة فقط
                        <select value={selectedDraftId} onChange={(event) => setSelectedDraftId(event.target.value)} className="mx-2 rounded border p-2">{drafts.map((draft) => <option key={draft.id} value={draft.id}>{draft.id} · {draft.status}</option>)}</select>
                    </label>
                    {selectedDraft && <StoredOpeningReviewDetails draft={selectedDraft} />}
                </div>}
            </section>}

            <section className="rounded-2xl border border-violet-200 bg-violet-50 p-5" data-testid="writer-transition-state"><h3 className="font-black text-violet-950">حاجز انتقال الكاتب المحاسبي</h3><p className="mt-2 text-sm font-bold text-violet-900">{TRANSITION_LABELS[transition?.state] || "حالة غير معروفة"} · مراجعة {transition?.state_revision ?? "—"}</p><p className="mt-1 text-xs font-semibold leading-6 text-violet-800">في transition_blocked تُمنع كتابات القديم وميزان 2 معًا. فشل ميزان 2 لا يعيد تشغيل الكاتب القديم.</p>{can(PERMISSIONS.transition) && transition?.state === "legacy_active" && <button type="button" onClick={() => advanceTransition("transition_blocked")} className="mt-3 rounded-lg bg-violet-800 px-4 py-2 text-xs font-black text-white">إيقاف الكتابات للانتقال</button>}{transition?.state === "transition_blocked" && <p className="mt-3 text-sm font-bold">التفعيل مقفل إلى حين اجتياز التهيئة والتحقق المستقل.</p>}</section>
        </div>
    );
}
