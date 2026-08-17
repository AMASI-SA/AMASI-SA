import { useMemo, useState } from "react";
import { CheckCircle, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { toast } from "sonner";
import {
    approveAndExecuteMetaManagementProposal,
    previewMetaManagementMutation,
} from "../../services/metaIntegrationsV2";

function idempotencyKey(entityId, action, value) {
    const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    return `meta-${entityId}-${action}-${value}-${random}`.slice(0, 160);
}

export default function MetaEntityActions({
    accountId,
    entity,
    entityType,
    reviewer = false,
    onExecuted,
}) {
    const [action, setAction] = useState("update_status");
    const [status, setStatus] = useState(entity.status === "ACTIVE" ? "PAUSED" : "ACTIVE");
    const [amount, setAmount] = useState("");
    const [newName, setNewName] = useState("");
    const [proposal, setProposal] = useState(null);
    const [loading, setLoading] = useState(false);
    const [executing, setExecuting] = useState(false);

    const actions = useMemo(() => {
        const rows = [{ value: "update_status", label: "تشغيل/إيقاف" }];
        if (["campaign", "adset"].includes(entityType)) rows.push({ value: "update_budget", label: "تعديل الميزانية" });
        if (entityType === "adset") rows.push({ value: "update_bid", label: "تعديل Bid/Cost Cap" });
        if (entityType === "campaign") rows.push({ value: "clone_campaign", label: "نسخ كحملة جديدة" });
        return rows;
    }, [entityType]);

    async function createPreview() {
        if (reviewer || loading) return;
        setLoading(true);
        setProposal(null);
        try {
            const value = action === "update_status" ? status : action === "clone_campaign" ? newName : amount;
            const next = await previewMetaManagementMutation({
                account_id: accountId,
                entity_type: entityType,
                entity_id: entity.id,
                action,
                status: action === "update_status" ? status : null,
                amount_native: ["update_status", "clone_campaign"].includes(action) ? null : Number(amount),
                new_name: action === "clone_campaign" ? newName : null,
                idempotency_key: idempotencyKey(entity.id, action, value),
            });
            setProposal(next);
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error((typeof detail === "string" ? detail : detail?.message) || "تعذر تجهيز معاينة التعديل.");
        } finally {
            setLoading(false);
        }
    }

    async function execute() {
        if (!proposal?.proposal_id || executing || reviewer) return;
        setExecuting(true);
        try {
            const result = await approveAndExecuteMetaManagementProposal(proposal.proposal_id);
            if (result.status === "completed" && result.verified === true) {
                toast.success("نفّذت Meta التعديل وتحقق ميزان من القيمة الجديدة");
                setProposal(null);
                await onExecuted?.();
            } else {
                toast.warning("وصل التعديل إلى Meta لكن لم تكتمل مطابقة القراءة اللاحقة؛ راجع السجل.");
            }
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error((typeof detail === "string" ? detail : detail?.message) || "تعذر تنفيذ تعديل Meta.");
        } finally {
            setExecuting(false);
        }
    }

    if (reviewer) {
        return <div className="mt-2 text-[10px] font-bold text-slate-400">حساب المراجع: عرض فقط دون تنفيذ.</div>;
    }

    return (
        <div className="mt-2 rounded-md border border-slate-200 bg-white p-2" data-testid={`meta-actions-${entityType}-${entity.id}`}>
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                <select value={action} onChange={(event) => { setAction(event.target.value); setProposal(null); }} className="h-9 rounded-md border border-slate-200 px-2 text-[11px] font-bold">
                    {actions.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
                </select>
                {action === "update_status" ? (
                    <select value={status} onChange={(event) => { setStatus(event.target.value); setProposal(null); }} className="h-9 rounded-md border border-slate-200 px-2 text-[11px] font-bold">
                        <option value="ACTIVE">تشغيل</option>
                        <option value="PAUSED">إيقاف</option>
                    </select>
                ) : action === "clone_campaign" ? (
                    <input type="text" value={newName} onChange={(event) => { setNewName(event.target.value); setProposal(null); }} placeholder="اسم الحملة الجديدة" className="h-9 rounded-md border border-slate-200 px-2 text-[11px]" />
                ) : (
                    <input type="number" min="0.01" step="0.01" value={amount} onChange={(event) => { setAmount(event.target.value); setProposal(null); }} placeholder={action === "update_bid" ? "التكلفة بالريال" : "الميزانية بالريال"} className="h-9 rounded-md border border-slate-200 px-2 text-[11px]" />
                )}
                <button type="button" onClick={createPreview} disabled={loading || (["update_budget", "update_bid"].includes(action) && !(Number(amount) > 0)) || (action === "clone_campaign" && newName.trim().length < 3)} className="inline-flex h-9 items-center justify-center gap-1 rounded-md bg-violet-700 px-3 text-[11px] font-extrabold text-white disabled:opacity-50">
                    {loading && <SpinnerGap size={14} className="animate-spin" />}
                    معاينة
                </button>
            </div>

            {proposal && (
                <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
                    <div className="flex items-start gap-2">
                        <WarningCircle size={16} className="mt-0.5 shrink-0" weight="fill" />
                        <div>
                            <div className="font-extrabold">معاينة فقط — لم يتغير شيء في Meta بعد</div>
                            <div className="mt-1 font-mono text-[10px]">
                                {proposal.action === "clone_campaign"
                                    ? `نسخة كاملة متوقفة باسم: ${proposal.planned?.new_name}`
                                    : `${proposal.field}: ${String(proposal.before?.[proposal.field] ?? "—")} ← ${String(proposal.planned?.[proposal.field] ?? "—")}`}
                            </div>
                        </div>
                    </div>
                    <button type="button" onClick={execute} disabled={executing} className="mt-2 inline-flex min-h-8 items-center gap-1 rounded-md bg-emerald-700 px-3 font-extrabold text-white disabled:opacity-50">
                        {executing ? <SpinnerGap size={14} className="animate-spin" /> : <CheckCircle size={14} weight="fill" />}
                        موافقة وتنفيذ ثم تحقق
                    </button>
                </div>
            )}
        </div>
    );
}
