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


const fmt = (v) =>
    Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const todayIso = () => new Date().toISOString().slice(0, 10);

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
    const monthStart = () => new Date().toISOString().slice(0, 8) + "01";
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
                            <table className="w-full text-sm">
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
    const monthStart = () => new Date().toISOString().slice(0, 8) + "01";
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
                                <li>كل ترحيل ينشئ سجلاً منفصلاً في الـ ledger يمكنك مراجعته أو إلغاؤه يدوياً.</li>
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
                            <table className="w-full text-sm">
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
                        <div className="overflow-x-auto border border-slate-200 rounded-lg">
                            <table className="w-full text-sm">
                                <thead className="bg-slate-50 text-slate-600 text-[11px]">
                                    <tr>
                                        <th className="text-right p-2 font-bold">الحساب</th>
                                        <th className="text-right p-2 font-bold">الحالة</th>
                                        <th className="text-right p-2 font-bold">سطور مُرحّلة</th>
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
                            <table className="w-full text-xs">
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
            const [adRes, accRes, settingsRes] = await Promise.all([
                api.get("/ad-accounts"),
                // Iter-110 fix — endpoint expects `account_type=` (not `type=`)
                // and returns a plain list, not `{items: []}`. Filter to
                // visible active bank accounts only.
                api.get("/accounts?account_type=bank"),
                api.get("/settings"),
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
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التحميل");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

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
            <div className="text-[11px] text-slate-500 -mt-3 mb-1">
                💡 المزامنة المجدولة تعمل تلقائياً كل يوم الساعة <b>11:55 مساءً</b> لكل الحسابات المدعومة (Snap / TikTok / Meta).
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
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {items.map((row) => (
                        <div key={row.id} className="bg-white border border-slate-200 rounded-xl p-5 space-y-4" data-testid={`adacc-card-${row.id}`}>
                            <div className="flex items-start justify-between gap-2">
                                <div>
                                    <div className="flex items-center gap-2">
                                        <h3 className="font-extrabold text-base text-slate-900">{row.name}</h3>
                                        <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-slate-100 text-slate-700">
                                            {PROVIDER_LABEL[row.ad_provider] || row.ad_provider}
                                        </span>
                                    </div>
                                    {row.notes && <div className="text-xs text-slate-500 mt-1">{row.notes}</div>}
                                    {row.external_account_id && (
                                        <div className="text-[11px] text-slate-500 mt-1 font-mono" dir="ltr">
                                            🔗 {row.external_account_id}
                                        </div>
                                    )}
                                </div>
                                <button onClick={() => toggleMode(row)} className={`px-2 py-1 rounded-lg text-[11px] font-bold border ${row.debt_mode === "auto" ? "bg-violet-50 text-violet-800 border-violet-200" : "bg-amber-50 text-amber-800 border-amber-200"}`} title="غيِّر وضع احتساب المديونية" data-testid={`adacc-mode-toggle-${row.id}`}>
                                    {row.debt_mode === "auto" ? "تلقائي" : "يدوي"}
                                </button>
                            </div>

                            <div className="grid grid-cols-3 gap-2">
                                <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 text-center">
                                    <div className="text-[10px] font-bold text-emerald-800">الرصيد</div>
                                    <div className="num text-base font-extrabold text-emerald-900 mt-1">{fmt(row.balance)}</div>
                                </div>
                                <div className="bg-rose-50 border border-rose-200 rounded-lg p-3 text-center">
                                    <div className="text-[10px] font-bold text-rose-800">المديونية</div>
                                    <div className="num text-base font-extrabold text-rose-900 mt-1">{fmt(row.open_debt)}</div>
                                </div>
                                <div className="bg-violet-50 border border-violet-200 rounded-lg p-3 text-center">
                                    <div className="text-[10px] font-bold text-violet-800">الصرف</div>
                                    <div className="num text-base font-extrabold text-violet-900 mt-1">{fmt(row.total_spend)}</div>
                                </div>
                            </div>

                            <div className="space-y-1 text-[11px] text-slate-600 border-t border-slate-100 pt-3">
                                <div className="flex items-center gap-1.5">
                                    <Clock size={11} className="text-slate-400" /> آخر تعبئة:
                                    <b className={row.last_topup ? "text-slate-900" : "text-slate-400"}>
                                        {row.last_topup ? `${row.last_topup.date} (${fmt(row.last_topup.amount)})` : "—"}
                                    </b>
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <Clock size={11} className="text-slate-400" /> آخر صرف:
                                    <b className={row.last_spend ? "text-slate-900" : "text-slate-400"}>
                                        {row.last_spend ? `${row.last_spend.date} (${fmt(row.last_spend.amount)})` : "—"}
                                    </b>
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <Clock size={11} className="text-slate-400" /> آخر مديونية:
                                    <b className={row.last_debt ? "text-rose-900" : "text-slate-400"}>
                                        {row.last_debt ? `${row.last_debt.date} (${fmt(row.last_debt.amount)})` : "—"}
                                    </b>
                                </div>
                            </div>

                            <div className="flex flex-wrap gap-2 pt-2">
                                <button onClick={() => setTopupFor(row)} className="flex-1 min-w-[100px] px-3 py-2 rounded-lg bg-emerald-700 text-white text-xs font-bold hover:bg-emerald-800" data-testid={`adacc-topup-btn-${row.id}`}>
                                    <Plus size={12} className="inline ml-1" /> تعبئة
                                </button>
                                <button onClick={() => setSpendFor(row)} className="flex-1 min-w-[100px] px-3 py-2 rounded-lg bg-rose-700 text-white text-xs font-bold hover:bg-rose-800" data-testid={`adacc-spend-btn-${row.id}`}>
                                    <Minus size={12} className="inline ml-1" /> تسجيل صرف
                                </button>
                                <button onClick={() => setLedgerFor(row)} className="px-3 py-2 rounded-lg bg-slate-100 text-slate-700 text-xs font-bold hover:bg-slate-200" data-testid={`adacc-ledger-btn-${row.id}`}>
                                    <ListBullets size={12} className="inline ml-1" /> السجل
                                </button>
                                <button onClick={() => setOpeningFor(row)} className="px-3 py-2 rounded-lg bg-amber-100 text-amber-800 text-xs font-bold hover:bg-amber-200" data-testid={`adacc-opening-btn-${row.id}`} title="رصيد افتتاحي يدوي">
                                    ⚙️ افتتاحي
                                </button>
                                {allowDelete && (
                                    <button
                                        onClick={() => deleteAccount(row)}
                                        disabled={Number(row.balance || 0) > 0 || Number(row.open_debt || 0) > 0}
                                        className="px-3 py-2 rounded-lg bg-rose-50 text-rose-700 text-xs font-bold hover:bg-rose-100 disabled:opacity-40 disabled:cursor-not-allowed border border-rose-200"
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
