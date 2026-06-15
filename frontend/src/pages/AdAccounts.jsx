/**
 * Ad-Account Balance & Debt Engine UI — Iter-106
 *
 * Lists every counterparty(kind=ad_account) with:
 *  • current balance         (asset)
 *  • current open debt       (liability — appears in Financial Position)
 *  • lifetime spend
 *  • debt mode (auto/manual) toggle
 *  • Topup / Spend dialogs
 *  • Per-account ledger viewer
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
    ChartLineUp, Plus, Minus, ArrowsClockwise, Clock, ListBullets,
    CheckCircle, Warning,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";
import { todaySA, monthStartSA } from "../lib/dates";


const fmt = (v) =>
    Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const todayIso = () => todaySA();

const PROVIDER_LABEL = {
    snapchat: "Snapchat", tiktok: "TikTok", meta: "Meta / Facebook",
    google: "Google Ads", twitter: "X (Twitter)", other: "أخرى",
};
const PROVIDER_OPTIONS = Object.entries(PROVIDER_LABEL);

const SYNC_SUPPORTED = new Set(["snapchat", "tiktok", "meta"]);

const LEDGER_LABEL = {
    topup:   { label: "تعبئة",            tone: "bg-emerald-50 text-emerald-800 border-emerald-200" },
    spend:   { label: "صرف يومي",         tone: "bg-violet-50 text-violet-800 border-violet-200" },
    debt:    { label: "إنشاء مديونية",    tone: "bg-rose-50 text-rose-800 border-rose-200" },
    manual:  { label: "تعديل يدوي",       tone: "bg-amber-50 text-amber-800 border-amber-200" },
    reverse: { label: "عكس حركة",         tone: "bg-slate-100 text-slate-800 border-slate-200" },
};

const inputCls =
    "w-full px-3 py-2.5 text-sm border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-violet-500";


// ── Create dialog (inline new ad account) ───────────────────────────
function CreateDialog({ open, onClose, onSaved }) {
    const [form, setForm] = useState({
        name: "", ad_provider: "snapchat", notes: "", external_account_id: "",
    });
    const [busy, setBusy] = useState(false);
    const [warning, setWarning] = useState(null);

    useEffect(() => {
        if (!open) return;
        setForm({ name: "", ad_provider: "snapchat", notes: "", external_account_id: "" });
        setWarning(null);
    }, [open]);

    if (!open) return null;

    const submit = async (force = false) => {
        if (!form.name.trim()) { toast.error("الاسم مطلوب"); return; }
        setBusy(true);
        try {
            await api.post("/ad-accounts", {
                ...form, name: form.name.trim(),
                external_account_id: form.external_account_id.trim() || null,
                force,
            });
            toast.success(`تمت إضافة "${form.name}"`);
            onSaved();
            onClose();
        } catch (e) {
            const d = e.response?.data?.detail;
            if (typeof d === "object" && d?.message === "similar_name_exists") {
                setWarning({ suggestion: d.suggestion });
                toast.warning(`اسم مشابه موجود: ${d.suggestion?.name}`);
            } else if (typeof d === "object" && d?.message === "duplicate") {
                toast.error(`الاسم موجود مسبقاً: ${d.existing?.name}`);
            } else {
                toast.error(formatApiErrorDetail(d) || "تعذّر الإضافة");
            }
        } finally { setBusy(false); }
    };

    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="adacc-create-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-md my-8">
                <form onSubmit={(e) => { e.preventDefault(); submit(false); }}>
                    <div className="flex items-center justify-between p-5 border-b border-slate-100">
                        <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                            <Plus size={22} weight="duotone" className="text-violet-700" />
                            إضافة حساب إعلاني
                        </h2>
                        <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                    </div>
                    <div className="p-5 space-y-3">
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">المنصة *</label>
                            <select value={form.ad_provider} onChange={(e) => { setForm({ ...form, ad_provider: e.target.value }); setWarning(null); }} className={inputCls} data-testid="adacc-create-provider">
                                {PROVIDER_OPTIONS.map(([v, l]) => (<option key={v} value={v}>{l}</option>))}
                            </select>
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">الاسم *</label>
                            <input value={form.name} onChange={(e) => { setForm({ ...form, name: e.target.value }); setWarning(null); }} placeholder="مثال: متجر أماسي سعودي / Self Service" className={inputCls} data-testid="adacc-create-name" />
                        </div>
                        {SYNC_SUPPORTED.has(form.ad_provider) && (
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">
                                    معرّف الحساب على المنصة (Ad Account ID)
                                    <span className="text-slate-400 font-normal mr-1">— مهم لربط المزامنة</span>
                                </label>
                                <input
                                    value={form.external_account_id}
                                    onChange={(e) => setForm({ ...form, external_account_id: e.target.value })}
                                    placeholder="acc_SA_001"
                                    className={`${inputCls} font-mono`}
                                    dir="ltr"
                                    data-testid="adacc-create-external-id"
                                />
                                <div className="text-[11px] text-slate-500 mt-1">
                                    💡 لو عندك أكثر من حساب على نفس المنصة، اربط كل اسم بمعرّفه على {PROVIDER_LABEL[form.ad_provider]} حتى تنفصل المديونيات تلقائياً.
                                </div>
                            </div>
                        )}
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">ملاحظات</label>
                            <input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className={inputCls} />
                        </div>
                        {warning && (
                            <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs space-y-2" data-testid="adacc-create-warning">
                                <div>⚠️ يوجد حساب مشابه باسم <b>{warning.suggestion?.name}</b>. لو حساباً منفصلاً اضغط "أنشئ منفصلاً".</div>
                                <button type="button" onClick={() => submit(true)} disabled={busy}
                                    className="px-3 py-1.5 rounded-lg bg-rose-700 text-white text-xs font-bold disabled:opacity-50" data-testid="adacc-create-force-btn">
                                    أنشئ منفصلاً رغم التشابه
                                </button>
                            </div>
                        )}
                    </div>
                    <div className="p-5 border-t border-slate-100 flex justify-end gap-2">
                        <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold">إلغاء</button>
                        <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-800 disabled:opacity-50" data-testid="adacc-create-submit">
                            {busy ? "جاري…" : "إضافة"}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}


// ── Sync dialog (pull spend from platform daily ads) ────────────────
function SyncDialog({ row, open, onClose, onSaved }) {
    const monthStart = () => monthStartSA();
    const [form, setForm] = useState({ from_date: monthStart(), to_date: todayIso() });
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return;
        setForm({ from_date: monthStart(), to_date: todayIso() });
    }, [open]);

    if (!open || !row) return null;
    const supported = SYNC_SUPPORTED.has(row.ad_provider);

    const submit = async (e) => {
        e?.preventDefault?.();
        setBusy(true);
        try {
            const { data } = await api.post(
                `/ad-accounts/${row.id}/sync-from-platform`,
                { from_date: form.from_date, to_date: form.to_date },
            );
            if (data.spend === 0) {
                toast.info("لا توجد بيانات صرف في الفترة المختارة");
            } else {
                let msg = `تمت المزامنة: صرف ${fmt(data.amount)} ر.س`;
                if (data.debt_created > 0) msg += ` + مديونية ${fmt(data.debt_created)}`;
                toast.success(msg);
            }
            onSaved();
            onClose();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل");
        } finally { setBusy(false); }
    };

    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="adacc-sync-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-md my-8">
                <form onSubmit={submit}>
                    <div className="flex items-center justify-between p-5 border-b border-slate-100">
                        <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                            <ArrowsClockwise size={22} weight="duotone" className="text-violet-700" />
                            مزامنة من {PROVIDER_LABEL[row.ad_provider]}
                        </h2>
                        <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                    </div>
                    <div className="p-5 space-y-3">
                        {!supported ? (
                            <div className="bg-amber-50 border border-amber-200 rounded p-3 text-xs text-amber-900">
                                ⚠️ المزامنة التلقائية تعمل حالياً مع Snapchat / TikTok / Meta فقط. لـ {PROVIDER_LABEL[row.ad_provider]} استخدم "تسجيل صرف" يدوياً.
                            </div>
                        ) : (
                            <div className="bg-violet-50 border border-violet-200 rounded p-3 text-xs text-violet-900">
                                💡 سيتم جمع كل الصرف اليومي من بيانات {PROVIDER_LABEL[row.ad_provider]} في الفترة المختارة وتسجيله صرفاً واحداً على هذا الحساب.
                            </div>
                        )}
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">من تاريخ</label>
                                <input type="date" value={form.from_date} onChange={(e) => setForm({ ...form, from_date: e.target.value })} className={inputCls} data-testid="adacc-sync-from" />
                            </div>
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">إلى تاريخ</label>
                                <input type="date" value={form.to_date} onChange={(e) => setForm({ ...form, to_date: e.target.value })} className={inputCls} data-testid="adacc-sync-to" />
                            </div>
                        </div>
                    </div>
                    <div className="p-5 border-t border-slate-100 flex justify-end gap-2">
                        <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold">إلغاء</button>
                        <button type="submit" disabled={busy || !supported} className="px-4 py-2 rounded-lg bg-violet-700 text-white text-sm font-bold hover:bg-violet-800 disabled:opacity-50" data-testid="adacc-sync-submit">
                            {busy ? "جاري…" : "مزامنة الآن"}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}


// ── Topup dialog ────────────────────────────────────────────────────
function TopupDialog({ row, banks, open, onClose, onSaved }) {
    const [form, setForm] = useState({
        amount: "", paid_from_account_id: "", transaction_date: todayIso(), notes: "",
    });
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return;
        setForm({ amount: "", paid_from_account_id: banks[0]?.id || "",
                  transaction_date: todayIso(), notes: "" });
    }, [open, banks]);

    if (!open || !row) return null;

    const submit = async (e) => {
        e?.preventDefault?.();
        const amt = Number(form.amount);
        if (!amt || amt <= 0) { toast.error("أدخل مبلغاً"); return; }
        if (!form.paid_from_account_id) { toast.error("اختر البنك"); return; }
        setBusy(true);
        try {
            const { data } = await api.post(`/ad-accounts/${row.id}/topup`, {
                ...form, amount: amt,
            });
            const msg = data.applied_to_debt > 0
                ? `سُدِّد ${fmt(data.applied_to_debt)} من المديونية + ${fmt(data.applied_to_balance)} للرصيد`
                : `أُضيف ${fmt(data.applied_to_balance)} للرصيد`;
            toast.success(msg);
            onSaved();
            onClose();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل");
        } finally { setBusy(false); }
    };

    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="adacc-topup-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-md my-8">
                <form onSubmit={submit}>
                    <div className="flex items-center justify-between p-5 border-b border-slate-100">
                        <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                            <Plus size={22} weight="duotone" className="text-emerald-700" />
                            تعبئة رصيد — {row.name}
                        </h2>
                        <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                    </div>
                    <div className="p-5 space-y-3">
                        <div className="text-xs text-slate-600 bg-slate-50 rounded p-3 space-y-1">
                            <div>الرصيد الحالي: <b className="num">{fmt(row.balance)} ر.س</b></div>
                            <div>المديونية الحالية: <b className="num text-rose-700">{fmt(row.open_debt)} ر.س</b></div>
                            {row.open_debt > 0 && (
                                <div className="text-amber-700 mt-1 text-[11px]">
                                    💡 سيُسدَّد من المديونية أولاً قبل إضافة الباقي للرصيد.
                                </div>
                            )}
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">يُخصم من البنك *</label>
                            <select value={form.paid_from_account_id} onChange={(e) => setForm({ ...form, paid_from_account_id: e.target.value })} className={inputCls} data-testid="adacc-topup-bank">
                                <option value="">— اختر —</option>
                                {banks.map((b) => (<option key={b.id} value={b.id}>{b.name} ({fmt(b.current_balance)})</option>))}
                            </select>
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">المبلغ *</label>
                            <input type="number" min="0" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className={`${inputCls} num`} data-testid="adacc-topup-amount" />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">التاريخ</label>
                            <input type="date" value={form.transaction_date} onChange={(e) => setForm({ ...form, transaction_date: e.target.value })} className={inputCls} />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">ملاحظات</label>
                            <input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className={inputCls} />
                        </div>
                    </div>
                    <div className="p-5 border-t border-slate-100 flex justify-end gap-2">
                        <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold">إلغاء</button>
                        <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-emerald-700 text-white text-sm font-bold hover:bg-emerald-800 disabled:opacity-50" data-testid="adacc-topup-submit">
                            {busy ? "جاري…" : "تأكيد التعبئة"}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}


// ── Spend dialog ────────────────────────────────────────────────────
function SpendDialog({ row, open, onClose, onSaved }) {
    const [form, setForm] = useState({ amount: "", spend_date: todayIso(), description: "", notes: "" });
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return;
        setForm({ amount: "", spend_date: todayIso(), description: "", notes: "" });
    }, [open]);

    if (!open || !row) return null;

    const submit = async (e) => {
        e?.preventDefault?.();
        const amt = Number(form.amount);
        if (!amt || amt <= 0) { toast.error("أدخل مبلغ الصرف"); return; }
        setBusy(true);
        try {
            const { data } = await api.post(`/ad-accounts/${row.id}/spend`, {
                ...form, amount: amt,
            });
            let msg = `خُصِم ${fmt(data.covered_by_balance)} من الرصيد`;
            if (data.debt_created > 0) {
                msg += ` + إنشاء مديونية ${fmt(data.debt_created)}`;
            } else if (data.uncovered > 0) {
                msg += ` (${fmt(data.uncovered)} غير مغطى — وضع يدوي)`;
            }
            toast.success(msg);
            onSaved();
            onClose();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل");
        } finally { setBusy(false); }
    };

    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="adacc-spend-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-md my-8">
                <form onSubmit={submit}>
                    <div className="flex items-center justify-between p-5 border-b border-slate-100">
                        <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                            <Minus size={22} weight="duotone" className="text-rose-700" />
                            تسجيل صرف — {row.name}
                        </h2>
                        <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                    </div>
                    <div className="p-5 space-y-3">
                        <div className="text-xs text-slate-600 bg-slate-50 rounded p-3 space-y-1">
                            <div>الرصيد الحالي: <b className="num text-emerald-700">{fmt(row.balance)}</b></div>
                            <div>الوضع: <b>{row.debt_mode === "auto" ? "تلقائي" : "يدوي"}</b></div>
                            <div className="text-[11px] text-slate-500 mt-1">
                                {row.debt_mode === "auto"
                                    ? "💡 إذا تجاوز الصرف الرصيد، سيُنشأ التزام تلقائياً بالفرق."
                                    : "⚠️ الوضع يدوي — الفرق لن يُسجَّل كدين تلقائياً."}
                            </div>
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">المبلغ *</label>
                            <input type="number" min="0" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className={`${inputCls} num`} data-testid="adacc-spend-amount" />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">تاريخ الصرف</label>
                            <input type="date" value={form.spend_date} onChange={(e) => setForm({ ...form, spend_date: e.target.value })} className={inputCls} />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">الوصف</label>
                            <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className={inputCls} placeholder="حملة X — يوم 5 يونيو" />
                        </div>
                    </div>
                    <div className="p-5 border-t border-slate-100 flex justify-end gap-2">
                        <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold">إلغاء</button>
                        <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-rose-700 text-white text-sm font-bold hover:bg-rose-800 disabled:opacity-50" data-testid="adacc-spend-submit">
                            {busy ? "جاري…" : "تسجيل الصرف"}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}


// ── Ledger viewer ────────────────────────────────────────────────────
function LedgerDialog({ row, open, onClose, onSaved }) {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(false);
    const [editingId, setEditingId] = useState(null);
    const [editForm, setEditForm] = useState({ amount: "", transaction_date: "" });
    const [saving, setSaving] = useState(false);

    const load = () => {
        if (!row) return;
        setLoading(true);
        api.get(`/ad-accounts/${row.id}/ledger`)
            .then((r) => setRows(r.data?.items || []))
            .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        if (!open || !row) return;
        load();
        setEditingId(null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, row]);

    const startEdit = (r) => {
        setEditingId(r.id);
        setEditForm({
            amount: String(r.amount ?? ""),
            transaction_date: r.date || todayIso(),
        });
    };

    const saveEdit = async () => {
        if (!editingId) return;
        const amt = Number(editForm.amount);
        if (!amt || amt <= 0) { toast.error("المبلغ يجب أن يكون أكبر من صفر"); return; }
        setSaving(true);
        try {
            const { data } = await api.put(
                `/ad-accounts/${row.id}/topup/${editingId}`,
                { amount: amt, transaction_date: editForm.transaction_date }
            );
            toast.success(`تم التعديل (${fmt(data.previous_amount)} → ${fmt(data.amount)} ر.س)`);
            setEditingId(null);
            load();
            onSaved?.();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل التعديل");
        } finally { setSaving(false); }
    };

    if (!open || !row) return null;
    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="adacc-ledger-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-3xl my-8">
                <div className="flex items-center justify-between p-5 border-b border-slate-100">
                    <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                        <ListBullets size={22} weight="duotone" className="text-violet-700" />
                        سجل حركات — {row.name}
                    </h2>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                </div>
                <div className="p-5">
                    {loading ? <div className="text-center text-slate-500 text-sm py-8">جاري التحميل…</div> :
                     rows.length === 0 ? <div className="text-center text-slate-500 text-sm py-8">لا توجد حركات بعد</div> : (
                        <div className="overflow-x-auto border border-slate-200 rounded-lg">
                            <table className="mezan-table w-full text-sm">
                                <thead className="bg-slate-50 text-slate-600 text-xs">
                                    <tr>
                                        <th className="text-right p-2 font-bold">التاريخ</th>
                                        <th className="text-right p-2 font-bold">النوع</th>
                                        <th className="text-right p-2 font-bold">المبلغ</th>
                                        <th className="text-right p-2 font-bold">الرصيد بعد</th>
                                        <th className="text-right p-2 font-bold">المديونية بعد</th>
                                        <th className="text-right p-2 font-bold">الوصف</th>
                                        <th className="text-right p-2 font-bold w-20"></th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows.map((r) => {
                                        const t = LEDGER_LABEL[r.type] || LEDGER_LABEL.manual;
                                        const isEditing = editingId === r.id;
                                        const isEditableTopup = r.type === "topup";
                                        return (
                                            <tr key={r.id} className={`border-t border-slate-100 ${isEditing ? "bg-amber-50/40" : ""}`} data-testid={`adacc-ledger-row-${r.id}`}>
                                                {isEditing ? (
                                                    <>
                                                        <td className="p-2">
                                                            <input type="date" value={editForm.transaction_date}
                                                                onChange={(e) => setEditForm({ ...editForm, transaction_date: e.target.value })}
                                                                className="w-full px-2 py-1 text-xs rounded border border-slate-300"
                                                                data-testid={`adacc-ledger-edit-date-${r.id}`} />
                                                        </td>
                                                        <td className="p-2"><span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${t.tone}`}>{t.label}</span></td>
                                                        <td className="p-2">
                                                            <input type="number" min="0.01" step="0.01" value={editForm.amount}
                                                                onChange={(e) => setEditForm({ ...editForm, amount: e.target.value })}
                                                                className="w-24 px-2 py-1 text-xs rounded border border-slate-300 num"
                                                                data-testid={`adacc-ledger-edit-amount-${r.id}`} />
                                                        </td>
                                                        <td className="p-2 num text-xs text-emerald-700">{fmt(r.balance_after)}</td>
                                                        <td className="p-2 num text-xs text-rose-700">{fmt(r.debt_after)}</td>
                                                        <td className="p-2 text-xs text-slate-600">{r.description || "—"}</td>
                                                        <td className="p-2 flex gap-1">
                                                            <button onClick={saveEdit} disabled={saving} className="px-2 py-1 rounded bg-emerald-600 text-white text-[10px] font-bold hover:bg-emerald-700 disabled:opacity-50" data-testid={`adacc-ledger-save-${r.id}`}>
                                                                {saving ? "..." : "✓ حفظ"}
                                                            </button>
                                                            <button onClick={() => setEditingId(null)} className="px-2 py-1 rounded bg-slate-200 text-slate-700 text-[10px] font-bold hover:bg-slate-300" data-testid={`adacc-ledger-cancel-${r.id}`}>
                                                                ✕
                                                            </button>
                                                        </td>
                                                    </>
                                                ) : (
                                                    <>
                                                        <td className="p-2 text-xs">{r.date}</td>
                                                        <td className="p-2"><span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${t.tone}`}>{t.label}</span></td>
                                                        <td className="p-2 num text-xs font-bold text-slate-900">{fmt(r.amount)}</td>
                                                        <td className="p-2 num text-xs text-emerald-700">{fmt(r.balance_after)}</td>
                                                        <td className="p-2 num text-xs text-rose-700">{fmt(r.debt_after)}</td>
                                                        <td className="p-2 text-xs text-slate-600">
                                                            {r.description || "—"}
                                                            {r.breakdown?.edited_at && (
                                                                <span className="block text-[10px] text-amber-700 mt-0.5">
                                                                    ✏️ مُعدَّل (كان {fmt(r.breakdown.previous_amount)})
                                                                </span>
                                                            )}
                                                        </td>
                                                        <td className="p-2">
                                                            {isEditableTopup && (
                                                                <button onClick={() => startEdit(r)} className="px-2 py-1 rounded bg-amber-100 text-amber-800 text-[10px] font-bold hover:bg-amber-200" data-testid={`adacc-ledger-edit-${r.id}`}>
                                                                    ✏️ تعديل
                                                                </button>
                                                            )}
                                                        </td>
                                                    </>
                                                )}
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}


// ── Migration Preview + Apply dialog (Iter-110) ─────────────────────
function MigrationDialog({ open, onClose, onSaved }) {
    const monthStart = () => monthStartSA();
    const [step, setStep] = useState(1);            // 1 = pick dates · 2 = review · 3 = done
    const [form, setForm] = useState({ from_date: monthStart(), to_date: todayIso() });
    const [busy, setBusy] = useState(false);
    const [preview, setPreview] = useState(null);   // { accounts, totals, ... }
    const [selected, setSelected] = useState({});   // {cp_id: true|false}
    const [mode, setMode] = useState("daily");      // "daily" | "lump"
    const [result, setResult] = useState(null);
    const [showDailyFor, setShowDailyFor] = useState(null);

    useEffect(() => {
        if (!open) return;
        setStep(1);
        setForm({ from_date: monthStart(), to_date: todayIso() });
        setPreview(null);
        setSelected({});
        setMode("daily");
        setResult(null);
        setShowDailyFor(null);
    }, [open]);

    if (!open) return null;

    const fetchPreview = async (e) => {
        e?.preventDefault?.();
        if (!form.from_date || !form.to_date) { toast.error("اختر الفترة"); return; }
        if (form.from_date > form.to_date) { toast.error("من تاريخ يجب أن يكون قبل إلى تاريخ"); return; }
        setBusy(true);
        try {
            const { data } = await api.post("/ad-accounts/migration/preview", form);
            setPreview(data);
            // Pre-tick only the rows that are NOT blocked and have data
            const tick = {};
            (data.accounts || []).forEach((a) => {
                tick[a.id] = !a.blocked_by_default && a.period_spend > 0;
            });
            setSelected(tick);
            setStep(2);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل تحميل المعاينة");
        } finally { setBusy(false); }
    };

    const toggleAll = (val) => {
        const next = {};
        (preview?.accounts || []).forEach((a) => {
            next[a.id] = val && !a.blocked_by_default && a.period_spend > 0;
        });
        setSelected(next);
    };

    const apply = async () => {
        const ids = Object.entries(selected).filter(([, v]) => v).map(([k]) => k);
        if (ids.length === 0) { toast.error("اختر حساباً واحداً على الأقل"); return; }
        if (!window.confirm(
            `سيتم ترحيل ${ids.length} حساب بوضع ${mode === "daily" ? "يومي (سطر لكل يوم)" : "إجمالي مجمّع"}. ` +
            `لا يمكن التراجع تلقائياً — راجع المعاينة جيداً. هل أنت متأكد؟`
        )) return;
        setBusy(true);
        try {
            const { data } = await api.post("/ad-accounts/migration/apply", {
                from_date: form.from_date, to_date: form.to_date,
                mode, account_ids: ids,
            });
            setResult(data);
            setStep(3);
            onSaved();
            toast.success(`تم الترحيل لـ ${data.results.filter((r) => r.ok).length} حساب`);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل الترحيل");
        } finally { setBusy(false); }
    };

    const totalSelected = preview?.accounts
        ?.filter((a) => selected[a.id])
        .reduce((s, a) => s + (a.period_spend || 0), 0) || 0;

    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="adacc-migration-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-5xl my-8">
                <div className="flex items-center justify-between p-5 border-b border-slate-100">
                    <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                        <ArrowsClockwise size={22} weight="duotone" className="text-amber-700" />
                        ترحيل المديونيات التاريخية
                        <span className="text-[11px] font-normal text-slate-500">— الخطوة {step} من 3</span>
                    </h2>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                </div>

                {/* STEP 1 — pick dates */}
                {step === 1 && (
                    <form onSubmit={fetchPreview} className="p-5 space-y-4">
                        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 text-xs text-amber-900 space-y-1">
                            <div className="font-bold">💡 كيف يعمل الترحيل؟</div>
                            <ul className="list-disc pr-5 space-y-1">
                                <li>نقرأ صرف كل حساب إعلاني من بيانات المنصة المخزّنة عندك.</li>
                                <li>الحسابات غير المربوطة بـ <b>Ad Account ID</b> ستُمنع افتراضياً حتى لا تندمج مع غيرها.</li>
                                <li>الترحيل <b>قابل لإعادة التشغيل بأمان</b> — لو رحّلت نفس الفترة مرتين، يستبدل النظام السجلات السابقة بالأرقام الجديدة (لا ازدواجية في الصرف أو المديونية).</li>
                            </ul>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">من تاريخ *</label>
                                <input type="date" value={form.from_date} onChange={(e) => setForm({ ...form, from_date: e.target.value })} className={inputCls} data-testid="adacc-mig-from" />
                            </div>
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">إلى تاريخ *</label>
                                <input type="date" value={form.to_date} onChange={(e) => setForm({ ...form, to_date: e.target.value })} className={inputCls} data-testid="adacc-mig-to" />
                            </div>
                        </div>
                        <div className="flex justify-end gap-2 pt-2">
                            <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold">إلغاء</button>
                            <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-amber-700 text-white text-sm font-bold hover:bg-amber-800 disabled:opacity-50" data-testid="adacc-mig-preview-btn">
                                {busy ? "جاري…" : "عرض المعاينة"}
                            </button>
                        </div>
                    </form>
                )}

                {/* STEP 2 — preview & select */}
                {step === 2 && preview && (
                    <div className="p-5 space-y-4">
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                            <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
                                <div className="font-bold text-slate-600">الفترة</div>
                                <div className="text-slate-900 mt-1 font-mono text-[11px]">{preview.from_date} → {preview.to_date}</div>
                            </div>
                            <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3">
                                <div className="font-bold text-emerald-800">جاهز للترحيل</div>
                                <div className="num text-emerald-900 text-base font-extrabold mt-1">{preview.totals.accounts_ready}</div>
                            </div>
                            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                                <div className="font-bold text-amber-800">يحتاج مراجعة</div>
                                <div className="num text-amber-900 text-base font-extrabold mt-1">{preview.totals.accounts_warned}</div>
                            </div>
                            <div className="bg-violet-50 border border-violet-200 rounded-lg p-3">
                                <div className="font-bold text-violet-800">إجمالي الصرف</div>
                                <div className="num text-violet-900 text-base font-extrabold mt-1">{fmt(preview.totals.period_spend)}</div>
                            </div>
                        </div>

                        <div className="flex flex-wrap items-center gap-3 bg-slate-50 border border-slate-200 rounded-lg p-3">
                            <div className="text-xs font-bold text-slate-700">وضع الترحيل:</div>
                            <label className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border cursor-pointer text-xs font-bold ${mode === "daily" ? "bg-amber-100 border-amber-300 text-amber-900" : "bg-white border-slate-300 text-slate-600"}`}>
                                <input type="radio" name="mode" value="daily" checked={mode === "daily"} onChange={() => setMode("daily")} data-testid="adacc-mig-mode-daily" />
                                يومي — سطر لكل يوم (أدق ✓ افتراضي)
                            </label>
                            <label className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border cursor-pointer text-xs font-bold ${mode === "lump" ? "bg-amber-100 border-amber-300 text-amber-900" : "bg-white border-slate-300 text-slate-600"}`}>
                                <input type="radio" name="mode" value="lump" checked={mode === "lump"} onChange={() => setMode("lump")} data-testid="adacc-mig-mode-lump" />
                                مجمّع — صرف واحد للفترة (أسرع)
                            </label>
                            <div className="ml-auto flex gap-2">
                                <button onClick={() => toggleAll(true)} className="text-[11px] underline text-slate-700" data-testid="adacc-mig-select-all">حدد القابل للترحيل</button>
                                <button onClick={() => toggleAll(false)} className="text-[11px] underline text-slate-700" data-testid="adacc-mig-clear">إلغاء التحديد</button>
                            </div>
                        </div>

                        <div className="overflow-x-auto border border-slate-200 rounded-lg max-h-[420px] overflow-y-auto">
                            <table className="mezan-table w-full text-sm">
                                <thead className="bg-slate-50 text-slate-600 text-[11px] sticky top-0">
                                    <tr>
                                        <th className="p-2 w-8"></th>
                                        <th className="text-right p-2 font-bold">الحساب</th>
                                        <th className="text-right p-2 font-bold">المنصة</th>
                                        <th className="text-right p-2 font-bold">Ad Account ID</th>
                                        <th className="text-right p-2 font-bold">صرف الفترة</th>
                                        <th className="text-right p-2 font-bold">أيام</th>
                                        <th className="text-right p-2 font-bold">الرصيد الحالي</th>
                                        <th className="text-right p-2 font-bold">المديونية الحالية</th>
                                        <th className="text-right p-2 font-bold">الوضع</th>
                                        <th className="text-right p-2 font-bold">تنبيهات</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {preview.accounts.map((a) => {
                                        const disabled = a.blocked_by_default || a.period_spend === 0;
                                        return (
                                            <tr key={a.id} className={`border-t border-slate-100 ${a.blocked_by_default ? "bg-amber-50/30" : ""}`} data-testid={`adacc-mig-row-${a.id}`}>
                                                <td className="p-2">
                                                    <input type="checkbox" checked={!!selected[a.id]} disabled={disabled} onChange={(e) => setSelected({ ...selected, [a.id]: e.target.checked })} data-testid={`adacc-mig-check-${a.id}`} />
                                                </td>
                                                <td className="p-2 text-xs font-bold text-slate-900">{a.name}</td>
                                                <td className="p-2 text-xs">
                                                    <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-700">
                                                        {PROVIDER_LABEL[a.ad_provider] || a.ad_provider}
                                                    </span>
                                                </td>
                                                <td className="p-2 text-[11px] font-mono text-slate-700" dir="ltr">
                                                    {a.external_account_id || <span className="text-rose-500">— غير مربوط</span>}
                                                </td>
                                                <td className="p-2 num text-xs font-bold text-violet-900">{fmt(a.period_spend)}</td>
                                                <td className="p-2 num text-xs text-slate-600">
                                                    {a.days_with_data}
                                                    {a.days_with_data > 0 && (
                                                        <button onClick={() => setShowDailyFor(a)} className="ml-1 text-violet-600 underline text-[10px]" data-testid={`adacc-mig-daily-${a.id}`}>
                                                            عرض
                                                        </button>
                                                    )}
                                                </td>
                                                <td className="p-2 num text-xs text-emerald-700">{fmt(a.current_balance)}</td>
                                                <td className="p-2 num text-xs text-rose-700">{fmt(a.current_open_debt)}</td>
                                                <td className="p-2 text-[11px]">
                                                    <span className={`px-2 py-0.5 rounded font-bold ${a.debt_mode === "auto" ? "bg-violet-50 text-violet-800" : "bg-amber-50 text-amber-800"}`}>
                                                        {a.debt_mode === "auto" ? "تلقائي" : "يدوي"}
                                                    </span>
                                                </td>
                                                <td className="p-2 text-[11px]">
                                                    {a.warnings.length === 0 ? (
                                                        <span className="flex items-center gap-1 text-emerald-700"><CheckCircle size={12} /> سليم</span>
                                                    ) : (
                                                        <div className="space-y-1">
                                                            {a.warnings.map((w, i) => (
                                                                <div key={i} className="flex items-start gap-1 text-amber-800">
                                                                    <Warning size={12} className="shrink-0 mt-0.5" /> <span>{w}</span>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    )}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>

                        <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
                            <div className="text-xs text-slate-600">
                                المحدد للترحيل: <b className="num text-slate-900">{Object.values(selected).filter(Boolean).length}</b> حساب ·
                                إجمالي الصرف: <b className="num text-violet-900">{fmt(totalSelected)}</b>
                            </div>
                            <div className="flex gap-2">
                                <button onClick={() => setStep(1)} className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold">← رجوع</button>
                                <button onClick={apply} disabled={busy || Object.values(selected).filter(Boolean).length === 0} className="px-4 py-2 rounded-lg bg-amber-700 text-white text-sm font-bold hover:bg-amber-800 disabled:opacity-50" data-testid="adacc-mig-apply-btn">
                                    {busy ? "جاري الترحيل…" : `تنفيذ الترحيل (${mode === "daily" ? "يومي" : "مجمّع"})`}
                                </button>
                            </div>
                        </div>
                    </div>
                )}

                {/* STEP 3 — done */}
                {step === 3 && result && (
                    <div className="p-5 space-y-3">
                        <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4 text-sm text-emerald-900">
                            ✅ تمت العملية. أدناه تفاصيل ما تم.
                        </div>
                        {(() => {
                            const totalReversed = (result.results || [])
                                .reduce((s, r) => s + (r.reversed_prior_rows || 0), 0);
                            return totalReversed > 0 ? (
                                <div
                                    className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-[12px] text-amber-900"
                                    data-testid="adacc-mig-reversed-notice"
                                >
                                    🔁 تم استبدال <b className="num">{totalReversed}</b> سطر ترحيل سابق ضمن نفس النطاق ليصبح هذا الترحيل هو السجل الفعّال (لا ازدواجية).
                                </div>
                            ) : null;
                        })()}
                        <div className="overflow-x-auto border border-slate-200 rounded-lg">
                            <table className="mezan-table w-full text-sm">
                                <thead className="bg-slate-50 text-slate-600 text-[11px]">
                                    <tr>
                                        <th className="text-right p-2 font-bold">الحساب</th>
                                        <th className="text-right p-2 font-bold">الحالة</th>
                                        <th className="text-right p-2 font-bold">سطور مُرحّلة</th>
                                        <th className="text-right p-2 font-bold">سُحب سابق</th>
                                        <th className="text-right p-2 font-bold">إجمالي الصرف</th>
                                        <th className="text-right p-2 font-bold">مديونية أُنشئت</th>
                                        <th className="text-right p-2 font-bold">الرصيد بعد</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {result.results.map((r) => (
                                        <tr key={r.id} className="border-t border-slate-100">
                                            <td className="p-2 text-xs font-bold">{r.name || r.id}</td>
                                            <td className="p-2 text-[11px]">
                                                {r.ok ? (
                                                    <span className="text-emerald-700 flex items-center gap-1"><CheckCircle size={12} /> تم</span>
                                                ) : (
                                                    <span className="text-rose-700 flex items-center gap-1"><Warning size={12} /> {r.error}</span>
                                                )}
                                            </td>
                                            <td className="p-2 num text-xs">{r.rows_posted ?? "—"}</td>
                                            <td
                                                className="p-2 num text-xs text-amber-700"
                                                data-testid={`adacc-mig-reversed-${r.id}`}
                                            >
                                                {r.reversed_prior_rows ? r.reversed_prior_rows : "—"}
                                            </td>
                                            <td className="p-2 num text-xs text-violet-900">{r.total_spend != null ? fmt(r.total_spend) : "—"}</td>
                                            <td className="p-2 num text-xs text-rose-700">{r.debt_created != null ? fmt(r.debt_created) : "—"}</td>
                                            <td className="p-2 num text-xs text-emerald-700">{r.balance_after != null ? fmt(r.balance_after) : "—"}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        <div className="flex justify-end pt-2">
                            <button onClick={onClose} className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-bold" data-testid="adacc-mig-close-btn">إغلاق</button>
                        </div>
                    </div>
                )}

                {/* Daily-rows preview popover */}
                {showDailyFor && (
                    <div className="fixed inset-0 z-[60] bg-slate-900/60 flex items-center justify-center p-4" onClick={() => setShowDailyFor(null)}>
                        <div className="bg-white rounded-xl shadow-2xl w-full max-w-sm max-h-[70vh] overflow-y-auto" dir="rtl" onClick={(e) => e.stopPropagation()}>
                            <div className="flex items-center justify-between p-4 border-b border-slate-100">
                                <h3 className="font-bold text-slate-900 text-sm">صرف يومي — {showDailyFor.name}</h3>
                                <button onClick={() => setShowDailyFor(null)} className="text-slate-500 text-xl">×</button>
                            </div>
                            <table className="mezan-table compact w-full text-xs">
                                <thead className="bg-slate-50 text-slate-600">
                                    <tr><th className="text-right p-2">التاريخ</th><th className="text-right p-2">الصرف</th></tr>
                                </thead>
                                <tbody>
                                    {showDailyFor.daily_rows.map((r) => (
                                        <tr key={r.date} className="border-t border-slate-100">
                                            <td className="p-2 font-mono">{r.date}</td>
                                            <td className="p-2 num">{fmt(r.spend)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            {showDailyFor.daily_rows_truncated && (
                                <div className="text-[10px] text-slate-500 p-2 text-center">… مقتطع — لعرض الكل نفّذ الترحيل بوضع يومي.</div>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}


// ── Opening Balance dialog (Iter-110) ───────────────────────────────
function OpeningDialog({ row, open, onClose, onSaved }) {
    const [form, setForm] = useState({
        opening_balance: "", opening_debt: "",
        start_date: todayIso(), method: "auto", notes: "",
    });
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open || !row) return;
        setForm({
            opening_balance: row.balance != null ? String(row.balance) : "",
            opening_debt:    row.open_debt != null ? String(row.open_debt) : "",
            start_date:      row.opening_start_date || todayIso(),
            method:          row.debt_mode || "auto",
            notes:           row.opening_notes || "",
        });
    }, [open, row]);

    if (!open || !row) return null;

    const submit = async (e) => {
        e?.preventDefault?.();
        const payload = {
            opening_balance: form.opening_balance === "" ? null : Number(form.opening_balance),
            opening_debt:    form.opening_debt === "" ? null : Number(form.opening_debt),
            start_date:      form.start_date || null,
            method:          form.method,
            notes:           form.notes || null,
        };
        setBusy(true);
        try {
            await api.put(`/ad-accounts/${row.id}/opening`, payload);
            toast.success("تم حفظ الرصيد الافتتاحي");
            onSaved();
            onClose();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل الحفظ");
        } finally { setBusy(false); }
    };

    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="adacc-opening-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-md my-8">
                <form onSubmit={submit}>
                    <div className="flex items-center justify-between p-5 border-b border-slate-100">
                        <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                            <Plus size={22} weight="duotone" className="text-amber-700" />
                            رصيد افتتاحي — {row.name}
                        </h2>
                        <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                    </div>
                    <div className="p-5 space-y-3">
                        <div className="bg-amber-50 border border-amber-200 rounded p-3 text-[11px] text-amber-900">
                            💡 يُستخدم هذا الخيار عندما تفضّل البدء من اليوم بدل ترحيل بيانات تاريخية، أو لتعديل أرقام افتتاحية يدوياً. الرصيد الافتتاحي يُضاف فوراً، والمديونية الافتتاحية تُسجَّل كالتزام مفتوح منفصل (مصدر: ad_account_opening).
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">الرصيد الافتتاحي</label>
                                <input type="number" min="0" step="0.01" value={form.opening_balance} onChange={(e) => setForm({ ...form, opening_balance: e.target.value })} className={`${inputCls} num`} data-testid="adacc-opening-balance" placeholder="0.00" />
                            </div>
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">المديونية الافتتاحية</label>
                                <input type="number" min="0" step="0.01" value={form.opening_debt} onChange={(e) => setForm({ ...form, opening_debt: e.target.value })} className={`${inputCls} num`} data-testid="adacc-opening-debt" placeholder="0.00" />
                            </div>
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">تاريخ بداية الاحتساب</label>
                            <input type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} className={inputCls} data-testid="adacc-opening-start" />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">طريقة الاحتساب</label>
                            <select value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })} className={inputCls} data-testid="adacc-opening-method">
                                <option value="auto">تلقائي من الصرف (recommended)</option>
                                <option value="manual">يدوي فقط</option>
                            </select>
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">ملاحظات</label>
                            <input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className={inputCls} placeholder="مثال: رصيد افتتاحي من 2026-06-01" />
                        </div>
                    </div>
                    <div className="p-5 border-t border-slate-100 flex justify-end gap-2">
                        <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold">إلغاء</button>
                        <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-amber-700 text-white text-sm font-bold hover:bg-amber-800 disabled:opacity-50" data-testid="adacc-opening-submit">
                            {busy ? "جاري…" : "حفظ"}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}


// ── Main page ───────────────────────────────────────────────────────
export default function AdAccounts() {
    const [items, setItems] = useState([]);
    const [totals, setTotals] = useState({ balance: 0, open_debt: 0, total_spend: 0 });
    const [banks, setBanks] = useState([]);
    const [loading, setLoading] = useState(true);
    const [topupFor, setTopupFor] = useState(null);
    const [spendFor, setSpendFor] = useState(null);
    const [ledgerFor, setLedgerFor] = useState(null);
    const [syncFor, setSyncFor] = useState(null);
    const [createOpen, setCreateOpen] = useState(false);
    const [migrationOpen, setMigrationOpen] = useState(false);
    const [openingFor, setOpeningFor] = useState(null);
    const [allowDelete, setAllowDelete] = useState(false);
    const [diagnose, setDiagnose] = useState(null);
    const [diagBusy, setDiagBusy] = useState(false);
    // Iter-204 — surface the most-recent fetch time so merchants
    // can confirm the silent half-hour auto-refresh is working.
    const [lastLoaded, setLastLoaded] = useState(null);
    // Iter-211 — { ad_account_id: { status, days_stale, last_spend_date } }
    const [syncHealth, setSyncHealth] = useState({});

    const runDiagnose = async () => {
        setDiagBusy(true);
        try {
            const { data } = await api.get("/ad-accounts/diagnose");
            setDiagnose(data);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل التشخيص");
        } finally { setDiagBusy(false); }
    };

    const load = async () => {
        setLoading(true);
        try {
            const [adRes, accRes, settingsRes, healthRes] = await Promise.all([
                api.get("/ad-accounts"),
                // Iter-110 fix — endpoint expects `account_type=` (not `type=`)
                // and returns a plain list, not `{items: []}`. Filter to
                // visible active bank accounts only.
                api.get("/accounts?account_type=bank"),
                api.get("/settings"),
                // Iter-211 — sync staleness per account
                api.get("/ad-accounts/diagnostics/sync-health").catch(() => ({ data: null })),
            ]);
            setItems(adRes.data?.items || []);
            setTotals(adRes.data?.totals || {});
            const rawAccounts = Array.isArray(accRes.data)
                ? accRes.data
                : (accRes.data?.items || accRes.data?.accounts || []);
            setBanks(rawAccounts.filter(
                (a) => a.account_type === "bank"
                    && a.status !== "hidden"
                    && a.status !== "inactive",
            ));
            setAllowDelete(!!settingsRes.data?.ad_account_allow_delete);
            // Iter-211 — index health by ad-account id for quick lookup
            const healthMap = {};
            for (const h of (healthRes?.data?.accounts || [])) {
                healthMap[h.id] = h;
            }
            setSyncHealth(healthMap);
            setLastLoaded(Date.now());
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التحميل");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

    // Iter-204 — Silent auto-poll every 30 minutes so the ad-account
    // balances/debt reflect the half-hour backend cron without forcing
    // the merchant to reload. Only fires while the tab is visible.
    useEffect(() => {
        const id = setInterval(() => {
            if (document.visibilityState === "visible") load();
        }, 30 * 60 * 1000);
        return () => clearInterval(id);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Also refetch when the user returns to the tab (e.g. after
    // switching apps), so stale numbers are flushed instantly.
    useEffect(() => {
        const onVis = () => {
            if (document.visibilityState === "visible") load();
        };
        document.addEventListener("visibilitychange", onVis);
        return () => document.removeEventListener("visibilitychange", onVis);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const deleteAccount = async (row) => {
        const balance = Number(row.balance || 0);
        const debt = Number(row.open_debt || 0);
        if (balance > 0 || debt > 0) {
            toast.error(
                `لا يمكن حذف "${row.name}" — الرصيد ${fmt(balance)} والمديونية ${fmt(debt)}. سدّد المديونية واصرف الرصيد أولاً.`
            );
            return;
        }
        if (!window.confirm(
            `هل أنت متأكد من حذف الحساب "${row.name}"؟\n\n` +
            `هذه العملية لا يمكن التراجع عنها — سيُحذف الحساب نهائياً من قائمة الحسابات الإعلانية.\n\n` +
            `ملاحظة: لن تتأثر بيانات الصرف اليومي المخزّنة في جداول المنصة (snapchat/meta/tiktok daily).`
        )) return;
        try {
            await api.delete(`/ad-accounts/${row.id}`);
            toast.success(`تم حذف الحساب "${row.name}"`);
            load();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل الحذف");
        }
    };

    const toggleMode = async (row) => {
        const next = row.debt_mode === "auto" ? "manual" : "auto";
        try {
            await api.put(`/ad-accounts/${row.id}/settings`, { debt_mode: next });
            toast.success(`الوضع الآن: ${next === "auto" ? "تلقائي" : "يدوي"}`);
            load();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        }
    };

    return (
        <div dir="rtl" data-testid="ad-accounts-page" className="space-y-5">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                        <ChartLineUp size={28} weight="duotone" className="text-violet-700" />
                        الحسابات الإعلانية والمديونية
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        رصيد المنصة الإعلانية = أصل · مديونية تلقائية تنشأ عند تجاوز الصرف للرصيد. الصرف يبقى مصروف إعلاني واحد دون تكرار.
                    </p>
                </div>
                <div className="flex flex-col sm:flex-row gap-2 self-start">
                    <button onClick={runDiagnose} disabled={diagBusy} className="px-4 py-2.5 rounded-lg bg-blue-100 text-blue-800 text-sm font-bold hover:bg-blue-200 flex items-center gap-2 disabled:opacity-50" data-testid="adacc-diagnose-btn">
                        🩺 {diagBusy ? "جاري التشخيص…" : "تشخيص المزامنة"}
                    </button>
                    <button onClick={() => setMigrationOpen(true)} className="px-4 py-2.5 rounded-lg bg-amber-100 text-amber-800 text-sm font-bold hover:bg-amber-200 flex items-center gap-2" data-testid="adacc-migration-btn">
                        <ArrowsClockwise size={16} /> ترحيل المديونيات التاريخية
                    </button>
                    <button
                        onClick={async () => {
                            try {
                                const { data: preview } = await api.post(
                                    "/ad-accounts/migration/cleanup-duplicates?dry_run=true",
                                );
                                const s = preview.summary || {};
                                if ((s.duplicate_ledger_rows_removed || 0) === 0
                                    && (s.duplicate_liabilities_merged || 0) === 0) {
                                    toast.success("لا توجد ترحيلات مكررة — حسابك نظيف ✨");
                                    return;
                                }
                                const lines = [
                                    `سيتم تنظيف الترحيلات المُكرّرة:`,
                                    `• حسابات تم فحصها: ${s.counterparties_scanned}`,
                                    `• سطور سيتم حذفها: ${s.duplicate_ledger_rows_removed}`,
                                    `• مديونيات مكررة سيتم دمجها: ${s.duplicate_liabilities_merged}`,
                                    `• رصيد سيُستعاد: ${(s.balance_restored || 0).toLocaleString()} ر.س`,
                                    `• قيمة مديونية ستُخصم: ${(s.liability_amount_reduced || 0).toLocaleString()} ر.س`,
                                    ``,
                                    `هل تريد المتابعة؟ (لا يمكن التراجع)`,
                                ].join("\n");
                                if (!window.confirm(lines)) return;
                                const { data: applied } = await api.post(
                                    "/ad-accounts/migration/cleanup-duplicates?dry_run=false",
                                );
                                const a = applied.summary || {};
                                toast.success(
                                    `تم التنظيف · ${a.duplicate_ledger_rows_removed} سطر · `
                                    + `${a.duplicate_liabilities_merged} مديونية مدموجة`
                                );
                                load();
                            } catch (e) {
                                toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل التنظيف");
                            }
                        }}
                        className="px-4 py-2.5 rounded-lg bg-rose-100 text-rose-800 text-sm font-bold hover:bg-rose-200 flex items-center gap-2"
                        data-testid="adacc-cleanup-duplicates-btn"
                        title="ينظّف الترحيلات المكررة من قبل إصلاح Iter-133"
                    >
                        🧹 تنظيف الترحيلات المُكرّرة
                    </button>
                    <button
                        onClick={async () => {
                            const ok = window.confirm(
                                "هذا الإصلاح سيمحو أي مصاريف خاطئة تم ترحيلها تلقائياً من " +
                                "النظام لحسابات إعلانية لا تحتوي على Ad Account ID (Snap/Meta) " +
                                "خلال آخر 7 أيام. هذا لمعالجة خطأ تجمّع المصاريف الذي ظهر في " +
                                "فبراير 2026 (مثلاً 100,000 ر.س خاطئة).\n\nمتابعة؟"
                            );
                            if (!ok) return;
                            try {
                                const { data } = await api.post(
                                    "/ad-accounts/recover/cross-account-leak");
                                if (!data.recovered || data.recovered.length === 0) {
                                    toast.success("لا توجد سطور خاطئة لمعالجتها — حسابك سليم ✓");
                                    return;
                                }
                                const lines = data.recovered.map(
                                    (r) => `${r.name}: حذف ${r.rows_deleted} سطر بقيمة ${fmt(r.amount_reversed)} ر.س`);
                                toast.success(
                                    `تم الإصلاح · إجمالي ${fmt(data.total_amount_reversed)} ر.س\n` + lines.join("\n"),
                                    { duration: 9000 }
                                );
                                load();
                            } catch (e) {
                                toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل");
                            }
                        }}
                        className="px-4 py-2.5 rounded-lg bg-amber-100 text-amber-900 text-sm font-bold hover:bg-amber-200 flex items-center gap-2"
                        data-testid="adacc-recover-leak-btn"
                        title="يصحّح مصاريف اليوم الخاطئة الناتجة عن خلل المزامنة (Iter-163)"
                    >
                        🛟 إصلاح مصروف اليوم الخاطئ
                    </button>
                    <button onClick={async () => {
                        const today = todayIso();
                        const doSync = async (force = false) => {
                            try {
                                const { data } = await api.post("/ad-accounts/sync-all", {
                                    from_date: today, to_date: today, force,
                                });
                                const processed = data.results.filter((r) => !r.skipped).length;
                                const skipped   = data.results.filter((r) =>  r.skipped).length;
                                const debt = data.results.reduce((s, r) => s + (r.debt_created || 0), 0);
                                // Iter-163 — surface accounts skipped due to
                                // missing external_account_id so the merchant
                                // knows the cross-account spend bug is prevented.
                                const missingExt = data.results.filter(
                                    (r) => r.reason === "missing_external_account_id");
                                if (missingExt.length > 0) {
                                    const names = missingExt.map((r) => r.name).join("، ");
                                    toast.warning(
                                        `الحسابات التالية لم يتم مزامنتها لعدم وجود معرّف خارجي: ${names}. عدّل الحساب وأضف external_account_id.`,
                                        { duration: 8000 }
                                    );
                                }

                                // Iter-110 — auto-offer a forced retry if EVERYTHING was skipped
                                // (signals a stale `last_auto_sync_date` flag from a previous
                                // run that did not actually create rows).
                                if (!force && processed === 0 && skipped > 0) {
                                    const ok = window.confirm(
                                        `تم تخطّي كل الحسابات (${skipped}) لأن النظام يرى أنها مزامَنة سابقاً اليوم.\n` +
                                        `هل تريد إعادة المزامنة بشكل إجباري (تجاوز التحقق التكراري)؟\n\n` +
                                        `استخدم هذا الخيار فقط إذا كنت متأكداً أن المزامنة السابقة لم تنتج مديونية صحيحة.`
                                    );
                                    if (ok) return doSync(true);
                                }

                                if (processed > 0) {
                                    toast.success(
                                        `المزامنة تمت: ${processed} حساب${debt > 0 ? ` · مديونية ${fmt(debt)} ر.س` : ""}${skipped ? ` · ${skipped} مُتخطّى` : ""}`
                                    );
                                } else {
                                    toast.info(`المزامنة تمت: ${processed} حساب${skipped ? ` · ${skipped} مُتخطّى` : ""}`);
                                }
                                load();
                            } catch (e) {
                                toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل");
                            }
                        };
                        doSync(false);
                    }} className="px-4 py-2.5 rounded-lg bg-violet-100 text-violet-800 text-sm font-bold hover:bg-violet-200 flex items-center gap-2" data-testid="adacc-sync-all-btn">
                        <ArrowsClockwise size={16} /> مزامنة الكل الآن
                    </button>
                    <button onClick={() => setCreateOpen(true)} className="px-4 py-2.5 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-800 flex items-center gap-2" data-testid="adacc-add-btn">
                        <Plus size={16} /> إضافة حساب إعلاني
                    </button>
                </div>
            </div>
            <div className="text-[11px] text-slate-500 -mt-3 mb-1 flex items-center gap-2 flex-wrap">
                <span>💡 المزامنة التلقائية تعمل في الخلفية كل <b>30 دقيقة</b> لكل الحسابات المدعومة (Snap / TikTok / Meta) — بدون الحاجة لفتح هذه الصفحة. اضغط "مزامنة الكل الآن" لتحديث فوري.</span>
                {lastLoaded && (
                    <span className="text-emerald-700 font-bold" data-testid="adacc-last-loaded">
                        · آخر تحديث: {new Date(lastLoaded).toLocaleTimeString("ar-SA", { hour: "2-digit", minute: "2-digit" })}
                    </span>
                )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                    <div className="text-xs font-bold text-emerald-800 mb-1">إجمالي الأرصدة الإعلانية</div>
                    <div className="num text-2xl font-extrabold text-emerald-900">{fmt(totals.balance)} ر.س</div>
                </div>
                <div className="rounded-xl border border-rose-200 bg-rose-50 p-4">
                    <div className="text-xs font-bold text-rose-800 mb-1">إجمالي المديونيات الإعلانية</div>
                    <div className="num text-2xl font-extrabold text-rose-900">{fmt(totals.open_debt)} ر.س</div>
                </div>
                <div className="rounded-xl border border-violet-200 bg-violet-50 p-4">
                    <div className="text-xs font-bold text-violet-800 mb-1">إجمالي الصرف التراكمي</div>
                    <div className="num text-2xl font-extrabold text-violet-900">{fmt(totals.total_spend)} ر.س</div>
                </div>
            </div>

            {loading ? (
                <div className="p-10 text-center text-slate-500 text-sm">جاري التحميل…</div>
            ) : items.length === 0 ? (
                <div className="p-10 text-center text-slate-500 text-sm bg-white border border-slate-200 rounded-xl" data-testid="adacc-empty">
                    لا توجد حسابات إعلانية. أضف الحسابات من <b>قائمة الأطراف الموحَّدة</b> (kind = ad_account).
                </div>
            ) : (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-stretch">
                    {items.map((row) => (
                        <div key={row.id} className="bg-white border border-slate-200 rounded-xl p-5 flex flex-col gap-4 h-full hover:border-slate-300 hover:shadow-sm transition-all duration-200" data-testid={`adacc-card-${row.id}`}>
                            <div className="flex items-start justify-between gap-2">
                                <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <h3 className="font-extrabold text-base text-slate-900 truncate">{row.name}</h3>
                                        <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-slate-100 text-slate-700 whitespace-nowrap shrink-0">
                                            {PROVIDER_LABEL[row.ad_provider] || row.ad_provider}
                                        </span>
                                        {/* Iter-211 — sync staleness pill */}
                                        {(() => {
                                            const h = syncHealth[row.id];
                                            if (!h) return null;
                                            const cfg = {
                                                healthy: { tone: "emerald", emoji: "🟢", label: "البيانات محدّثة" },
                                                warning: { tone: "amber",   emoji: "🟡", label: `قديمة ${h.days_stale} يوم` },
                                                stale:   { tone: "rose",    emoji: "🔴", label: `متوقفة منذ ${h.days_stale} يوم` },
                                                no_data: { tone: "slate",   emoji: "⚫", label: "لا بيانات" },
                                            }[h.status] || null;
                                            if (!cfg) return null;
                                            return (
                                                <span
                                                    className={`px-2 py-0.5 rounded text-[10px] font-bold bg-${cfg.tone}-50 text-${cfg.tone}-800 border border-${cfg.tone}-200 cursor-help`}
                                                    data-testid={`adacc-sync-status-${row.id}`}
                                                    title={
                                                        `حالة المزامنة: ${cfg.label}\n` +
                                                        `الجدولة: ${h.expected_interval || "—"}\n` +
                                                        `آخر بيانات: ${h.last_spend_date || "—"}\n` +
                                                        `مصدر: ${h.source_collection || "—"}\n` +
                                                        `آخر استلام: ${h.last_received_at || "—"}\n\n` +
                                                        (h.status === "stale" || h.status === "no_data"
                                                            ? "📡 لم تصل بيانات صرف جديدة.\n" +
                                                              (h.sync_via === "make_com"
                                                                  ? "هذا الحساب مربوط بـ Make.com (دورة 5 ساعات).\nافحص سجلات Make.com والـ Access Token للمنصة."
                                                                  : "هذا الحساب مربوط بـ API مباشر (دورة 30 دقيقة).\nافحص صلاحية المفاتيح ومراقبة لوحة التحكم للأخطاء.")
                                                            : h.status === "warning"
                                                                ? "⏳ البيانات متأخرة قليلاً — لو استمر التأخر راجع المصدر."
                                                                : "✅ البيانات تصل بانتظام.")
                                                    }
                                                >
                                                    {cfg.emoji} {cfg.label}
                                                </span>
                                            );
                                        })()}
                                    </div>
                                    {row.notes && <div className="text-xs text-slate-500 mt-1 line-clamp-2">{row.notes}</div>}
                                    {row.external_account_id && (
                                        <div className="text-[11px] text-slate-500 mt-1 font-mono truncate" dir="ltr" title={row.external_account_id}>
                                            🔗 {row.external_account_id}
                                        </div>
                                    )}
                                </div>
                                <button onClick={() => toggleMode(row)} className={`px-2 py-1 rounded-lg text-[11px] font-bold border whitespace-nowrap shrink-0 ${row.debt_mode === "auto" ? "bg-violet-50 text-violet-800 border-violet-200" : "bg-amber-50 text-amber-800 border-amber-200"}`} title="غيِّر وضع احتساب المديونية" data-testid={`adacc-mode-toggle-${row.id}`}>
                                    {row.debt_mode === "auto" ? "تلقائي" : "يدوي"}
                                </button>
                            </div>

                            <div className="grid grid-cols-3 gap-2">
                                <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 text-center flex flex-col justify-center min-h-[68px]">
                                    <div className="text-[10px] font-bold text-emerald-800 leading-tight">الرصيد</div>
                                    <div className="num text-base font-extrabold text-emerald-900 mt-1 leading-none">{fmt(row.balance)}</div>
                                </div>
                                <div className="bg-rose-50 border border-rose-200 rounded-lg p-3 text-center flex flex-col justify-center min-h-[68px]">
                                    <div className="text-[10px] font-bold text-rose-800 leading-tight">المديونية</div>
                                    <div className="num text-base font-extrabold text-rose-900 mt-1 leading-none">{fmt(row.open_debt)}</div>
                                </div>
                                <div className="bg-violet-50 border border-violet-200 rounded-lg p-3 text-center flex flex-col justify-center min-h-[68px]">
                                    <div className="text-[10px] font-bold text-violet-800 leading-tight">الصرف</div>
                                    <div className="num text-base font-extrabold text-violet-900 mt-1 leading-none">{fmt(row.total_spend)}</div>
                                </div>
                            </div>

                            {/* Iter-159i — Per-account credit limit + alert threshold */}
                            <CreditLimitPanel row={row} onSaved={load} fmt={fmt} />

                            {/* Iter-160 — Accounting actions (settlement/writeoff/adjustment/audit log) */}
                            <AccountingActionsPanel row={row} onDone={load} fmt={fmt} />

                            {/* Iter-168 — push the "last activity" + action buttons to the bottom so all cards in the grid have aligned footers regardless of mid-section content height. */}
                            <div className="mt-auto space-y-3">
                                <div className="space-y-1 text-[11px] text-slate-600 border-t border-slate-100 pt-3">
                                    <div className="flex items-center gap-1.5">
                                        <Clock size={11} className="text-slate-400 shrink-0" /> آخر تعبئة:
                                        <b className={`truncate ${row.last_topup ? "text-slate-900" : "text-slate-400"}`}>
                                            {row.last_topup ? `${row.last_topup.date} (${fmt(row.last_topup.amount)})` : "—"}
                                        </b>
                                    </div>
                                    <div className="flex items-center gap-1.5">
                                        <Clock size={11} className="text-slate-400 shrink-0" /> آخر صرف:
                                        <b className={`truncate ${row.last_spend ? "text-slate-900" : "text-slate-400"}`}>
                                            {row.last_spend ? `${row.last_spend.date} (${fmt(row.last_spend.amount)})` : "—"}
                                        </b>
                                    </div>
                                    <div className="flex items-center gap-1.5">
                                        <Clock size={11} className="text-slate-400 shrink-0" /> آخر مديونية:
                                        <b className={`truncate ${row.last_debt ? "text-rose-900" : "text-slate-400"}`}>
                                            {row.last_debt ? `${row.last_debt.date} (${fmt(row.last_debt.amount)})` : "—"}
                                        </b>
                                    </div>
                                </div>

                                <div className="flex flex-wrap gap-2">
                                    <button onClick={() => setTopupFor(row)} className="flex-1 min-w-[100px] px-3 py-2 rounded-lg bg-emerald-700 text-white text-xs font-bold hover:bg-emerald-800 transition-colors" data-testid={`adacc-topup-btn-${row.id}`}>
                                        <Plus size={12} className="inline ml-1" /> تعبئة
                                    </button>
                                    <button onClick={() => setSpendFor(row)} className="flex-1 min-w-[100px] px-3 py-2 rounded-lg bg-rose-700 text-white text-xs font-bold hover:bg-rose-800 transition-colors" data-testid={`adacc-spend-btn-${row.id}`}>
                                        <Minus size={12} className="inline ml-1" /> تسجيل صرف
                                    </button>
                                    <button onClick={() => setLedgerFor(row)} className="px-3 py-2 rounded-lg bg-slate-100 text-slate-700 text-xs font-bold hover:bg-slate-200 transition-colors" data-testid={`adacc-ledger-btn-${row.id}`}>
                                        <ListBullets size={12} className="inline ml-1" /> السجل
                                    </button>
                                    <button onClick={() => setOpeningFor(row)} className="px-3 py-2 rounded-lg bg-amber-100 text-amber-800 text-xs font-bold hover:bg-amber-200 transition-colors" data-testid={`adacc-opening-btn-${row.id}`} title="رصيد افتتاحي يدوي">
                                        ⚙️ افتتاحي
                                    </button>
                                    <button
                                        onClick={async () => {
                                            if (!window.confirm(
                                                "سيعيد احتساب المديونية الحالية لهذا الحساب من السجل الفعلي (ad_account_ledger). " +
                                                "هذا يُصلح حالات بقي فيها رقم المديونية على البطاقة قديماً بعد تصحيح المزامنة.\n\nمتابعة؟"
                                            )) return;
                                            try {
                                                const { data } = await api.post(
                                                    `/ad-accounts/${row.id}/recover/recompute-debt-from-ledger`);
                                                toast.success(
                                                    `تم التحديث:\n` +
                                                    `• المديونية: ${fmt(data.previous_open_debt)} → ${fmt(data.new_open_debt)} ر.س (${data.delta >= 0 ? "+" : ""}${fmt(data.delta)})\n` +
                                                    `• الرصيد: ${fmt(data.previous_balance)} → ${fmt(data.new_balance)} ر.س (${data.balance_delta >= 0 ? "+" : ""}${fmt(data.balance_delta)})`,
                                                    { duration: 9000 });
                                                load();
                                            } catch (e) {
                                                toast.error(e?.response?.data?.detail || "فشل");
                                            }
                                        }}
                                        className="px-3 py-2 rounded-lg bg-indigo-100 text-indigo-800 text-xs font-bold hover:bg-indigo-200 transition-colors"
                                        data-testid={`adacc-recompute-btn-${row.id}`}
                                        title="يصلح المديونية على البطاقة إذا كانت لا تطابق السجل (Iter-169)"
                                    >
                                        🔄 إعادة احتساب من السجل
                                    </button>
                                    {allowDelete && (
                                        <button
                                            onClick={() => deleteAccount(row)}
                                            disabled={Number(row.balance || 0) > 0 || Number(row.open_debt || 0) > 0}
                                            className="px-3 py-2 rounded-lg bg-rose-50 text-rose-700 text-xs font-bold hover:bg-rose-100 disabled:opacity-40 disabled:cursor-not-allowed border border-rose-200 transition-colors"
                                            data-testid={`adacc-delete-btn-${row.id}`}
                                            title={
                                                Number(row.balance || 0) > 0 || Number(row.open_debt || 0) > 0
                                                    ? "غير متاح — الرصيد أو المديونية أكبر من 0"
                                                    : "حذف هذا الحساب الإعلاني"
                                            }
                                        >
                                            🗑️ حذف
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            <TopupDialog row={topupFor} banks={banks} open={!!topupFor} onClose={() => setTopupFor(null)} onSaved={load} />
            <SpendDialog row={spendFor} open={!!spendFor} onClose={() => setSpendFor(null)} onSaved={load} />
            <LedgerDialog row={ledgerFor} open={!!ledgerFor} onClose={() => setLedgerFor(null)} onSaved={load} />
            <SyncDialog row={syncFor} open={!!syncFor} onClose={() => setSyncFor(null)} onSaved={load} />
            <CreateDialog open={createOpen} onClose={() => setCreateOpen(false)} onSaved={load} />
            <MigrationDialog open={migrationOpen} onClose={() => setMigrationOpen(false)} onSaved={load} />
            <OpeningDialog row={openingFor} open={!!openingFor} onClose={() => setOpeningFor(null)} onSaved={load} />

            {/* Iter-110 — Diagnose dialog for sync data-source mismatch */}
            {diagnose && (
                <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="adacc-diag-dialog">
                    <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-4xl my-8">
                        <div className="flex items-center justify-between p-5 border-b border-slate-100">
                            <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                                🩺 تشخيص المزامنة
                                <span className="text-xs font-normal text-slate-500">يكشف لماذا حساب لا يجلب الصرف</span>
                            </h2>
                            <button onClick={() => setDiagnose(null)} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                        </div>
                        <div className="p-5 space-y-4">
                            {(diagnose.accounts || []).map((a) => (
                                <div key={a.id} className={`rounded-lg border-2 p-4 ${a.healthy ? "bg-emerald-50/40 border-emerald-200" : "bg-rose-50/40 border-rose-300"}`} data-testid={`adacc-diag-row-${a.id}`}>
                                    <div className="flex items-start justify-between gap-3 mb-2">
                                        <div className="flex items-center gap-2">
                                            <span className="text-lg">{a.healthy ? "✅" : "❌"}</span>
                                            <span className="text-base font-extrabold text-slate-900">{a.name}</span>
                                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-700">{a.ad_provider}</span>
                                        </div>
                                        <div className="text-[11px] text-slate-500" dir="ltr">{a.id}</div>
                                    </div>
                                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] mb-3">
                                        <div className="bg-white/60 p-2 rounded">
                                            <div className="text-slate-500">external_account_id الحالي</div>
                                            <div className="font-mono font-bold text-slate-900 break-all" dir="ltr">{a.external_account_id || <span className="text-rose-600">— غير محدد</span>}</div>
                                        </div>
                                        <div className="bg-white/60 p-2 rounded">
                                            <div className="text-slate-500">الرصيد</div>
                                            <div className="num font-bold text-emerald-700">{fmt(a.balance)}</div>
                                        </div>
                                        <div className="bg-white/60 p-2 rounded">
                                            <div className="text-slate-500">آخر مزامنة</div>
                                            <div className="font-mono text-[10px] font-bold">{a.last_auto_sync_date || "—"}</div>
                                        </div>
                                        <div className="bg-white/60 p-2 rounded">
                                            <div className="text-slate-500">نمط الدين</div>
                                            <div className="font-bold">{a.debt_mode === "auto" ? "تلقائي" : "يدوي"}</div>
                                        </div>
                                    </div>

                                    {/* Per-source status */}
                                    <div className="space-y-2">
                                        {a.per_source_status.map((s, i) => (
                                            <div key={i} className="bg-white/80 border border-slate-200 rounded p-2 text-[11px]">
                                                <div className="flex items-center gap-2 mb-1">
                                                    <span className="font-mono font-bold text-slate-700" dir="ltr">{s.collection}</span>
                                                    <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px]">حقل التمييز: {s.scope_field || "بدون"}</span>
                                                    {s.your_external_id_matches ? (
                                                        <span className="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold text-[10px]">✓ مطابق</span>
                                                    ) : (
                                                        <span className="px-1.5 py-0.5 rounded bg-rose-100 text-rose-800 font-bold text-[10px]">✗ غير مطابق</span>
                                                    )}
                                                    <span className="ml-auto text-slate-500">{s.total_rows_in_source} صفوف</span>
                                                </div>
                                                {s.scope_field && s.available_ids?.length > 0 && (
                                                    <div className="mt-1">
                                                        <span className="text-slate-500">IDs متاحة فعلياً: </span>
                                                        {s.available_ids.map((id) => (
                                                            <span key={id} dir="ltr" className={`inline-block font-mono px-1.5 py-0.5 m-0.5 rounded text-[10px] ${id === a.external_account_id ? "bg-emerald-200 text-emerald-900 font-bold" : "bg-amber-100 text-amber-900"}`}>{id}</span>
                                                        ))}
                                                    </div>
                                                )}
                                                {s.sample_recent?.length > 0 && (
                                                    <div className="mt-1 text-slate-600">
                                                        <span className="text-slate-500">عيّنة آخر بيانات: </span>
                                                        {s.sample_recent.map((r, j) => (
                                                            <span key={j} className="font-mono text-[10px]">{r.date}={fmt(r.spend)} </span>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        ))}
                                    </div>

                                    {/* Problems summary */}
                                    {!a.healthy && (
                                        <div className="mt-3 bg-rose-100 border border-rose-300 rounded p-3 text-xs text-rose-900 space-y-1">
                                            <div className="font-extrabold">🔍 المشكلة:</div>
                                            {a.diagnosis.map((d, i) => (
                                                <div key={i}>• {d}</div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                        <div className="p-5 border-t border-slate-100 flex justify-end">
                            <button onClick={() => setDiagnose(null)} className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-bold" data-testid="adacc-diag-close">إغلاق</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}


// ── Iter-159i — Per-account credit limit + alert threshold panel ────
function CreditLimitPanel({ row, onSaved, fmt }) {
    const [editing, setEditing] = useState(false);
    const [limit, setLimit] = useState(
        row.credit_limit != null ? String(row.credit_limit) : ""
    );
    const [pct, setPct] = useState(
        row.alert_threshold_pct != null ? String(row.alert_threshold_pct) : "80"
    );
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        setLimit(row.credit_limit != null ? String(row.credit_limit) : "");
        setPct(row.alert_threshold_pct != null ? String(row.alert_threshold_pct) : "80");
    }, [row.id, row.credit_limit, row.alert_threshold_pct]);

    const save = async () => {
        const limitNum = limit.trim() === "" ? null : Number(limit);
        const pctNum = Number(pct);
        if (limit.trim() !== "" && (!Number.isFinite(limitNum) || limitNum < 0)) {
            toast.error("حد المديونية يجب أن يكون رقماً موجباً");
            return;
        }
        if (!Number.isFinite(pctNum) || pctNum < 0 || pctNum > 100) {
            toast.error("نسبة الصرف يجب أن تكون بين 0 و 100");
            return;
        }
        setBusy(true);
        try {
            await api.put(`/ad-accounts/${row.id}/credit-limit`, {
                credit_limit: limitNum,
                alert_threshold_pct: pctNum,
            });
            toast.success("تم حفظ إعدادات حد المديونية");
            setEditing(false);
            await onSaved?.();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل الحفظ");
        } finally { setBusy(false); }
    };

    const hasLimit = row.credit_limit != null && Number(row.credit_limit) > 0;
    const usage = hasLimit ? (Number(row.open_debt || 0) / Number(row.credit_limit)) * 100 : 0;
    const threshold = Number(row.alert_threshold_pct ?? 80);
    const isOverThreshold = hasLimit && usage >= threshold;
    const isOverLimit = hasLimit && usage >= 100;

    return (
        <div className="border-t border-slate-100 pt-3" data-testid={`adacc-credit-limit-${row.id}`}>
            <div className="flex items-center justify-between mb-2">
                <div className="text-[11px] font-bold text-slate-700 inline-flex items-center gap-1.5">
                    💳 حد المديونية
                </div>
                {!editing && (
                    <button
                        onClick={() => setEditing(true)}
                        className="text-[10px] text-indigo-700 hover:text-indigo-900 font-bold"
                        data-testid={`adacc-credit-limit-edit-${row.id}`}
                    >
                        {hasLimit ? "تعديل" : "ضبط الحد"}
                    </button>
                )}
            </div>

            {!editing ? (
                hasLimit ? (
                    <div className="space-y-2">
                        <div className="grid grid-cols-2 gap-2 text-[11px]">
                            <div className="bg-slate-50 border border-slate-200 rounded p-2">
                                <div className="text-slate-500">السقف المسموح</div>
                                <div className="num font-extrabold text-slate-900 text-sm">{fmt(row.credit_limit)} ر.س</div>
                            </div>
                            <div className="bg-slate-50 border border-slate-200 rounded p-2">
                                <div className="text-slate-500">نسبة التنبيه</div>
                                <div className="num font-extrabold text-slate-900 text-sm">{threshold.toFixed(0)}%</div>
                            </div>
                        </div>
                        {/* Usage progress bar */}
                        <div className="space-y-1">
                            <div className="flex justify-between text-[10px]">
                                <span className="text-slate-500">الاستهلاك</span>
                                <span className={`font-extrabold ${isOverLimit ? "text-rose-700" : isOverThreshold ? "text-amber-700" : "text-emerald-700"}`}>
                                    {usage.toFixed(0)}%
                                </span>
                            </div>
                            <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                                <div
                                    className={`h-full transition-all ${isOverLimit ? "bg-rose-500" : isOverThreshold ? "bg-amber-500" : "bg-emerald-500"}`}
                                    style={{ width: `${Math.min(usage, 100)}%` }}
                                    data-testid={`adacc-credit-limit-bar-${row.id}`}
                                ></div>
                            </div>
                            {isOverLimit && (
                                <div className="text-[10px] text-rose-700 font-bold mt-1">⚠ تجاوزت الحد!</div>
                            )}
                            {isOverThreshold && !isOverLimit && (
                                <div className="text-[10px] text-amber-700 font-bold mt-1">⚠ على وشك النفاذ — بلغت {usage.toFixed(0)}% من السقف</div>
                            )}
                        </div>
                    </div>
                ) : (
                    <div className="text-[11px] text-slate-400 italic">
                        لم يتم ضبط حد للمديونية لهذا الحساب — اضغط «ضبط الحد» لإضافة سقف وتفعيل التنبيهات المخصّصة.
                    </div>
                )
            ) : (
                <div className="space-y-2 bg-indigo-50/40 border border-indigo-200 rounded-lg p-3">
                    <div>
                        <label className="text-[11px] text-slate-700 font-bold">حد المديونية (ر.س)</label>
                        <input
                            type="number"
                            min="0"
                            step="100"
                            value={limit}
                            onChange={(e) => setLimit(e.target.value)}
                            className="w-full mt-1 px-2 py-1.5 border border-slate-300 rounded text-sm num"
                            placeholder="مثلاً 10000 (اتركه فارغاً = بدون حد)"
                            data-testid={`adacc-credit-limit-input-${row.id}`}
                        />
                    </div>
                    <div>
                        <label className="text-[11px] text-slate-700 font-bold">نسبة الصرف لتفعيل التنبيه (%)</label>
                        <input
                            type="number"
                            min="0"
                            max="100"
                            step="5"
                            value={pct}
                            onChange={(e) => setPct(e.target.value)}
                            className="w-full mt-1 px-2 py-1.5 border border-slate-300 rounded text-sm num"
                            placeholder="80"
                            data-testid={`adacc-credit-threshold-input-${row.id}`}
                        />
                        <div className="text-[10px] text-slate-500 mt-1">
                            عند بلوغ هذه النسبة من الحد سيظهر إشعار «المديونية على وشك النفاذ».
                        </div>
                    </div>
                    <div className="flex gap-2 pt-1">
                        <button
                            onClick={save}
                            disabled={busy}
                            className="flex-1 px-3 py-1.5 bg-indigo-600 text-white text-xs rounded hover:bg-indigo-700 disabled:opacity-50 font-bold"
                            data-testid={`adacc-credit-limit-save-${row.id}`}
                        >
                            {busy ? "جاري الحفظ..." : "💾 حفظ"}
                        </button>
                        <button
                            onClick={() => setEditing(false)}
                            disabled={busy}
                            className="px-3 py-1.5 bg-slate-100 text-slate-700 text-xs rounded hover:bg-slate-200 font-bold"
                            data-testid={`adacc-credit-limit-cancel-${row.id}`}
                        >
                            إلغاء
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}

// ── Iter-159n — Recompute debt button + confirmation dialog ────────
// Iter-160 — Accounting Actions Panel (Settlement / Write-off / Adjustment + Audit Log)
// Replaces the destructive "Reset Debt" / "Recompute Debt" buttons with
// proper double-entry accounting actions. All actions append to the
// general_ledger; nothing is ever deleted.
function AccountingActionsPanel({ row, onDone, fmt }) {
    const [showForm, setShowForm] = useState(false);
    const [kind, setKind] = useState("settlement");
    const [amount, setAmount] = useState("");
    const [reasonCode, setReasonCode] = useState("");
    const [notes, setNotes] = useState("");
    const [reasonCodes, setReasonCodes] = useState([]);
    const [busy, setBusy] = useState(false);
    const [showAudit, setShowAudit] = useState(false);
    const [auditItems, setAuditItems] = useState([]);
    const [ledgerItems, setLedgerItems] = useState([]);

    const KINDS = [
        { value: "settlement", label: "تسوية", color: "emerald",
          desc: "سداد فعلي يخفض المديونية", direction: "reduce_debt" },
        { value: "writeoff",   label: "شطب",   color: "amber",
          desc: "شطب معتمد يخفض المديونية", direction: "reduce_debt" },
        { value: "adjustment", label: "تعديل", color: "sky",
          desc: "قيد تعديل عام (موجب أو سالب)", direction: "reduce_debt" },
    ];

    const loadReasonCodes = async () => {
        if (reasonCodes.length) return;
        try {
            const { data } = await api.get("/ledger/reason-codes");
            setReasonCodes(data);
            if (data.length) setReasonCode(data[0].code);
        } catch (e) { /* ignore */ }
    };

    const loadAuditLog = async () => {
        try {
            const [aud, led] = await Promise.all([
                api.get(`/ad-accounts/${row.id}/audit-log?limit=50`),
                api.get(`/ad-accounts/${row.id}/adjustment-entries?limit=50`),
            ]);
            setAuditItems(aud.data?.items || []);
            setLedgerItems(led.data?.items || []);
        } catch (e) {
            toast.error("فشل تحميل سجل التدقيق");
        }
    };

    const openForm = async (k) => {
        setKind(k);
        await loadReasonCodes();
        setShowForm(true);
        setAmount("");
        setNotes("");
    };

    const submit = async (direction = "reduce_debt") => {
        const amt = Number(amount);
        if (!amt || amt <= 0) {
            toast.error("أدخل مبلغاً صحيحاً");
            return;
        }
        if (!reasonCode) {
            toast.error("اختر سبب العملية");
            return;
        }
        setBusy(true);
        try {
            const { data } = await api.post(
                `/ad-accounts/${row.id}/adjustments`,
                { kind, amount: amt, direction, reason_code: reasonCode, notes },
            );
            toast.success(
                `تم تسجيل ${KINDS.find(x => x.value === kind)?.label} بقيمة ${fmt(amt)} ر.س — المديونية الجديدة: ${fmt(data.account?.open_debt)}`,
                { duration: 7000 },
            );
            setShowForm(false);
            await onDone?.();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشلت العملية");
        } finally {
            setBusy(false);
        }
    };

    const reverseEntry = async (entryId, entryNo) => {
        const reasonCodeRev = window.prompt(
            "سبب العكس (اختر من: actual_payment / data_entry_error / duplicate_entry / accounting_settle / other):",
            "data_entry_error",
        );
        if (!reasonCodeRev) return;
        const note = window.prompt("ملاحظات إضافية (اختياري):", "") || "";
        if (!window.confirm(
            `سيتم إنشاء قيد عكسي للقيد رقم ${entryNo}\n` +
            "القيد الأصلي سيُحفظ تاريخياً مع status=reversed.\n\nمتابعة؟",
        )) return;
        try {
            await api.post(
                `/ledger/entries/${entryId}/reverse`,
                { reason_code: reasonCodeRev, notes: note },
            );
            toast.success("تم إنشاء القيد العكسي بنجاح");
            await loadAuditLog();
            await onDone?.();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل عكس القيد");
        }
    };

    return (
        <div className="border-t border-slate-100 pt-3 space-y-2"
             data-testid={`adacc-accounting-${row.id}`}>
            <div className="flex items-center justify-between gap-2">
                <div className="text-[11px] font-bold text-slate-700">
                    📒 العمليات المحاسبية
                </div>
                <button
                    onClick={async () => {
                        if (!showAudit) await loadAuditLog();
                        setShowAudit(!showAudit);
                    }}
                    className="text-[10px] text-slate-600 hover:text-slate-900 underline"
                    data-testid={`adacc-audit-toggle-${row.id}`}
                >
                    {showAudit ? "إخفاء السجل" : "📋 سجل التدقيق"}
                </button>
            </div>

            {(row.adjustments_total_debit > 0 || row.adjustments_total_credit > 0) && (
                <div className="bg-slate-50 border border-slate-200 rounded-lg p-2 text-[10px]">
                    <div className="flex justify-between">
                        <span className="text-slate-600">إجمالي التسويات المخفّضة:</span>
                        <span className="num font-bold text-emerald-700">{fmt(row.adjustments_total_debit)}</span>
                    </div>
                    {row.adjustments_total_credit > 0 && (
                        <div className="flex justify-between mt-0.5">
                            <span className="text-slate-600">إجمالي التعديلات المضافة:</span>
                            <span className="num font-bold text-rose-700">{fmt(row.adjustments_total_credit)}</span>
                        </div>
                    )}
                </div>
            )}

            <div className="flex flex-wrap gap-1.5">
                {KINDS.map(k => (
                    <button key={k.value}
                        onClick={() => openForm(k.value)}
                        className={`text-[11px] bg-${k.color}-50 hover:bg-${k.color}-100 text-${k.color}-800 border border-${k.color}-200 px-2.5 py-1 rounded font-bold flex-1 min-w-[80px]`}
                        title={k.desc}
                        data-testid={`adacc-${k.value}-btn-${row.id}`}
                    >
                        {k.value === "settlement" && "✓ تسوية"}
                        {k.value === "writeoff" && "✂ شطب"}
                        {k.value === "adjustment" && "± تعديل"}
                    </button>
                ))}
            </div>

            {showForm && (
                <div className="bg-slate-50 border border-slate-300 rounded-lg p-3 space-y-2"
                     data-testid={`adacc-adjustment-form-${row.id}`}>
                    <div className="text-[11px] font-bold text-slate-700">
                        {kind === "settlement" && "✓ تسوية مديونية"}
                        {kind === "writeoff" && "✂ شطب رصيد معتمد"}
                        {kind === "adjustment" && "± قيد تعديل"}
                    </div>
                    <div className="text-[10px] text-slate-500">
                        لن يتم حذف أي بيانات. سيُسجَّل قيد محاسبي جديد مع الحفاظ على السجل التاريخي.
                    </div>
                    <input type="number" step="0.01" placeholder="المبلغ"
                        value={amount} onChange={e => setAmount(e.target.value)}
                        className="w-full px-2 py-1.5 border border-slate-300 rounded text-xs"
                        data-testid={`adacc-adj-amount-${row.id}`}
                    />
                    <select value={reasonCode} onChange={e => setReasonCode(e.target.value)}
                        className="w-full px-2 py-1.5 border border-slate-300 rounded text-xs"
                        data-testid={`adacc-adj-reason-${row.id}`}
                    >
                        <option value="">— اختر السبب —</option>
                        {reasonCodes.map(rc => (
                            <option key={rc.code} value={rc.code}>{rc.label}</option>
                        ))}
                    </select>
                    <textarea placeholder="ملاحظات (اختياري)" rows={2}
                        value={notes} onChange={e => setNotes(e.target.value)}
                        className="w-full px-2 py-1.5 border border-slate-300 rounded text-xs"
                        data-testid={`adacc-adj-notes-${row.id}`}
                    />
                    <div className="flex gap-1.5">
                        {kind === "adjustment" && (
                            <>
                                <button onClick={() => submit("reduce_debt")}
                                    disabled={busy}
                                    className="flex-1 px-2 py-1.5 bg-emerald-600 text-white text-[11px] rounded font-bold disabled:opacity-50">
                                    تخفيض المديونية
                                </button>
                                <button onClick={() => submit("increase_debt")}
                                    disabled={busy}
                                    className="flex-1 px-2 py-1.5 bg-rose-600 text-white text-[11px] rounded font-bold disabled:opacity-50">
                                    زيادة المديونية
                                </button>
                            </>
                        )}
                        {kind !== "adjustment" && (
                            <button onClick={() => submit("reduce_debt")}
                                disabled={busy}
                                className="flex-1 px-2 py-1.5 bg-emerald-600 text-white text-[11px] rounded font-bold disabled:opacity-50"
                                data-testid={`adacc-adj-submit-${row.id}`}>
                                {busy ? "جاري..." : "اعتماد القيد"}
                            </button>
                        )}
                        <button onClick={() => setShowForm(false)}
                            className="px-2 py-1.5 bg-slate-200 text-slate-700 text-[11px] rounded">
                            إلغاء
                        </button>
                    </div>
                </div>
            )}

            {showAudit && (
                <div className="bg-white border border-slate-300 rounded-lg p-3 max-h-80 overflow-y-auto space-y-2"
                     data-testid={`adacc-audit-log-${row.id}`}>
                    <div className="text-[11px] font-bold text-slate-800 mb-2">
                        📒 قيود الـ Ledger ({ledgerItems.length})
                    </div>
                    {ledgerItems.length === 0 && (
                        <div className="text-[10px] text-slate-400 text-center py-3">
                            لا توجد قيود محاسبية حتى الآن
                        </div>
                    )}
                    {ledgerItems.map(le => (
                        <div key={le.id}
                             className={`text-[10px] border rounded p-2 space-y-0.5 ${
                                le.status === "reversed" ? "bg-slate-50 border-slate-200 opacity-60" :
                                le.entry_type === "reversal" ? "bg-amber-50 border-amber-200" :
                                "bg-emerald-50 border-emerald-200"
                             }`}>
                            <div className="flex items-center justify-between">
                                <div className="font-bold text-slate-800">
                                    #{le.entry_no} · {le.entry_type === "settlement" ? "تسوية" :
                                                          le.entry_type === "writeoff" ? "شطب" :
                                                          le.entry_type === "adjustment" ? "تعديل" :
                                                          le.entry_type === "reversal" ? "قيد عكسي" :
                                                          le.entry_type}
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <span className={`num font-bold ${le.side === "debit" ? "text-emerald-700" : "text-rose-700"}`}>
                                        {le.side === "debit" ? "+" : "−"} {fmt(le.amount)}
                                    </span>
                                    {le.status === "posted" && le.entry_type !== "reversal" && (
                                        <button onClick={() => reverseEntry(le.id, le.entry_no)}
                                            className="text-[10px] text-amber-700 hover:text-amber-900 underline"
                                            data-testid={`adacc-reverse-${le.entry_no}`}>
                                            عكس
                                        </button>
                                    )}
                                    {le.status === "reversed" && (
                                        <span className="text-[9px] text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
                                            معكوس
                                        </span>
                                    )}
                                </div>
                            </div>
                            <div className="text-slate-600">
                                {le.reason_code} {le.notes && `· ${le.notes}`}
                            </div>
                            <div className="text-[9px] text-slate-400">
                                {le.created_at?.slice(0, 19).replace("T", " ")}
                            </div>
                        </div>
                    ))}
                    <div className="border-t border-slate-200 pt-2 mt-2">
                        <div className="text-[11px] font-bold text-slate-800 mb-2">
                            🔍 سجل التدقيق ({auditItems.length})
                        </div>
                        {auditItems.slice(0, 20).map(au => (
                            <div key={au.id} className="text-[9px] text-slate-600 border-b border-slate-100 py-1">
                                <span className="font-bold">{au.action}</span>
                                {au.reason_code && ` · ${au.reason_code}`}
                                {au.notes && ` · ${au.notes}`}
                                <div className="text-slate-400">{au.timestamp?.slice(0, 19).replace("T", " ")}</div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}

