/**
 * Iter-251 · Phase 1 — Bank Transfer Review page.
 *
 * Independent review queue for every incoming bank transfer that
 * originates from a wallet / platform / customer.  Until a Reviewer
 * confirms here, the money does NOT hit the bank's GL balance.
 *
 * MVP scope (Phase 1): manual entries + manual confirm/reject only.
 * Future phases will plug Salla / Tamara / Tabby / Imkan / shipping-COD
 * webhooks into this same queue.
 */
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const fmt = (v) =>
    v == null
        ? "—"
        : Number(v).toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });

const fmtDate = (v) => {
    if (!v) return "—";
    try { return new Date(v).toLocaleDateString("en-CA"); }
    catch (_e) { return v; }
};

const SOURCE_LABELS = {
    salla:             { ar: "سلة",            color: "emerald" },
    tamara:            { ar: "تمارا",          color: "amber"   },
    tabby:             { ar: "تابي",           color: "sky"     },
    imkan:             { ar: "إمكان",          color: "violet"  },
    shipping_cod:      { ar: "شركة شحن (COD)", color: "indigo"  },
    customer_transfer: { ar: "عميل",           color: "slate"   },
    manual:            { ar: "يدوي",           color: "slate"   },
};

const STATUS_LABELS = {
    pending: {
        ar: "بانتظار التأكيد", color: "amber",
        icon: "⏳",
    },
    confirmed: {
        ar: "مؤكد", color: "emerald",
        icon: "✓",
    },
    confirmed_with_difference: {
        ar: "مؤكد مع فرق", color: "indigo",
        icon: "⚖️",
    },
    rejected: {
        ar: "مرفوض", color: "rose",
        icon: "✗",
    },
    legacy_confirmed: {
        ar: "مؤكد سابقاً (قديم)", color: "slate",
        icon: "📦",
    },
    missing_target_bank: {
        ar: "بدون بنك مستلم", color: "rose",
        icon: "🏦",
    },
};

const STATUS_CHIP_CLASSES = {
    amber:   "bg-amber-100 text-amber-800 border-amber-300",
    emerald: "bg-emerald-100 text-emerald-800 border-emerald-300",
    indigo:  "bg-indigo-100 text-indigo-800 border-indigo-300",
    rose:    "bg-rose-100 text-rose-800 border-rose-300",
    slate:   "bg-slate-100 text-slate-700 border-slate-300",
    sky:     "bg-sky-100 text-sky-800 border-sky-300",
    violet:  "bg-violet-100 text-violet-800 border-violet-300",
};

function StatusChip({ status }) {
    const m = STATUS_LABELS[status] || STATUS_LABELS.pending;
    return (
        <span
            className={`text-[10px] px-1.5 py-0.5 rounded border font-bold whitespace-nowrap ${STATUS_CHIP_CLASSES[m.color]}`}
            data-testid={`btr-status-${status}`}
        >
            {m.icon} {m.ar}
        </span>
    );
}

function SourceChip({ source }) {
    const m = SOURCE_LABELS[source] || SOURCE_LABELS.manual;
    return (
        <span
            className={`text-[10px] px-1.5 py-0.5 rounded border font-bold whitespace-nowrap ${STATUS_CHIP_CLASSES[m.color]}`}
            data-testid={`btr-source-${source}`}
        >
            {m.ar}
        </span>
    );
}

function SummaryCards({ summary }) {
    const by = summary?.by_status || {};
    const cards = [
        { key: "pending",                   label: "بانتظار التأكيد", color: "amber",   icon: "⏳" },
        { key: "confirmed",                 label: "مؤكد",            color: "emerald", icon: "✓" },
        { key: "confirmed_with_difference", label: "مؤكد مع فرق",     color: "indigo",  icon: "⚖️" },
        { key: "rejected",                  label: "مرفوض",           color: "rose",    icon: "✗" },
    ];
    return (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            {cards.map(({ key, label, color, icon }) => {
                const row = by[key] || { count: 0, expected_total: 0,
                                          received_total: 0 };
                return (
                    <div
                        key={key}
                        className={`rounded-xl border p-3 ${STATUS_CHIP_CLASSES[color]}`}
                        data-testid={`btr-card-${key}`}
                    >
                        <div className="text-xs font-bold mb-1">{icon} {label}</div>
                        <div className="text-2xl font-extrabold font-mono">{row.count}</div>
                        <div className="text-[11px] font-mono mt-1 opacity-80">
                            متوقع: {fmt(row.expected_total)} ر.س
                        </div>
                        {row.received_total !== 0 && (
                            <div className="text-[11px] font-mono opacity-80">
                                واصل: {fmt(row.received_total)} ر.س
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
}

// ─────────────────────────── Action Modals ──────────────────────────

function ConfirmModal({ open, review, onClose, onDone }) {
    const [mode, setMode] = useState("exact");  // exact | diff
    const [received, setReceived] = useState("");
    const [note, setNote] = useState("");
    const [bankRef, setBankRef] = useState("");
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (open && review) {
            setMode("exact");
            setReceived(String(review.expected_amount || ""));
            setNote("");
            setBankRef(review.bank_reference || "");
        }
    }, [open, review]);

    if (!open || !review) return null;

    async function submit() {
        setBusy(true);
        try {
            const url = mode === "exact"
                ? `/bank-transfer-review/${review.id}/confirm`
                : `/bank-transfer-review/${review.id}/confirm-with-difference`;
            const body = mode === "exact"
                ? { review_note: note || null, bank_reference: bankRef || null }
                : {
                    received_amount: Number(received),
                    review_note: note || null,
                    bank_reference: bankRef || null,
                };
            const { data } = await api.post(url, body);
            toast.success("تم تأكيد وصول المبلغ ✓");
            onDone(data);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل التأكيد");
        } finally {
            setBusy(false);
        }
    }

    const diff = Number(received || 0) - Number(review.expected_amount || 0);

    return (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"
             onClick={onClose}
             data-testid="btr-confirm-modal">
            <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-5"
                 onClick={(e) => e.stopPropagation()}>
                <div className="flex items-center justify-between mb-3">
                    <h3 className="text-base font-extrabold">تأكيد وصول التحويل</h3>
                    <button onClick={onClose} className="text-slate-400 hover:text-slate-700">✕</button>
                </div>
                <div className="text-xs bg-slate-50 rounded-lg p-3 mb-3 space-y-1">
                    <div><b>المصدر:</b> {review.source_account_name}</div>
                    <div><b>البنك:</b> {review.target_bank_name}</div>
                    <div><b>المبلغ المتوقع:</b> <span className="font-mono font-extrabold text-emerald-700">{fmt(review.expected_amount)}</span> ر.س</div>
                </div>

                <div className="flex gap-2 mb-3">
                    <button
                        type="button"
                        onClick={() => { setMode("exact"); setReceived(String(review.expected_amount)); }}
                        className={`flex-1 px-3 py-2 rounded-lg text-xs font-bold border ${mode==="exact" ? "bg-emerald-600 text-white border-emerald-700" : "bg-white text-slate-700 border-slate-300"}`}
                        data-testid="btr-modal-mode-exact"
                    >
                        ✓ تأكيد كامل (مطابق)
                    </button>
                    <button
                        type="button"
                        onClick={() => { setMode("diff"); }}
                        className={`flex-1 px-3 py-2 rounded-lg text-xs font-bold border ${mode==="diff" ? "bg-indigo-600 text-white border-indigo-700" : "bg-white text-slate-700 border-slate-300"}`}
                        data-testid="btr-modal-mode-diff"
                    >
                        ⚖️ تأكيد مع فرق
                    </button>
                </div>

                {mode === "diff" && (
                    <div className="mb-3">
                        <label className="text-xs font-bold text-slate-700 block mb-1">
                            المبلغ الواصل فعلياً <span className="text-rose-600">*</span>
                        </label>
                        <input
                            type="number"
                            step="0.01"
                            min="0"
                            value={received}
                            onChange={(e) => setReceived(e.target.value)}
                            className="w-full border rounded-lg px-3 py-2 text-sm font-mono"
                            data-testid="btr-modal-received-input"
                        />
                        {Number(received) > 0 && (
                            <div className={`text-[11px] mt-1 font-mono ${diff < 0 ? "text-rose-700" : "text-emerald-700"}`}>
                                الفرق: {fmt(diff)} ر.س
                                {diff < 0 && ` (سيبقى ${fmt(Math.abs(diff))} في رصيد المصدر)`}
                            </div>
                        )}
                    </div>
                )}

                <div className="mb-3">
                    <label className="text-xs font-bold text-slate-700 block mb-1">رقم مرجع البنك (اختياري)</label>
                    <input
                        value={bankRef}
                        onChange={(e) => setBankRef(e.target.value)}
                        placeholder="—"
                        className="w-full border rounded-lg px-3 py-2 text-xs"
                        data-testid="btr-modal-bankref-input"
                    />
                </div>

                <div className="mb-4">
                    <label className="text-xs font-bold text-slate-700 block mb-1">ملاحظة (اختياري)</label>
                    <textarea
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        rows={2}
                        className="w-full border rounded-lg px-3 py-2 text-xs"
                        data-testid="btr-modal-note-input"
                    />
                </div>

                <div className="flex gap-2">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 rounded-lg border border-slate-300 text-xs font-bold text-slate-700"
                    >
                        إلغاء
                    </button>
                    <button
                        onClick={submit}
                        disabled={busy || (mode === "diff" && !(Number(received) > 0))}
                        className="flex-1 px-4 py-2 rounded-lg bg-emerald-600 text-white text-xs font-extrabold disabled:opacity-40"
                        data-testid="btr-modal-submit"
                    >
                        {busy ? "جارٍ التأكيد..." : "تأكيد الوصول"}
                    </button>
                </div>
            </div>
        </div>
    );
}

function RejectModal({ open, review, onClose, onDone }) {
    const [note, setNote] = useState("");
    const [busy, setBusy] = useState(false);
    useEffect(() => { if (open) setNote(""); }, [open]);
    if (!open || !review) return null;
    async function submit() {
        if (!note.trim()) {
            return toast.error("سبب الرفض مطلوب");
        }
        setBusy(true);
        try {
            const { data } = await api.post(
                `/bank-transfer-review/${review.id}/reject`,
                { review_note: note.trim() },
            );
            toast.success("تم الرفض");
            onDone(data);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل الرفض");
        } finally { setBusy(false); }
    }
    return (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"
             onClick={onClose} data-testid="btr-reject-modal">
            <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-5"
                 onClick={(e) => e.stopPropagation()}>
                <h3 className="text-base font-extrabold mb-3">رفض/تعليق التحويل</h3>
                <p className="text-xs text-slate-600 mb-3">
                    اشرح سبب الرفض. سيبقى السجل في النظام للتتبع لكن لن يدخل GL.
                </p>
                <textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={3}
                    placeholder="سبب الرفض..."
                    className="w-full border rounded-lg px-3 py-2 text-xs mb-3"
                    data-testid="btr-reject-note-input"
                />
                <div className="flex gap-2">
                    <button onClick={onClose}
                            className="px-4 py-2 rounded-lg border border-slate-300 text-xs font-bold">إلغاء</button>
                    <button onClick={submit} disabled={busy}
                            className="flex-1 px-4 py-2 rounded-lg bg-rose-600 text-white text-xs font-extrabold disabled:opacity-40"
                            data-testid="btr-reject-submit">
                        {busy ? "..." : "تأكيد الرفض"}
                    </button>
                </div>
            </div>
        </div>
    );
}

function CreateModal({ open, onClose, onDone }) {
    const [form, setForm] = useState({
        source_type: "manual",
        source_id: "",
        source_account_name: "",
        target_bank_id: "",
        target_bank_name: "",
        expected_amount: "",
        transfer_date: new Date().toISOString().slice(0, 10),
        internal_reference: "",
        review_note: "",
    });
    const [busy, setBusy] = useState(false);
    useEffect(() => {
        if (open) setForm({
            source_type: "manual", source_id: "",
            source_account_name: "", target_bank_id: "",
            target_bank_name: "", expected_amount: "",
            transfer_date: new Date().toISOString().slice(0, 10),
            internal_reference: "", review_note: "",
        });
    }, [open]);
    if (!open) return null;
    const upd = (k, v) => setForm((s) => ({ ...s, [k]: v }));

    async function submit() {
        if (!form.source_id || !form.source_account_name
            || !form.target_bank_id || !form.target_bank_name
            || !(Number(form.expected_amount) > 0)
            || !form.transfer_date) {
            return toast.error("الحقول الأساسية مطلوبة (مع المبلغ > 0)");
        }
        setBusy(true);
        try {
            const { data } = await api.post("/bank-transfer-review", {
                ...form,
                expected_amount: Number(form.expected_amount),
            });
            toast.success("تم إنشاء سجل المراجعة");
            onDone(data);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل الإنشاء");
        } finally { setBusy(false); }
    }
    return (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"
             onClick={onClose} data-testid="btr-create-modal">
            <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-5 max-h-[90vh] overflow-y-auto"
                 onClick={(e) => e.stopPropagation()}>
                <h3 className="text-base font-extrabold mb-3">➕ إضافة سجل مراجعة يدوي</h3>
                <p className="text-xs text-slate-500 mb-3">
                    للحركات اليدوية/التجريبية. الـ webhooks الإنتاجية ستملأ هذي الصفحة تلقائياً في المرحلة 2.
                </p>
                <div className="grid grid-cols-2 gap-3 text-xs">
                    <div>
                        <label className="font-bold block mb-1">نوع المصدر</label>
                        <select value={form.source_type}
                                onChange={(e) => upd("source_type", e.target.value)}
                                className="w-full border rounded-lg px-2 py-2"
                                data-testid="btr-create-source-type">
                            {Object.entries(SOURCE_LABELS).map(([k, v]) =>
                                <option key={k} value={k}>{v.ar}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className="font-bold block mb-1">معرّف المصدر *</label>
                        <input value={form.source_id}
                               onChange={(e) => upd("source_id", e.target.value)}
                               placeholder="مثال: SETTLEMENT-001"
                               className="w-full border rounded-lg px-2 py-2"
                               data-testid="btr-create-source-id" />
                    </div>
                    <div className="col-span-2">
                        <label className="font-bold block mb-1">اسم الحساب المصدر *</label>
                        <input value={form.source_account_name}
                               onChange={(e) => upd("source_account_name", e.target.value)}
                               placeholder="محفظة سلة"
                               className="w-full border rounded-lg px-2 py-2"
                               data-testid="btr-create-source-name" />
                    </div>
                    <div>
                        <label className="font-bold block mb-1">معرّف البنك *</label>
                        <input value={form.target_bank_id}
                               onChange={(e) => upd("target_bank_id", e.target.value)}
                               placeholder="BANK-INMA-1"
                               className="w-full border rounded-lg px-2 py-2"
                               data-testid="btr-create-bank-id" />
                    </div>
                    <div>
                        <label className="font-bold block mb-1">اسم البنك *</label>
                        <input value={form.target_bank_name}
                               onChange={(e) => upd("target_bank_name", e.target.value)}
                               placeholder="بنك الإنماء"
                               className="w-full border rounded-lg px-2 py-2"
                               data-testid="btr-create-bank-name" />
                    </div>
                    <div>
                        <label className="font-bold block mb-1">المبلغ المتوقع *</label>
                        <input type="number" step="0.01" min="0"
                               value={form.expected_amount}
                               onChange={(e) => upd("expected_amount", e.target.value)}
                               className="w-full border rounded-lg px-2 py-2 font-mono"
                               data-testid="btr-create-amount" />
                    </div>
                    <div>
                        <label className="font-bold block mb-1">تاريخ التحويل *</label>
                        <input type="date" value={form.transfer_date}
                               onChange={(e) => upd("transfer_date", e.target.value)}
                               className="w-full border rounded-lg px-2 py-2 font-mono"
                               data-testid="btr-create-date" />
                    </div>
                    <div className="col-span-2">
                        <label className="font-bold block mb-1">المرجع الداخلي (اختياري)</label>
                        <input value={form.internal_reference}
                               onChange={(e) => upd("internal_reference", e.target.value)}
                               className="w-full border rounded-lg px-2 py-2"
                               data-testid="btr-create-ref" />
                    </div>
                    <div className="col-span-2">
                        <label className="font-bold block mb-1">ملاحظات (اختياري)</label>
                        <textarea rows={2} value={form.review_note}
                                  onChange={(e) => upd("review_note", e.target.value)}
                                  className="w-full border rounded-lg px-2 py-2" />
                    </div>
                </div>
                <div className="flex gap-2 mt-4">
                    <button onClick={onClose}
                            className="px-4 py-2 rounded-lg border border-slate-300 text-xs font-bold">إلغاء</button>
                    <button onClick={submit} disabled={busy}
                            className="flex-1 px-4 py-2 rounded-lg bg-emerald-600 text-white text-xs font-extrabold disabled:opacity-40"
                            data-testid="btr-create-submit">
                        {busy ? "..." : "إنشاء السجل"}
                    </button>
                </div>
            </div>
        </div>
    );
}

function AssignBankModal({ open, review, banks, onClose, onDone }) {
    const [bankId, setBankId] = useState("");
    const [note, setNote] = useState("");
    const [busy, setBusy] = useState(false);
    useEffect(() => {
        if (open) { setBankId(""); setNote(""); }
    }, [open]);
    if (!open || !review) return null;
    const bank = banks.find((b) => b.id === bankId);
    async function submit() {
        if (!bankId || !bank) {
            return toast.error("اختر بنكاً");
        }
        setBusy(true);
        try {
            const { data } = await api.post(
                `/bank-transfer-review/${review.id}/assign-bank`,
                {
                    target_bank_id:   bank.id,
                    target_bank_name: bank.name,
                    review_note: note || null,
                },
            );
            toast.success("تم تعيين البنك ✓");
            onDone(data);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل التعيين");
        } finally { setBusy(false); }
    }
    return (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"
             onClick={onClose} data-testid="btr-assignbank-modal">
            <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-5"
                 onClick={(e) => e.stopPropagation()}>
                <h3 className="text-base font-extrabold mb-2">
                    🏦 تعيين البنك المستلم
                </h3>
                <div className="text-xs bg-amber-50 border border-amber-200 rounded-lg p-2 mb-3">
                    هذا السجل وارد من <b>{SOURCE_LABELS[review.source_type]?.ar || review.source_type}</b>
                    {" "}بدون تحديد البنك المستلم. اختر البنك قبل المتابعة للتأكيد.
                </div>
                <label className="text-xs font-bold block mb-1">البنك المستلم *</label>
                <select
                    value={bankId}
                    onChange={(e) => setBankId(e.target.value)}
                    className="w-full border rounded-lg px-3 py-2 text-sm mb-3"
                    data-testid="btr-assignbank-select"
                >
                    <option value="">— اختر —</option>
                    {banks.map((b) =>
                        <option key={b.id} value={b.id}>{b.name}</option>)}
                </select>
                <label className="text-xs font-bold block mb-1">ملاحظة (اختياري)</label>
                <textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={2}
                    className="w-full border rounded-lg px-3 py-2 text-xs mb-4"
                />
                <div className="flex gap-2">
                    <button onClick={onClose}
                            className="px-4 py-2 rounded-lg border border-slate-300 text-xs font-bold">إلغاء</button>
                    <button onClick={submit} disabled={busy || !bankId}
                            className="flex-1 px-4 py-2 rounded-lg bg-emerald-600 text-white text-xs font-extrabold disabled:opacity-40"
                            data-testid="btr-assignbank-submit">
                        {busy ? "..." : "تعيين البنك"}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ──────────────────────────────── Page ───────────────────────────────

export default function BankTransferReview() {
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [summary, setSummary] = useState(null);
    const [loading, setLoading] = useState(true);

    const [filterStatus, setFilterStatus] = useState("pending");
    const [filterSource, setFilterSource] = useState("");
    const [search, setSearch] = useState("");

    const [confirmReview, setConfirmReview] = useState(null);
    const [rejectReview, setRejectReview] = useState(null);
    const [assignReview, setAssignReview] = useState(null);
    const [createOpen, setCreateOpen] = useState(false);
    // Iter-251 · Phase 1.5 — provider→bank routing config.
    const [providerBanks, setProviderBanks] = useState({});
    const [bankAccounts, setBankAccounts] = useState([]);

    useEffect(() => {
        let alive = true;
        Promise.all([
            api.get("/bank-transfer-review/config/provider-banks"),
            api.get("/accounts", { params: { account_type: "bank" } }),
        ]).then(([cfg, accs]) => {
            if (!alive) return;
            setProviderBanks(cfg.data || {});
            const list = Array.isArray(accs.data) ? accs.data
                : (accs.data?.items || []);
            setBankAccounts(list.filter((a) => a.account_type === "bank"));
        }).catch(() => { /* ignore */ });
        return () => { alive = false; };
    }, []);

    const reload = useCallback(async () => {
        setLoading(true);
        try {
            const [listResp, sumResp, cfgResp] = await Promise.all([
                api.get("/bank-transfer-review", {
                    params: {
                        status: filterStatus || undefined,
                        source_type: filterSource || undefined,
                        q: search || undefined,
                        limit: 200,
                    },
                }),
                api.get("/bank-transfer-review/summary"),
                api.get("/bank-transfer-review/config/provider-banks"),
            ]);
            setItems(listResp.data.items || []);
            setTotal(listResp.data.total || 0);
            setSummary(sumResp.data);
            setProviderBanks(cfgResp.data || {});
        } catch (e) {
            toast.error("فشل تحميل البيانات");
        } finally {
            setLoading(false);
        }
    }, [filterStatus, filterSource, search]);

    useEffect(() => { reload(); }, [reload]);

    const filterChips = useMemo(() => [
        { key: "",                          label: "الكل",                count: total },
        { key: "missing_target_bank",       label: "بدون بنك مستلم",
          count: summary?.by_status?.missing_target_bank?.count || 0 },
        { key: "pending",                   label: "بانتظار التأكيد",
          count: summary?.by_status?.pending?.count || 0 },
        { key: "confirmed",                 label: "مؤكد",
          count: summary?.by_status?.confirmed?.count || 0 },
        { key: "confirmed_with_difference", label: "مؤكد مع فرق",
          count: summary?.by_status?.confirmed_with_difference?.count || 0 },
        { key: "rejected",                  label: "مرفوض",
          count: summary?.by_status?.rejected?.count || 0 },
        { key: "legacy_confirmed",          label: "مؤكد سابقاً (قديم)",
          count: summary?.by_status?.legacy_confirmed?.count || 0 },
    ], [summary, total]);

    // Iter-251 · Phase 1.5 — Unconfigured providers banner.
    const unconfiguredProviders = useMemo(
        () => Object.entries(providerBanks || {})
            .filter(([_, v]) => !v.configured)
            .map(([k]) => k),
        [providerBanks],
    );
    const PROV_AR = { salla: "سلة", tamara: "تمارا",
                       tabby: "تابي", imkan: "إمكان" };

    return (
        <div className="p-4" dir="rtl" data-testid="bank-transfer-review-page">
            <div className="flex items-start justify-between mb-3 flex-wrap gap-2">
                <div>
                    <h1 className="text-xl font-extrabold text-slate-900">
                        🏦 مراجعة التحويلات البنكية
                    </h1>
                    <p className="text-xs text-slate-500 max-w-2xl mt-1">
                        قائمة الانتظار المركزية لكل تحويل وارد من سلة/تمارا/تابي/إمكان/شركات الشحن/العملاء.
                        لا يدخل أي مبلغ في رصيد البنك قبل تأكيد الموظف هنا.
                    </p>
                </div>
                <button
                    onClick={() => setCreateOpen(true)}
                    className="bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-extrabold px-4 py-2 rounded-lg shadow"
                    data-testid="btr-add-button"
                >
                    ➕ إضافة سجل يدوي
                </button>
            </div>

            <SummaryCards summary={summary} />

            {/* Iter-251 · Phase 1.5 — Provider routing health banner. */}
            {unconfiguredProviders.length > 0 && (
                <div className="bg-amber-50 border border-amber-300 rounded-xl p-3 mb-4 flex items-start gap-3"
                     data-testid="btr-unconfigured-banner">
                    <span className="text-2xl leading-none">⚠️</span>
                    <div className="flex-1">
                        <div className="text-xs font-extrabold text-amber-900 mb-1">
                            مزودون بدون بنك مستلم افتراضي
                        </div>
                        <div className="text-[11px] text-amber-800 leading-relaxed">
                            أي تسوية واردة من هؤلاء المزودين ستبقى بحالة
                            «<b>بدون بنك مستلم</b>» في القائمة أدناه
                            حتى يختار الموظف البنك يدوياً.
                            اذهب إلى <a href="/settings"
                                className="underline font-bold hover:text-amber-950"
                                data-testid="btr-banner-settings-link">
                                الإعدادات
                            </a> لتحديد البنك:
                        </div>
                        <div className="flex flex-wrap gap-2 mt-2">
                            {unconfiguredProviders.map((p) => {
                                const stuck = providerBanks[p]?.missing_target_bank_count || 0;
                                return (
                                    <span key={p}
                                          className="text-[10px] px-2 py-1 rounded-full bg-amber-200 text-amber-900 font-bold border border-amber-300">
                                        {PROV_AR[p] || p}
                                        {stuck > 0 && (
                                            <span className="ms-1 text-rose-700">
                                                ({stuck} عالقة)
                                            </span>
                                        )}
                                    </span>
                                );
                            })}
                        </div>
                    </div>
                </div>
            )}

            <div className="flex flex-wrap items-center gap-2 mb-3 bg-slate-50 rounded-xl p-3">
                {filterChips.map(({ key, label, count }) => (
                    <button
                        key={key || "all"}
                        onClick={() => setFilterStatus(key)}
                        className={`text-xs font-bold px-3 py-1.5 rounded-full border whitespace-nowrap transition-colors ${
                            filterStatus === key
                                ? "bg-slate-900 text-white border-slate-900"
                                : "bg-white text-slate-700 border-slate-300 hover:bg-slate-100"
                        }`}
                        data-testid={`btr-filter-status-${key || "all"}`}
                    >
                        {label} <span className="opacity-70">({count})</span>
                    </button>
                ))}
                <div className="flex-grow" />
                <select
                    value={filterSource}
                    onChange={(e) => setFilterSource(e.target.value)}
                    className="text-xs border rounded-lg px-2 py-1.5 bg-white"
                    data-testid="btr-filter-source"
                >
                    <option value="">كل المصادر</option>
                    {Object.entries(SOURCE_LABELS).map(([k, v]) =>
                        <option key={k} value={k}>{v.ar}</option>)}
                </select>
                <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="بحث..."
                    className="text-xs border rounded-lg px-3 py-1.5 w-40"
                    data-testid="btr-search-input"
                />
            </div>

            <div className="bg-white rounded-xl border border-slate-200 overflow-x-auto">
                <table className="min-w-full text-xs">
                    <thead className="bg-slate-100 sticky top-0">
                        <tr className="text-right">
                            <th className="p-2.5">المصدر</th>
                            <th className="p-2.5">من</th>
                            <th className="p-2.5">إلى</th>
                            <th className="p-2.5">المرجع</th>
                            <th className="p-2.5">التاريخ</th>
                            <th className="p-2.5 text-center">المتوقع</th>
                            <th className="p-2.5 text-center">الواصل</th>
                            <th className="p-2.5 text-center">الفرق</th>
                            <th className="p-2.5">الحالة</th>
                            <th className="p-2.5">المراجع</th>
                            <th className="p-2.5 text-center">إجراء</th>
                        </tr>
                    </thead>
                    <tbody data-testid="btr-table-body">
                        {loading ? (
                            <tr><td colSpan={11} className="p-6 text-center text-slate-500">
                                جارٍ التحميل...
                            </td></tr>
                        ) : items.length === 0 ? (
                            <tr><td colSpan={11} className="p-6 text-center text-slate-500"
                                    data-testid="btr-empty">
                                لا توجد سجلات بهذا الفلتر
                            </td></tr>
                        ) : items.map((r) => {
                            const diffNeg = r.difference != null && r.difference < 0;
                            return (
                            <tr key={r.id} className="border-t hover:bg-slate-50"
                                data-testid={`btr-row-${r.id}`}>
                                <td className="p-2"><SourceChip source={r.source_type} /></td>
                                <td className="p-2 font-bold text-slate-700">
                                    {r.source_account_name}
                                    {r.internal_reference && (
                                        <div className="text-[10px] text-slate-500 font-mono">
                                            #{r.internal_reference}
                                        </div>
                                    )}
                                </td>
                                <td className="p-2 font-bold text-slate-700">
                                    {r.target_bank_name || (
                                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-rose-100 text-rose-800 border border-rose-300 font-bold">
                                            ⚠ غير محدد
                                        </span>
                                    )}
                                </td>
                                <td className="p-2 font-mono text-[11px] text-slate-600">
                                    {r.provider_reference || r.bank_reference || r.source_id || "—"}
                                </td>
                                <td className="p-2 font-mono text-slate-600">
                                    {fmtDate(r.transfer_date)}
                                </td>
                                <td className="p-2 text-center font-mono font-bold text-emerald-700">
                                    {fmt(r.expected_amount)}
                                </td>
                                <td className="p-2 text-center font-mono">
                                    {r.received_amount == null
                                        ? <span className="text-slate-400">—</span>
                                        : fmt(r.received_amount)}
                                </td>
                                <td className={`p-2 text-center font-mono font-bold ${diffNeg ? "text-rose-700" : "text-slate-500"}`}>
                                    {r.difference == null ? "—" : fmt(r.difference)}
                                </td>
                                <td className="p-2"><StatusChip status={r.status} /></td>
                                <td className="p-2 text-[11px] text-slate-600">
                                    {r.reviewed_by_name || "—"}
                                    {r.reviewed_at && (
                                        <div className="text-[9px] text-slate-400 font-mono">
                                            {fmtDate(r.reviewed_at)}
                                        </div>
                                    )}
                                </td>
                                <td className="p-2 text-center">
                                    {r.status === "missing_target_bank" ? (
                                        <button
                                            onClick={() => setAssignReview(r)}
                                            className="text-rose-700 hover:bg-rose-100 text-[11px] font-bold px-2 py-1 rounded border border-rose-300"
                                            data-testid={`btr-assignbank-btn-${r.id}`}
                                            title="تعيين البنك المستلم"
                                        >🏦 تعيين بنك</button>
                                    ) : r.status === "pending" ? (
                                        <div className="flex gap-1 justify-center">
                                            <button
                                                onClick={() => setConfirmReview(r)}
                                                className="text-emerald-700 hover:bg-emerald-100 text-[11px] font-bold px-2 py-1 rounded border border-emerald-300"
                                                data-testid={`btr-confirm-btn-${r.id}`}
                                                title="تأكيد الوصول"
                                            >✓ تأكيد</button>
                                            <button
                                                onClick={() => setRejectReview(r)}
                                                className="text-rose-700 hover:bg-rose-100 text-[11px] font-bold px-2 py-1 rounded border border-rose-300"
                                                data-testid={`btr-reject-btn-${r.id}`}
                                                title="رفض/تعليق"
                                            >✗ رفض</button>
                                        </div>
                                    ) : (
                                        <span className="text-[10px] text-slate-400">مغلق</span>
                                    )}
                                </td>
                            </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            <ConfirmModal
                open={!!confirmReview}
                review={confirmReview}
                onClose={() => setConfirmReview(null)}
                onDone={() => { setConfirmReview(null); reload(); }}
            />
            <RejectModal
                open={!!rejectReview}
                review={rejectReview}
                onClose={() => setRejectReview(null)}
                onDone={() => { setRejectReview(null); reload(); }}
            />
            <AssignBankModal
                open={!!assignReview}
                review={assignReview}
                banks={bankAccounts}
                onClose={() => setAssignReview(null)}
                onDone={() => { setAssignReview(null); reload(); }}
            />
            <CreateModal
                open={createOpen}
                onClose={() => setCreateOpen(false)}
                onDone={() => { setCreateOpen(false); reload(); }}
            />
        </div>
    );
}
