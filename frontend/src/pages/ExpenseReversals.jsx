/**
 * Iter-201 — Expense Reversal screen.
 *
 * Independent operation. Mirrors every leg of an expense_record so
 * the source account (bank/cash/payment_platform/employee custody)
 * gets the money back exactly where it left from. Original entry
 * stays visible and is NEVER modified.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { ArrowUUpLeft, Warning, ArrowRight, Receipt } from "@phosphor-icons/react";

const fmt = (v) =>
    `${Number(v || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    })} ر.س`;

const SRC_LABEL = {
    bank: "بنك",
    cash: "صندوق",
    payment_platform: "منصة دفع",
    employee: "موظف (عهدة)",
};

export default function ExpenseReversals() {
    const [ops, setOps] = useState([]);
    const [log, setLog] = useState([]);
    const [selected, setSelected] = useState(null);
    const [reason, setReason] = useState("");
    const [busy, setBusy] = useState(false);
    const [confirm, setConfirm] = useState(false);

    const reload = async () => {
        try {
            const [o, l] = await Promise.all([
                api.get("/accounting/expenses/reversible"),
                api.get("/accounting/expenses/reversals"),
            ]);
            setOps(o.data?.operations || []);
            setLog((l.data?.reversals || []).slice(0, 50));
        } catch (e) {
            toast.error(formatApiErrorDetail(e));
        }
    };

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const [o, l] = await Promise.all([
                    api.get("/accounting/expenses/reversible"),
                    api.get("/accounting/expenses/reversals"),
                ]);
                if (!cancelled) {
                    setOps(o.data?.operations || []);
                    setLog((l.data?.reversals || []).slice(0, 50));
                }
            } catch (e) {
                if (!cancelled) toast.error(formatApiErrorDetail(e));
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const handleSubmit = () => {
        if (!selected) return toast.error("اختر المصروف");
        if (selected.already_reversed)
            return toast.error("هذا المصروف معكوس مسبقاً");
        if (reason.trim().length < 5)
            return toast.error("اكتب سبباً واضحاً (5 أحرف على الأقل)");
        setConfirm(true);
    };

    const handleConfirm = async () => {
        setBusy(true);
        try {
            const r = await api.post("/accounting/expenses/reverse", {
                original_txn_group_id: selected.txn_group_id,
                reason: reason.trim(),
            });
            toast.success(r.data?.summary || "تم العكس بنجاح");
            setConfirm(false);
            setSelected(null);
            setReason("");
            await reload();
        } catch (e) {
            toast.error(formatApiErrorDetail(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="space-y-6" data-testid="expense-reversals-page">
            <div className="flex items-start gap-3">
                <div className="p-3 rounded-xl bg-rose-100">
                    <ArrowUUpLeft size={28} weight="duotone" className="text-rose-700" />
                </div>
                <div>
                    <h1 className="text-2xl font-extrabold text-foreground">
                        عكس مصروف
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
                        إلغاء عملية مصروف وإرجاع المبلغ <b>إلى نفس الحساب الذي خرج منه</b> (بنك،
                        صندوق، منصة دفع، أو عهدة موظف). القيد الأصلي يبقى محفوظاً للتدقيق.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 bg-white border border-border rounded-xl p-5 space-y-4">
                    <label className="text-xs font-bold text-muted-foreground block">
                        المصروفات المسجلة (الأحدث أولاً)
                    </label>
                    {ops.length === 0 ? (
                        <div className="text-sm text-muted-foreground p-3 bg-slate-50 rounded-lg">
                            لا توجد مصروفات قابلة للعكس.
                        </div>
                    ) : (
                        <div className="max-h-80 overflow-y-auto border border-border rounded-lg divide-y">
                            {ops.map((o) => {
                                const isSel = selected?.txn_group_id === o.txn_group_id;
                                return (
                                    <button
                                        key={o.txn_group_id}
                                        type="button"
                                        data-testid={`expense-op-${o.txn_group_id}`}
                                        disabled={o.already_reversed}
                                        onClick={() => setSelected(o)}
                                        className={`w-full text-right p-3 transition flex items-center justify-between gap-3 ${
                                            isSel
                                                ? "bg-rose-50 ring-2 ring-rose-400"
                                                : o.already_reversed
                                                ? "opacity-50 cursor-not-allowed"
                                                : "hover:bg-slate-50"
                                        }`}
                                    >
                                        <div className="flex-1">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <span className="px-2 py-0.5 rounded text-xs font-bold bg-emerald-100 text-emerald-800 inline-flex items-center gap-1">
                                                    <Receipt size={12} /> مصروف
                                                </span>
                                                <span className="text-xs text-muted-foreground">
                                                    {SRC_LABEL[o.source_type] || o.source_type}
                                                    {o.source_name ? `: ${o.source_name}` : ""}
                                                </span>
                                                <span className="text-xs text-muted-foreground">
                                                    {o.posted_at?.slice(0, 10)}
                                                </span>
                                                {o.already_reversed && (
                                                    <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-100 text-rose-700">
                                                        مَعكوس
                                                    </span>
                                                )}
                                            </div>
                                            {o.notes && (
                                                <p className="text-xs text-muted-foreground mt-1 truncate">
                                                    {o.notes}
                                                </p>
                                            )}
                                        </div>
                                        <div className="num font-extrabold text-foreground">
                                            {fmt(o.amount)}
                                        </div>
                                    </button>
                                );
                            })}
                        </div>
                    )}

                    {selected && !selected.already_reversed && (
                        <>
                            <div>
                                <label className="text-xs font-bold text-muted-foreground mb-1 block">
                                    سبب العكس (إجباري)
                                </label>
                                <textarea
                                    data-testid="expense-reversal-reason"
                                    value={reason}
                                    onChange={(e) => setReason(e.target.value)}
                                    rows={3}
                                    className="w-full px-3 py-2 text-sm border border-border rounded-lg resize-none"
                                    placeholder="مثال: تم تسجيل المصروف بالخطأ ولم يحدث فعلياً"
                                />
                            </div>
                            <div className="flex items-center justify-between bg-rose-50 border border-rose-200 rounded-lg p-3">
                                <div className="flex items-start gap-2 text-sm text-rose-800">
                                    <Warning size={20} weight="duotone" className="shrink-0 mt-0.5" />
                                    <span>
                                        سيُستعاد {fmt(selected.amount)} إلى{" "}
                                        <b>{selected.source_name || (SRC_LABEL[selected.source_type] || "مصدر المصروف")}</b>.
                                    </span>
                                </div>
                                <button
                                    type="button"
                                    onClick={handleSubmit}
                                    disabled={busy}
                                    data-testid="submit-expense-reversal"
                                    className="px-4 py-2 bg-rose-600 text-white text-sm font-bold rounded-lg hover:bg-rose-700 transition disabled:opacity-50 shrink-0"
                                >
                                    عكس المصروف
                                </button>
                            </div>
                        </>
                    )}
                </div>

                <div className="bg-white border border-border rounded-xl p-5">
                    <h3 className="font-bold text-foreground mb-3 flex items-center gap-2">
                        <ArrowUUpLeft size={18} className="text-rose-600" />
                        سجل عكوسات المصروفات
                    </h3>
                    {log.length === 0 ? (
                        <p className="text-xs text-muted-foreground">
                            لا توجد عكوسات سابقة.
                        </p>
                    ) : (
                        <div className="space-y-2 max-h-[500px] overflow-y-auto">
                            {log.map((c) => (
                                <div
                                    key={c.reversal_group_id}
                                    className="text-xs border border-border rounded-lg p-2 bg-slate-50"
                                    data-testid={`expense-rev-log-${c.reversal_group_id}`}
                                >
                                    <div className="flex items-center gap-1 mb-1 flex-wrap">
                                        <span className="font-bold">مصروف</span>
                                        <ArrowRight size={12} className="text-rose-600" />
                                        <span className="font-bold text-emerald-700">
                                            {c.source_account?.name ||
                                                SRC_LABEL[c.source_account?.type] || "—"}
                                        </span>
                                        <span className="ms-auto num font-extrabold text-foreground">
                                            {fmt(c.amount)}
                                        </span>
                                    </div>
                                    {c.reason && (
                                        <div className="text-[10px] text-muted-foreground truncate">
                                            {c.reason}
                                        </div>
                                    )}
                                    <div className="text-[10px] text-muted-foreground mt-1">
                                        {c.posted_at?.slice(0, 16)?.replace("T", " ")}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {confirm && (
                <div
                    className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
                    data-testid="expense-reversal-confirm-modal"
                >
                    <div className="bg-white rounded-xl max-w-md w-full p-6 space-y-4">
                        <div className="flex items-center gap-3">
                            <Warning size={28} className="text-rose-600" weight="duotone" />
                            <h3 className="text-lg font-extrabold">تأكيد عكس المصروف</h3>
                        </div>
                        <div className="bg-slate-50 rounded-lg p-3 text-sm space-y-2">
                            <div>
                                <span className="text-muted-foreground">المبلغ: </span>
                                <b className="num">{fmt(selected?.amount)}</b>
                            </div>
                            <div>
                                <span className="text-muted-foreground">سيُستعاد إلى: </span>
                                <b className="text-emerald-700">
                                    {selected?.source_name ||
                                        SRC_LABEL[selected?.source_type] || "—"}
                                </b>
                            </div>
                            <div>
                                <span className="text-muted-foreground">السبب: </span>
                                <span>{reason}</span>
                            </div>
                        </div>
                        <div className="bg-rose-50 border border-rose-200 rounded-lg p-3 text-sm text-rose-800 flex items-start gap-2">
                            <Warning size={20} weight="duotone" className="shrink-0 mt-0.5" />
                            <div>
                                سيتم إنشاء قيد عكسي يُرجع المبلغ إلى مصدر المصروف. القيد
                                الأصلي يبقى محفوظاً.
                            </div>
                        </div>
                        <div className="flex gap-2 justify-end">
                            <button
                                onClick={() => setConfirm(false)}
                                disabled={busy}
                                className="px-4 py-2 text-sm font-bold border border-border rounded-lg hover:bg-slate-50"
                                data-testid="cancel-expense-reversal"
                            >
                                إلغاء
                            </button>
                            <button
                                onClick={handleConfirm}
                                disabled={busy}
                                className="px-4 py-2 text-sm font-bold bg-rose-600 text-white rounded-lg hover:bg-rose-700 disabled:opacity-50"
                                data-testid="confirm-expense-reversal"
                            >
                                {busy ? "جارٍ التنفيذ..." : "تأكيد العكس"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
