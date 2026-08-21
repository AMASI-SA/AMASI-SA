import { useMemo, useState } from "react";
import { Camera, CheckCircle, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { toast } from "sonner";
import { useOptionalAuth } from "../../context/AuthContext";

import {
    acknowledgeTrackingInstruction,
    completeTrackingInstruction,
    uploadTrackingInstructionEvidence,
} from "../../services/orderTrackingNotes";

const ENFORCEMENT_LABELS = {
    notice: "تنبيه",
    acknowledgement_required: "تأكيد الاطلاع إلزامي",
    completion_required: "تنفيذ إلزامي قبل الإكمال",
};

function applies(row, stage) {
    return Array.isArray(row?.target_stages) && row.target_stages.includes(stage);
}

export default function CustomerServiceInstructionBanner({
    instructions = [],
    stage,
    onUpdated,
}) {
    const { user } = useOptionalAuth() || {};
    const rows = useMemo(
        () => instructions.filter((row) => applies(row, stage)),
        [instructions, stage],
    );
    const [busy, setBusy] = useState("");
    const [files, setFiles] = useState({});
    const [results, setResults] = useState({});
    const [waiting, setWaiting] = useState({});
    if (!rows.length) return null;

    async function act(row, action) {
        if (busy) return;
        setBusy(row.id);
        try {
            const response = await action();
            if (response?.waiting_customer_service_approval) {
                setWaiting((current) => ({ ...current, [row.id]: true }));
                toast.success("أُرسل طلب الموافقة إلى خدمة العملاء، وبقيت المرحلة متوقفة.");
            } else {
                toast.success("تم تنفيذ تعليمات خدمة العملاء.");
            }
            onUpdated?.(response);
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error(detail?.message || detail?.code || error?.message || "تعذر تنفيذ التعليمات");
        } finally {
            setBusy("");
        }
    }

    return (
        <div className="space-y-2" data-testid="customer-service-stage-instructions">
            {rows.map((row) => {
                const requiredAction = row.required_action || "none";
                const needsPhotos = ["upload_photos", "upload_photos_and_result"].includes(requiredAction);
                const needsResult = ["write_result", "upload_photos_and_result"].includes(requiredAction);
                const awaitingApproval = waiting[row.id] || row.status === "waiting_customer_service_approval";
                const acknowledged = (row.acknowledged_by_ids || []).includes(user?.id);
                return (
                    <article key={row.id} className={`rounded-2xl border p-3 text-sm ${row.priority === "urgent" ? "border-rose-300 bg-rose-50 text-rose-950" : "border-amber-300 bg-amber-50 text-amber-950"}`}>
                        <div className="flex items-start gap-2">
                            <WarningCircle size={22} weight="fill" className="mt-0.5 shrink-0" />
                            <div className="min-w-0 flex-1">
                                <div className="font-black">تعليمات خدمة العملاء · {ENFORCEMENT_LABELS[row.enforcement] || "تنبيه"}</div>
                                {row.scope !== "order" && <div className="mt-1 text-[11px] font-black opacity-75">خاصة بـ {(row.target_ids || []).length || 1} {row.scope === "piece" ? "قطعة محددة" : "منتج محدد"}</div>}
                                <p className="mt-1 whitespace-pre-wrap font-bold leading-6">{row.note}</p>
                                {row.rejection_note && <div className="mt-2 rounded-xl border border-rose-200 bg-white p-2 text-xs font-black text-rose-900">ملاحظة خدمة العملاء بعد الرفض: {row.rejection_note}</div>}
                                {(row.delivery_date || row.delivery_time) && <div className="mt-1 text-xs font-black">الموعد: {row.delivery_date || ""} {row.delivery_time || ""}</div>}
                            </div>
                        </div>

                        {awaitingApproval ? (
                            <div className="mt-3 rounded-xl border border-violet-200 bg-white p-3 text-xs font-black text-violet-900">وصل الطلب إلى خدمة العملاء وهو بانتظار موافقتهم. لا يمكن تجاوز المرحلة الآن.</div>
                        ) : row.enforcement === "acknowledgement_required" && acknowledged ? (
                            <div className="mt-3 rounded-xl border border-emerald-200 bg-white p-3 text-xs font-black text-emerald-800"><CheckCircle className="ml-1 inline" /> تم تأكيد اطلاعك على التعليمات</div>
                        ) : row.enforcement === "acknowledgement_required" ? (
                            <button type="button" disabled={Boolean(busy)} onClick={() => act(row, () => acknowledgeTrackingInstruction(row.id))} className="mt-3 min-h-10 rounded-xl bg-amber-900 px-4 text-xs font-black text-white disabled:opacity-50">
                                {busy === row.id ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" />} اطلعت على التعليمات
                            </button>
                        ) : row.enforcement === "completion_required" ? (
                            <div className="mt-3 space-y-2">
                                {needsPhotos && <label className="flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-xl border border-amber-300 bg-white px-3 text-xs font-black"><Camera size={20} /> اختر صور الإثبات<input type="file" accept="image/jpeg,image/png,image/webp" multiple className="sr-only" onChange={(event) => setFiles((current) => ({ ...current, [row.id]: Array.from(event.target.files || []) }))} /></label>}
                                {files[row.id]?.length > 0 && <div className="text-xs font-black">تم اختيار {files[row.id].length} صورة</div>}
                                {needsResult && <textarea rows={2} value={results[row.id] || ""} onChange={(event) => setResults((current) => ({ ...current, [row.id]: event.target.value }))} placeholder="اكتب نتيجة التنفيذ" className="w-full rounded-xl border border-amber-300 bg-white p-3 text-xs font-bold" />}
                                <button
                                    type="button"
                                    disabled={Boolean(busy) || (needsPhotos && !files[row.id]?.length) || (needsResult && !results[row.id]?.trim())}
                                    onClick={() => act(row, () => needsPhotos
                                        ? uploadTrackingInstructionEvidence(row.id, files[row.id], results[row.id] || "")
                                        : completeTrackingInstruction(row.id, results[row.id] || "وصل المنتج أو الطلب إلى المرحلة المحددة"))}
                                    className="min-h-11 rounded-xl bg-rose-700 px-4 text-xs font-black text-white disabled:opacity-40"
                                >
                                    {busy === row.id ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" />}
                                    {row.approval_required ? "وصل عندي — إرسال طلب موافقة لخدمة العملاء" : "تنفيذ المطلوب وفتح المرحلة"}
                                </button>
                            </div>
                        ) : null}
                    </article>
                );
            })}
        </div>
    );
}
