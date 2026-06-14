/**
 * Iter-199 — Salary Payment Reversal screen.
 *
 * عملية مستقلة عن "تصحيح موظف خطأ" (Iter-196). هنا نعكس صرف الراتب
 * بالكامل — البنك يستعيد المبلغ والراتب يعود مستحقاً.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { ArrowUUpLeft, Warning, Bank, ArrowRight } from "@phosphor-icons/react";

const fmt = (v) =>
    `${Number(v || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    })} ر.س`;

export default function SalaryReversals() {
    const [employees, setEmployees] = useState([]);
    const [empId, setEmpId] = useState("");
    const [ops, setOps] = useState([]);
    const [selected, setSelected] = useState(null);
    const [reason, setReason] = useState("");
    const [busy, setBusy] = useState(false);
    const [confirm, setConfirm] = useState(false);
    const [log, setLog] = useState([]);

    useEffect(() => {
        (async () => {
            try {
                const [e, l] = await Promise.all([
                    api.get("/accounting/employees/list"),
                    api.get("/accounting/employees/salary-reversals"),
                ]);
                setEmployees(e.data?.employees || []);
                setLog((l.data?.reversals || []).slice(0, 50));
            } catch (er) {
                toast.error(formatApiErrorDetail(er));
            }
        })();
    }, []);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            if (!empId) {
                if (!cancelled) {
                    setOps([]);
                    setSelected(null);
                }
                return;
            }
            try {
                const r = await api.get(
                    `/accounting/employees/${empId}/reversible-salary-payments`
                );
                if (!cancelled) {
                    setOps(r.data?.operations || []);
                    setSelected(null);
                }
            } catch (e) {
                if (!cancelled) toast.error(formatApiErrorDetail(e));
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [empId]);

    const empName = useMemo(
        () => employees.find((e) => e.id === empId)?.name || "",
        [employees, empId]
    );

    const handleSubmit = () => {
        if (!selected) return toast.error("اختر عملية الراتب");
        if (selected.already_reversed)
            return toast.error("هذه العملية معكوسة مسبقاً");
        if (reason.trim().length < 5)
            return toast.error("اكتب سبباً واضحاً (5 أحرف على الأقل)");
        setConfirm(true);
    };

    const handleConfirm = async () => {
        setBusy(true);
        try {
            const r = await api.post(
                "/accounting/employees/reverse-salary-payment",
                {
                    original_txn_group_id: selected.txn_group_id,
                    reason: reason.trim(),
                }
            );
            toast.success(r.data?.summary || "تم العكس بنجاح");
            setConfirm(false);
            setSelected(null);
            setReason("");
            const [opsR, logR] = await Promise.all([
                api.get(
                    `/accounting/employees/${empId}/reversible-salary-payments`
                ),
                api.get("/accounting/employees/salary-reversals"),
            ]);
            setOps(opsR.data?.operations || []);
            setLog((logR.data?.reversals || []).slice(0, 50));
        } catch (e) {
            toast.error(formatApiErrorDetail(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="space-y-6" data-testid="salary-reversals-page">
            <div className="flex items-start gap-3">
                <div className="p-3 rounded-xl bg-rose-100">
                    <ArrowUUpLeft size={28} weight="duotone" className="text-rose-700" />
                </div>
                <div>
                    <h1 className="text-2xl font-extrabold text-foreground">
                        عكس صرف راتب
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
                        إلغاء عملية صرف راتب بالكامل وإرجاع المبلغ محاسبياً إلى البنك أو
                        الصندوق. هذه العملية مختلفة عن &laquo;تصحيح موظف&raquo;: هنا
                        <b> يتأثر البنك </b> ويستعيد المبلغ. القيد الأصلي لا يُحذف ولا يُعدَّل.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 bg-white border border-border rounded-xl p-5 space-y-4">
                    <div>
                        <label className="text-xs font-bold text-muted-foreground mb-1 block">
                            الموظف
                        </label>
                        <select
                            data-testid="reversal-employee-picker"
                            value={empId}
                            onChange={(e) => setEmpId(e.target.value)}
                            className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-rose-500"
                        >
                            <option value="">— اختر الموظف —</option>
                            {employees.map((e) => (
                                <option key={e.id} value={e.id}>
                                    {e.name}
                                </option>
                            ))}
                        </select>
                    </div>

                    {empId && (
                        <div>
                            <label className="text-xs font-bold text-muted-foreground mb-2 block">
                                عمليات صرف الراتب
                            </label>
                            {ops.length === 0 ? (
                                <div className="text-sm text-muted-foreground p-3 bg-slate-50 rounded-lg">
                                    لا توجد عمليات صرف راتب لهذا الموظف.
                                </div>
                            ) : (
                                <div className="max-h-80 overflow-y-auto border border-border rounded-lg divide-y">
                                    {ops.map((o) => {
                                        const isSelected =
                                            selected?.txn_group_id === o.txn_group_id;
                                        return (
                                            <button
                                                key={o.ledger_id}
                                                type="button"
                                                data-testid={`reversal-op-${o.ledger_id}`}
                                                disabled={o.already_reversed}
                                                onClick={() => setSelected(o)}
                                                className={`w-full text-right p-3 transition flex items-center justify-between gap-3 ${
                                                    isSelected
                                                        ? "bg-rose-50 ring-2 ring-rose-400"
                                                        : o.already_reversed
                                                        ? "opacity-50 cursor-not-allowed"
                                                        : "hover:bg-slate-50"
                                                }`}
                                            >
                                                <div className="flex-1">
                                                    <div className="flex items-center gap-2 flex-wrap">
                                                        <span className="px-2 py-0.5 rounded text-xs font-bold bg-emerald-100 text-emerald-800">
                                                            صرف راتب
                                                        </span>
                                                        {o.bank_account_name && (
                                                            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                                                                <Bank size={12} />
                                                                {o.bank_account_name}
                                                            </span>
                                                        )}
                                                        <span className="text-xs text-muted-foreground">
                                                            {o.posted_at?.slice(0, 10)}
                                                        </span>
                                                        {o.already_reversed && (
                                                            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-100 text-rose-700">
                                                                مَعكوسة
                                                            </span>
                                                        )}
                                                    </div>
                                                    {o.notes && (
                                                        <p className="text-xs text-muted-foreground mt-1 truncate">
                                                            {o.notes}
                                                        </p>
                                                    )}
                                                </div>
                                                <div className="text-left">
                                                    <div className="num font-extrabold text-foreground">
                                                        {fmt(o.amount)}
                                                    </div>
                                                </div>
                                            </button>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    )}

                    {selected && !selected.already_reversed && (
                        <>
                            <div>
                                <label className="text-xs font-bold text-muted-foreground mb-1 block">
                                    سبب العكس (إجباري)
                                </label>
                                <textarea
                                    data-testid="reversal-reason"
                                    value={reason}
                                    onChange={(e) => setReason(e.target.value)}
                                    rows={3}
                                    className="w-full px-3 py-2 text-sm border border-border rounded-lg resize-none"
                                    placeholder="مثال: لم يتم دفع الراتب فعلياً، تم تسجيله بالخطأ"
                                />
                            </div>
                            <div className="flex items-center justify-between bg-rose-50 border border-rose-200 rounded-lg p-3">
                                <div className="flex items-start gap-2 text-sm text-rose-800">
                                    <Warning size={20} weight="duotone" className="shrink-0 mt-0.5" />
                                    <span>
                                        <b>سيتأثر البنك:</b> سيُستعاد مبلغ {fmt(selected.amount)}
                                        {selected.bank_account_name &&
                                            ` إلى ${selected.bank_account_name}`}
                                        . القيد الأصلي يبقى محفوظاً.
                                    </span>
                                </div>
                                <button
                                    type="button"
                                    onClick={handleSubmit}
                                    disabled={busy}
                                    data-testid="submit-reversal"
                                    className="px-4 py-2 bg-rose-600 text-white text-sm font-bold rounded-lg hover:bg-rose-700 transition disabled:opacity-50 shrink-0"
                                >
                                    عكس الصرف
                                </button>
                            </div>
                        </>
                    )}
                </div>

                <div className="bg-white border border-border rounded-xl p-5">
                    <h3 className="font-bold text-foreground mb-3 flex items-center gap-2">
                        <ArrowUUpLeft size={18} className="text-rose-600" />
                        سجل العكوسات
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
                                    data-testid={`reversal-log-${c.reversal_group_id}`}
                                >
                                    <div className="flex items-center gap-1 mb-1 flex-wrap">
                                        <span className="font-bold">
                                            {c.employee?.name || "موظف"}
                                        </span>
                                        <ArrowRight size={12} className="text-rose-600" />
                                        <span className="inline-flex items-center gap-1 font-bold text-emerald-700">
                                            <Bank size={12} />
                                            {c.bank_account?.name || "بنك"}
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
                    data-testid="reversal-confirm-modal"
                >
                    <div className="bg-white rounded-xl max-w-md w-full p-6 space-y-4">
                        <div className="flex items-center gap-3">
                            <Warning size={28} className="text-rose-600" weight="duotone" />
                            <h3 className="text-lg font-extrabold">
                                تأكيد عكس صرف الراتب
                            </h3>
                        </div>
                        <div className="bg-slate-50 rounded-lg p-3 text-sm space-y-2">
                            <div>
                                <span className="text-muted-foreground">الموظف: </span>
                                <b>{empName}</b>
                            </div>
                            <div>
                                <span className="text-muted-foreground">المبلغ: </span>
                                <b className="num">{fmt(selected?.amount)}</b>
                            </div>
                            {selected?.bank_account_name && (
                                <div>
                                    <span className="text-muted-foreground">سيُستعاد إلى: </span>
                                    <b className="text-emerald-700">
                                        {selected.bank_account_name}
                                    </b>
                                </div>
                            )}
                            <div>
                                <span className="text-muted-foreground">السبب: </span>
                                <span>{reason}</span>
                            </div>
                        </div>
                        <div className="bg-rose-50 border border-rose-200 rounded-lg p-3 text-sm text-rose-800 flex items-start gap-2">
                            <Warning size={20} weight="duotone" className="shrink-0 mt-0.5" />
                            <div>
                                <b>هذه عملية تؤثر على البنك.</b>
                                <br />
                                سيتم إنشاء قيد عكسي يُرجع المبلغ محاسبياً إلى البنك. القيد
                                الأصلي يبقى ظاهراً للتدقيق.
                            </div>
                        </div>
                        <div className="flex gap-2 justify-end">
                            <button
                                onClick={() => setConfirm(false)}
                                disabled={busy}
                                className="px-4 py-2 text-sm font-bold border border-border rounded-lg hover:bg-slate-50"
                                data-testid="cancel-reversal"
                            >
                                إلغاء
                            </button>
                            <button
                                onClick={handleConfirm}
                                disabled={busy}
                                className="px-4 py-2 text-sm font-bold bg-rose-600 text-white rounded-lg hover:bg-rose-700 disabled:opacity-50"
                                data-testid="confirm-reversal"
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
