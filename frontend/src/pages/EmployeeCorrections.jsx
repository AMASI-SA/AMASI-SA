/**
 * Iter-196 — Employee Misposting Correction screen.
 *
 * Lets the merchant move the accounting effect of a salary_payment,
 * advance_grant, or custody_grant from a WRONG employee to the CORRECT
 * one — WITHOUT touching the bank/cash (the money already left).
 *
 * The page is read-only on bank/cash and write-only on the employee
 * ledger pair. Original entries are NEVER modified or hidden.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { ArrowsLeftRight, ShieldCheck, Warning, ArrowRight } from "@phosphor-icons/react";

const OP_LABEL = {
    salary_payment: { ar: "📅 صرف راتب",   color: "bg-emerald-100 text-emerald-800" },
    advance_grant:  { ar: "💰 منح سُلفة",   color: "bg-amber-100 text-amber-800" },
    custody_grant:  { ar: "🎒 تسليم عهدة",  color: "bg-sky-100 text-sky-800" },
};

const fmt = (v) =>
    `${Number(v || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    })} ر.س`;

export default function EmployeeCorrections() {
    const [employees, setEmployees] = useState([]);
    const [fromEmpId, setFromEmpId] = useState("");
    const [toEmpId, setToEmpId] = useState("");
    const [operations, setOperations] = useState([]);
    const [selectedTxn, setSelectedTxn] = useState(null);
    const [amount, setAmount] = useState("");
    const [reason, setReason] = useState("");
    const [busy, setBusy] = useState(false);
    const [confirm, setConfirm] = useState(false);
    const [auditLog, setAuditLog] = useState([]);

    useEffect(() => {
        (async () => {
            try {
                const [emps, log] = await Promise.all([
                    api.get("/accounting/employees/list"),
                    api.get("/accounting/employees/corrections"),
                ]);
                setEmployees(emps.data?.employees || []);
                setAuditLog((log.data?.corrections || []).slice(0, 50));
            } catch (e) {
                toast.error(formatApiErrorDetail(e));
            }
        })();
    }, []);

    // Load correctable operations when the wrong employee changes
    useEffect(() => {
        let cancelled = false;
        (async () => {
            if (!fromEmpId) {
                if (!cancelled) {
                    setOperations([]);
                    setSelectedTxn(null);
                }
                return;
            }
            try {
                const r = await api.get(
                    `/accounting/employees/${fromEmpId}/correctable-operations`
                );
                if (!cancelled) {
                    setOperations(r.data?.operations || []);
                    setSelectedTxn(null);
                }
            } catch (e) {
                if (!cancelled) toast.error(formatApiErrorDetail(e));
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [fromEmpId]);

    const fromEmpName = useMemo(
        () => employees.find((e) => e.id === fromEmpId)?.name || "",
        [employees, fromEmpId]
    );
    const toEmpName = useMemo(
        () => employees.find((e) => e.id === toEmpId)?.name || "",
        [employees, toEmpId]
    );

    const handleSubmit = async () => {
        if (!selectedTxn) return toast.error("اختر العملية الأصلية");
        if (!toEmpId) return toast.error("اختر الموظف الصحيح");
        if (fromEmpId === toEmpId)
            return toast.error("الموظف الخطأ والصحيح لا يمكن أن يكونا نفس الشخص");
        const amt = parseFloat(amount);
        if (!amt || amt <= 0) return toast.error("أدخل مبلغاً صحيحاً");
        if (amt > selectedTxn.remaining_correctable + 0.001)
            return toast.error(
                `المبلغ أكبر من المتبقي القابل للتصحيح (${fmt(selectedTxn.remaining_correctable)})`
            );
        if (reason.trim().length < 5)
            return toast.error("اكتب سبباً واضحاً (5 أحرف على الأقل)");
        setConfirm(true);
    };

    const handleConfirm = async () => {
        setBusy(true);
        try {
            const r = await api.post(
                "/accounting/employees/correct-misposting",
                {
                    original_txn_group_id: selectedTxn.txn_group_id,
                    from_employee_id: fromEmpId,
                    to_employee_id: toEmpId,
                    amount: parseFloat(amount),
                    reason: reason.trim(),
                }
            );
            toast.success(r.data?.summary || "تم التصحيح بنجاح");
            // Refresh state
            setConfirm(false);
            setSelectedTxn(null);
            setAmount("");
            setReason("");
            setToEmpId("");
            const [opsR, logR] = await Promise.all([
                api.get(`/accounting/employees/${fromEmpId}/correctable-operations`),
                api.get("/accounting/employees/corrections"),
            ]);
            setOperations(opsR.data?.operations || []);
            setAuditLog((logR.data?.corrections || []).slice(0, 50));
        } catch (e) {
            toast.error(formatApiErrorDetail(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="space-y-6" data-testid="employee-corrections-page">
            <div className="flex items-start gap-3">
                <div className="p-3 rounded-xl bg-amber-100">
                    <ArrowsLeftRight size={28} weight="duotone" className="text-amber-700" />
                </div>
                <div>
                    <h1 className="text-2xl font-extrabold text-foreground">
                        تصحيح عملية موظف
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
                        لو سُجلت عملية (راتب / سلفة / عهدة) على موظف خطأ، انقل الأثر المحاسبي إلى
                        الموظف الصحيح <b>دون أي حركة بنكية جديدة</b>. القيد الأصلي يبقى محفوظاً
                        ويظهر بشارة &laquo;مُصحَّح&raquo;.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* ── Form ── */}
                <div className="lg:col-span-2 bg-white border border-border rounded-xl p-5 space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label className="text-xs font-bold text-muted-foreground mb-1 block">
                                الموظف الخطأ (الذي سُجلت عليه العملية)
                            </label>
                            <select
                                data-testid="from-employee-picker"
                                value={fromEmpId}
                                onChange={(e) => setFromEmpId(e.target.value)}
                                className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-amber-500"
                            >
                                <option value="">— اختر الموظف —</option>
                                {employees.map((e) => (
                                    <option key={e.id} value={e.id}>
                                        {e.name}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className="text-xs font-bold text-muted-foreground mb-1 block">
                                الموظف الصحيح
                            </label>
                            <select
                                data-testid="to-employee-picker"
                                value={toEmpId}
                                onChange={(e) => setToEmpId(e.target.value)}
                                disabled={!fromEmpId}
                                className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-emerald-500 disabled:bg-slate-100"
                            >
                                <option value="">— اختر الموظف —</option>
                                {employees
                                    .filter((e) => e.id !== fromEmpId)
                                    .map((e) => (
                                        <option key={e.id} value={e.id}>
                                            {e.name}
                                        </option>
                                    ))}
                            </select>
                        </div>
                    </div>

                    {fromEmpId && (
                        <div>
                            <label className="text-xs font-bold text-muted-foreground mb-2 block">
                                العملية الأصلية المراد تصحيحها
                            </label>
                            {operations.length === 0 ? (
                                <div className="text-sm text-muted-foreground p-3 bg-slate-50 rounded-lg">
                                    لا توجد عمليات قابلة للتصحيح لهذا الموظف.
                                </div>
                            ) : (
                                <div className="max-h-80 overflow-y-auto border border-border rounded-lg divide-y">
                                    {operations.map((o) => {
                                        const meta = OP_LABEL[o.entry_type] || {
                                            ar: o.entry_type, color: "bg-slate-100",
                                        };
                                        const isSelected =
                                            selectedTxn?.txn_group_id === o.txn_group_id;
                                        const isExhausted =
                                            o.remaining_correctable < 0.01;
                                        return (
                                            <button
                                                key={o.ledger_id}
                                                type="button"
                                                data-testid={`operation-${o.ledger_id}`}
                                                disabled={isExhausted}
                                                onClick={() => {
                                                    setSelectedTxn(o);
                                                    setAmount(
                                                        String(o.remaining_correctable)
                                                    );
                                                }}
                                                className={`w-full text-right p-3 transition flex items-center justify-between gap-3 ${
                                                    isSelected
                                                        ? "bg-amber-50 ring-2 ring-amber-400"
                                                        : isExhausted
                                                        ? "opacity-50 cursor-not-allowed"
                                                        : "hover:bg-slate-50"
                                                }`}
                                            >
                                                <div className="flex-1">
                                                    <div className="flex items-center gap-2">
                                                        <span
                                                            className={`px-2 py-0.5 rounded text-xs font-bold ${meta.color}`}
                                                        >
                                                            {meta.ar}
                                                        </span>
                                                        <span className="text-xs text-muted-foreground">
                                                            {o.posted_at?.slice(0, 10)}
                                                        </span>
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
                                                    <div className="text-[10px] text-muted-foreground">
                                                        المتبقي:{" "}
                                                        <b className="num">
                                                            {fmt(o.remaining_correctable)}
                                                        </b>
                                                    </div>
                                                </div>
                                            </button>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    )}

                    {selectedTxn && (
                        <>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label className="text-xs font-bold text-muted-foreground mb-1 block">
                                        المبلغ (يمكن تجزئته)
                                    </label>
                                    <input
                                        type="number"
                                        step="0.01"
                                        data-testid="correction-amount"
                                        value={amount}
                                        onChange={(e) => setAmount(e.target.value)}
                                        className="w-full px-3 py-2 text-sm border border-border rounded-lg num"
                                        placeholder="0.00"
                                    />
                                    <p className="text-[10px] text-muted-foreground mt-1">
                                        المتبقي القابل للتصحيح:{" "}
                                        <b>{fmt(selectedTxn.remaining_correctable)}</b>
                                    </p>
                                </div>
                            </div>
                            <div>
                                <label className="text-xs font-bold text-muted-foreground mb-1 block">
                                    السبب (إجباري)
                                </label>
                                <textarea
                                    data-testid="correction-reason"
                                    value={reason}
                                    onChange={(e) => setReason(e.target.value)}
                                    rows={3}
                                    className="w-full px-3 py-2 text-sm border border-border rounded-lg resize-none"
                                    placeholder="مثال: العملية كانت لمحمد وليس لخالد، خطأ من المحاسب"
                                />
                            </div>
                            <div className="flex items-center justify-between bg-emerald-50 border border-emerald-200 rounded-lg p-3">
                                <div className="flex items-center gap-2 text-sm text-emerald-800">
                                    <ShieldCheck size={20} weight="duotone" />
                                    <span>
                                        <b>البنك لن يتأثر.</b> القيد الأصلي يبقى كما هو.
                                    </span>
                                </div>
                                <button
                                    type="button"
                                    onClick={handleSubmit}
                                    disabled={busy}
                                    data-testid="submit-correction"
                                    className="px-4 py-2 bg-amber-600 text-white text-sm font-bold rounded-lg hover:bg-amber-700 transition disabled:opacity-50"
                                >
                                    تنفيذ التصحيح
                                </button>
                            </div>
                        </>
                    )}
                </div>

                {/* ── Audit log ── */}
                <div className="bg-white border border-border rounded-xl p-5">
                    <h3 className="font-bold text-foreground mb-3 flex items-center gap-2">
                        <ShieldCheck size={18} className="text-emerald-600" />
                        سجل آخر التصحيحات
                    </h3>
                    {auditLog.length === 0 ? (
                        <p className="text-xs text-muted-foreground">
                            لا توجد تصحيحات سابقة.
                        </p>
                    ) : (
                        <div className="space-y-2 max-h-[500px] overflow-y-auto">
                            {auditLog.map((c) => (
                                <div
                                    key={c.correction_group_id}
                                    className="text-xs border border-border rounded-lg p-2 bg-slate-50"
                                    data-testid={`audit-row-${c.correction_group_id}`}
                                >
                                    <div className="flex items-center gap-1 mb-1">
                                        <span className="font-bold">
                                            {c.from_employee?.name || "؟"}
                                        </span>
                                        <ArrowRight size={12} className="text-amber-600" />
                                        <span className="font-bold text-emerald-700">
                                            {c.to_employee?.name || "؟"}
                                        </span>
                                        <span className="ms-auto num font-extrabold text-foreground">
                                            {fmt(c.amount)}
                                        </span>
                                    </div>
                                    <div className="text-[10px] text-muted-foreground truncate">
                                        {c.reason}
                                    </div>
                                    <div className="text-[10px] text-muted-foreground mt-1">
                                        {c.posted_at?.slice(0, 16)?.replace("T", " ")}
                                        {c.is_partial && (
                                            <span className="ms-2 px-1 py-0.5 bg-amber-100 text-amber-700 rounded">
                                                جزئي
                                            </span>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {/* ── Confirmation modal ── */}
            {confirm && (
                <div
                    className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
                    data-testid="correction-confirm-modal"
                >
                    <div className="bg-white rounded-xl max-w-md w-full p-6 space-y-4">
                        <div className="flex items-center gap-3">
                            <Warning size={28} className="text-amber-600" weight="duotone" />
                            <h3 className="text-lg font-extrabold">
                                تأكيد التصحيح
                            </h3>
                        </div>
                        <div className="bg-slate-50 rounded-lg p-3 text-sm space-y-2">
                            <div>
                                <span className="text-muted-foreground">من: </span>
                                <b>{fromEmpName}</b>
                            </div>
                            <div>
                                <span className="text-muted-foreground">إلى: </span>
                                <b className="text-emerald-700">{toEmpName}</b>
                            </div>
                            <div>
                                <span className="text-muted-foreground">المبلغ: </span>
                                <b className="num">{fmt(parseFloat(amount))}</b>
                            </div>
                            <div>
                                <span className="text-muted-foreground">السبب: </span>
                                <span>{reason}</span>
                            </div>
                        </div>
                        <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 text-sm text-emerald-800 flex items-start gap-2">
                            <ShieldCheck size={20} weight="duotone" />
                            <div>
                                <b>لن يتأثر البنك ولا الصندوق.</b>
                                <br />
                                التصحيح يكون فقط بين حسابات الموظفَين داخل دفتر الأستاذ.
                            </div>
                        </div>
                        <div className="flex gap-2 justify-end">
                            <button
                                onClick={() => setConfirm(false)}
                                disabled={busy}
                                className="px-4 py-2 text-sm font-bold border border-border rounded-lg hover:bg-slate-50"
                                data-testid="cancel-correction"
                            >
                                إلغاء
                            </button>
                            <button
                                onClick={handleConfirm}
                                disabled={busy}
                                className="px-4 py-2 text-sm font-bold bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50"
                                data-testid="confirm-correction"
                            >
                                {busy ? "جارٍ التنفيذ..." : "تأكيد التصحيح"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
