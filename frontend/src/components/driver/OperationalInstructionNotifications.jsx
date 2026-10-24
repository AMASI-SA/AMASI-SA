import { useCallback, useEffect, useRef, useState } from "react";
import api from "../../lib/api";
import InstructionTaskComments from "./InstructionTaskComments";
import { enableOperationalPush, disableOperationalPush, supportsOperationalPush } from "../../lib/operationalPush";

const labels = { active: "إجراء مطلوب", waiting_customer_service_approval: "بانتظار موافقة خدمة العملاء", completed: "مكتملة", cancelled: "ملغاة", canceled: "ملغاة" };
const isNote = (row) => ["general", "urgent_preparation", "delivery_instruction"].includes(row?.action_type);
const isRemoval = (row) => ["delete_product", "cancel_order"].includes(row?.action_type);
const stages = { pending_review: "انتظار المراجعة", reviewed: "تمت المراجعة", preparation: "التجهيز", supplier_dispatch: "الإرسال للمورد", supplier_receiving: "استلام المورد", preparation_receiving: "استلام التجهيز", assembly_labeling: "التجميع والعنونة", carrier_handoff: "تسليم شركة الشحن", store_courier: "مندوب التوصيل" };
function InstructionSummary({ instruction }) {
  if (!instruction) return null;
  return <div className="mt-1 text-xs"><span className={instruction.priority === "urgent" ? "font-black text-rose-800" : "text-slate-600"}>{instruction.priority === "urgent" ? "مستعجل" : instruction.priority === "high" ? "مهم" : "عادي"}</span> · {(instruction.target_stages || []).map((stage) => stages[stage] || stage).join("، ")}<div>{(instruction.tasks || []).map((task) => task.product_name).filter(Boolean).join("، ")}</div></div>;
}

export default function OperationalInstructionNotifications({ instructionId = "", detailOnly = false }) {
  const [items, setItems] = useState([]);
  const [listStatus, setListStatus] = useState("active");
  const [activeCount, setActiveCount] = useState(0);
  const [nextCursor, setNextCursor] = useState(null);
  const [detail, setDetail] = useState(null);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");
  const [pushMessage, setPushMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [taskIds, setTaskIds] = useState([]);
  const [resultNote, setResultNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [evidenceFiles, setEvidenceFiles] = useState([]);
  const [evidenceCount, setEvidenceCount] = useState(0);
  const generation = useRef(0);
  const listGeneration = useRef(0);
  const live = useRef(true);
  const refresh = useCallback(async (cursor = null) => {
    const request = ++listGeneration.current;
    try {
      const { data } = await api.get("/order-tracking-notes/instructions/inbox", { params: { status: listStatus, ...(typeof cursor === "string" && cursor ? { cursor } : {}) } });
      if (live.current && request === listGeneration.current) { setItems((old) => typeof cursor === "string" && cursor ? [...old, ...(data.items || [])].filter((item, index, rows) => rows.findIndex((row) => row.id === item.id) === index) : data.items || []); setActiveCount(Number(data.active_count) || 0); setNextCursor(data.next_cursor || null); setError(""); }
    } catch { if (live.current && request === listGeneration.current) setError("تعذر تحديث التنبيهات. أعد المحاولة للاطلاع على الحالة الحالية."); }
  }, [listStatus]);
  const open = useCallback(async (id, notificationId) => {
    const request = ++generation.current;
    setDetail(null); setEvents([]); setError(""); setTaskIds([]); setResultNote(""); setConfirmed(false); setAcknowledged(false); setEvidenceFiles([]); setEvidenceCount(0);
    try {
      const { data } = await api.get(`/order-tracking-notes/instructions/${encodeURIComponent(id)}`);
      if (!live.current || request !== generation.current) return;
      setDetail({ ...data.instruction, can_approve_resume: data.capabilities?.can_approve_resume === true, can_close_instruction: data.capabilities?.can_close_instruction === true, can_submit_tasks: data.capabilities?.can_submit_tasks === true, can_claim_task: data.capabilities?.can_claim_task === true, can_acknowledge: data.capabilities?.can_acknowledge === true, can_submit_evidence: data.capabilities?.can_submit_evidence === true });
      setEvidenceCount((data.evidence || []).filter((item) => item.status === "submitted").length);
      const currentNoteTasks = (data.instruction?.tasks || []).filter((task) => task.current);
      setAcknowledged(currentNoteTasks.length > 0 && currentNoteTasks.every((task) => Boolean(task.acknowledged_at)));
      setEvents(data.events || []);
      if (notificationId) {
        await api.post(`/order-tracking-notes/notifications/${encodeURIComponent(notificationId)}/read`);
        if (live.current) setItems((rows) => rows.map((row) => row.id === notificationId ? { ...row, read: true } : row));
      }
    } catch { if (live.current && request === generation.current) setError("تعذر فتح التعليمات؛ قد يكون الإسناد تغيّر. حدّث التنبيهات."); }
  }, []);
  useEffect(() => {
    live.current = true;
    if (!detailOnly) refresh();
    const id = instructionId || new URLSearchParams(window.location.search).get("instruction_id");
    if (id && /^[a-zA-Z0-9_-]{1,120}$/.test(id)) open(id);
    const onFocus = () => { if (!detailOnly) refresh(); if (id) open(id); else setDetail(null); };
    const onVisibility = () => { if (document.visibilityState === "visible") onFocus(); };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    const interval = window.setInterval(() => { if (!detailOnly) refresh(); }, 30000);
    return () => { live.current = false; generation.current++; window.clearInterval(interval); window.removeEventListener("focus", onFocus); document.removeEventListener("visibilitychange", onVisibility); };
  }, [refresh, open, instructionId, detailOnly]);
  async function changePush(enable) {
    setBusy(true); setPushMessage("");
    try { await (enable ? enableOperationalPush() : disableOperationalPush()); setPushMessage(enable ? "تم تفعيل إشعارات الهاتف لهذا الحساب." : "تم إيقاف إشعارات الهاتف على هذا الجهاز."); }
    catch (failure) { setPushMessage(failure.response ? "تعذر حفظ إعداد إشعارات الهاتف. حاول مرة أخرى." : failure.message); }
    finally { setBusy(false); }
  }
  async function submitTasks() {
    if (!detail || !confirmed || !taskIds.length || !resultNote.trim()) return;
    setSubmitting(true); setError("");
    const removal = ["cancel_order", "delete_product"].includes(detail.action_type);
    try {
      await api.post(`/order-tracking-notes/instructions/${encodeURIComponent(detail.id)}/tasks/submit`, { task_ids: taskIds, note: resultNote.trim(), confirmation: removal ? "removed_from_preparation" : "modified" });
      await open(detail.id); if (!detailOnly) await refresh();
    } catch { setError("تعذر تأكيد التنفيذ. حدّث التفاصيل للتحقق من الإسناد والحالة ثم أعد المحاولة."); }
    finally { setSubmitting(false); }
  }
  async function acknowledgeNote() {
    if (!detail || submitting) return;
    setSubmitting(true);
    try { await api.post(`/order-tracking-notes/instructions/${encodeURIComponent(detail.id)}/acknowledge`); setAcknowledged(true); }
    catch { setError("تعذر تأكيد وضوح الملاحظة. حاول مرة أخرى."); }
    finally { setSubmitting(false); }
  }
  async function claimTask() {
    if (!detail || submitting) return;
    setSubmitting(true);
    try { await api.post(`/order-tracking-notes/instructions/${encodeURIComponent(detail.id)}/tasks/claim`); await open(detail.id); }
    catch { setError("تعذر استلام مهمة المراجعة؛ ربما استلمها موظف آخر. حدّث التفاصيل."); }
    finally { setSubmitting(false); }
  }
  async function resolveInstruction(action) {
    if (!detail || submitting) return;
    if (action === "complete" ? !detail.can_close_instruction : !detail.can_approve_resume) return;
    if (!confirmed || (action === "reject" && (!taskIds.length || !resultNote.trim()))) return;
    setSubmitting(true); setError("");
    try {
      await api.post(`/order-tracking-notes/instructions/${encodeURIComponent(detail.id)}/${action}`, {note: resultNote.trim(), ...(action === "reject" ? {task_ids: taskIds} : {})});
      await open(detail.id); if (!detailOnly) await refresh();
    } catch {setError("تعذر تسجيل القرار. حدّث التفاصيل للتحقق من الحالة والصلاحية ثم أعد المحاولة.");}
    finally {setSubmitting(false);}
  }
  async function uploadEvidence() {
    if (!detail || submitting || !evidenceFiles.length) return;
    if (evidenceFiles.length > 8 || evidenceFiles.some((file) => file.size > 8 * 1024 * 1024 || !["image/jpeg", "image/png", "image/webp"].includes(file.type))) { setError("اختر حتى 8 صور JPEG أو PNG أو WebP، بحد 8 ميجابايت للصورة."); return; }
    setSubmitting(true); setError("");
    const body = new FormData(); evidenceFiles.forEach((file) => body.append("files", file)); body.append("result_note", resultNote.trim());
    try { const { data } = await api.post(`/order-tracking-notes/instructions/${encodeURIComponent(detail.id)}/evidence`, body, { headers: { "Content-Type": "multipart/form-data" } }); setEvidenceCount((count) => count + (data.evidence || []).length); setEvidenceFiles([]); }
    catch { setError("تعذر رفع صور الإثبات؛ لم يتم تأكيد التنفيذ. أعد المحاولة."); }
    finally { setSubmitting(false); }
  }
  return <section className="rounded-3xl border bg-white p-4" aria-label="تنبيهات تعليمات الطلبات">
    {!detailOnly && <><div className="flex items-center justify-between gap-2"><h2 className="font-black">تنبيهات تعليمات الطلبات <span aria-label="عدد الطلبات النشطة">({activeCount})</span></h2><button onClick={() => refresh()} className="rounded-xl border px-3 py-2 text-xs">تحديث التنبيهات</button></div><div className="mt-3 flex gap-2" role="tablist"><button role="tab" aria-selected={listStatus === "active"} onClick={() => { setItems([]); setListStatus("active"); }} className="rounded border px-3 py-2">نشطة</button><button role="tab" aria-selected={listStatus === "completed"} onClick={() => { setItems([]); setListStatus("completed"); }} className="rounded border px-3 py-2">مكتملة</button></div></>}
    {error && <p role="alert" className="mt-2 text-sm text-rose-800">{error}</p>}
    <div className="mt-3 space-y-2">{items.map((item) => <button key={item.id} onClick={() => open(item.id)} className="block w-full rounded-xl border border-rose-200 bg-rose-50 p-3 text-right"><b>{item.action_label || "تعليمات الطلب"}</b><div className="text-sm">طلب #{item.order_number} · {item.activation_state === "scheduled" ? "بانتظار المرحلة المحددة" : labels[item.status] || "افتح للاطلاع على الحالة الحالية"}</div><InstructionSummary instruction={item} /><div className="text-xs">{item.note}</div></button>)}</div>
    {!detailOnly && nextCursor && <button onClick={() => refresh(nextCursor)} className="mt-2 rounded border px-3 py-2 text-sm">عرض المزيد</button>}
    {!detailOnly && !items.length && !error && <p className="mt-2 text-sm text-slate-500">{listStatus === "active" ? "لا توجد طلبات نشطة." : "لا توجد طلبات مكتملة."}</p>}
    {detail && <article className="mt-3 rounded-xl border border-amber-300 bg-amber-50 p-3" aria-label="تفاصيل التعليمات الحالية">
      <div className="font-black">طلب #{detail.order_number} · {detail.activation_state === "scheduled" ? "بانتظار الوصول للمرحلة المحددة" : labels[detail.status] || detail.status}</div><InstructionSummary instruction={detail} /><p className="mt-2 whitespace-pre-wrap">السبب والتعليمات: {detail.note}</p>{detail.created_by_name && <p className="mt-1 text-xs">{isNote(detail) ? "أرسلها" : "أوقفه"}: {detail.created_by_name}</p>}{detail.rejection_note && <p className="mt-2 text-sm text-rose-800">سبب إعادة المهمة: {detail.rejection_note}</p>}
      {(detail.proposed_changes || []).map((change, index) => <div key={index} className="mt-2 rounded border bg-white p-2 text-sm"><b>{change.field}</b><div>السابق: {String(change.previous ?? "—")}</div><div>المطلوب: {String(change.requested ?? "—")}</div></div>)}
      {events.length > 0 && <details className="mt-2 rounded border p-2"><summary className="text-sm font-bold">آخر تحديثات الطلب</summary>{events.map((event) => <div key={event.id} className="mt-2 text-xs"><b>{event.title}</b><p>{event.message}</p>{event.metadata?.product_name && <p>{event.metadata.product_name} · {event.metadata.employee_name || ""}</p>}{event.metadata?.current?.stage && <p>المرحلة: {stages[event.metadata.current.stage] || event.metadata.current.stage}</p>}</div>)}</details>}
      {isNote(detail) && detail.activation_state !== "scheduled" && <div className="mt-2 text-sm"><p>ملاحظة فقط؛ لا توقف التجهيز أو الانتقال للمراحل.</p>{acknowledged ? <p>تم تأكيد وضوح الملاحظة.</p> : <button disabled={submitting || !detail.can_acknowledge} onClick={acknowledgeNote} className="mt-2 rounded border px-3 py-2">الملاحظة واضحة وسيتم تنفيذها</button>}</div>}
      {detail.activation_state !== "scheduled" && detail.can_claim_task && <button disabled={submitting} onClick={claimTask} className="mt-3 rounded border px-3 py-2 text-sm">استلام مسؤولية تنفيذ مهمة المراجعة</button>}
      <div className="mt-3 space-y-2">{(detail.tasks || []).map((task) => <label key={task.id} className="block rounded border bg-white p-2 text-sm"><input type="checkbox" disabled={submitting || !(detail.can_approve_resume ? task.status === "submitted" : task.status === "pending" && detail.status === "active" && detail.can_submit_tasks === true)} checked={taskIds.includes(task.id)} onChange={(event) => { setConfirmed(false); setTaskIds((ids) => event.target.checked ? [...ids, task.id] : ids.filter((id) => id !== task.id)); }} /> {task.product_name || "الطلب المسند إليك"} {task.status === "submitted" ? "— تم إرسال تأكيد التنفيذ" : "— بانتظار تنفيذك"}</label>)}</div>
      {(detail.tasks || []).map((task) => <div key={`comments-${task.id}`}><p className="mt-3 text-sm font-bold">{task.product_name || "مهمة الطلب"}</p><InstructionTaskComments instructionId={detail.id} taskId={task.id} /></div>)}
      {["edit_product", "edit_order", "delete_product", "cancel_order"].includes(detail.action_type) && detail.activation_state !== "scheduled" && detail.status === "active" && detail.can_submit_tasks === true && (detail.tasks || []).some((task) => task.status === "pending") && <div className="mt-3 space-y-2"><textarea aria-label="توضيح التنفيذ" value={resultNote} disabled={submitting} onChange={(event) => { setResultNote(event.target.value); setConfirmed(false); }} placeholder="اكتب ما نفذته فعليًا" className="w-full rounded border p-2" />{["upload_photos", "upload_photos_and_result"].includes(detail.required_action) && <div className="space-y-2 rounded border p-2"><label className="block text-sm">صور إثبات التنفيذ<input aria-label="صور إثبات التنفيذ" type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={submitting || !detail.can_submit_evidence} onChange={(event) => { setEvidenceFiles(Array.from(event.target.files || [])); setConfirmed(false); }} /></label><button disabled={submitting || !detail.can_submit_evidence || !evidenceFiles.length || (detail.required_action === "upload_photos_and_result" && !resultNote.trim())} onClick={uploadEvidence} className="rounded border px-3 py-2 text-sm">رفع صور الإثبات</button><p className="text-xs">عدد صور الإثبات المحفوظة: {evidenceCount}</p></div>}<label className="block text-sm"><input type="checkbox" disabled={submitting} checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> {["cancel_order", "delete_product"].includes(detail.action_type) ? "أؤكد إزالة المنتجات المحددة من التجهيز فعليًا" : "أؤكد تنفيذ التعديل المطلوب على المنتجات المحددة فعليًا"}</label><button disabled={submitting || !confirmed || !taskIds.length || !resultNote.trim() || (["upload_photos", "upload_photos_and_result"].includes(detail.required_action) && evidenceCount === 0)} onClick={submitTasks} className="rounded-xl bg-emerald-700 px-3 py-2 text-sm font-bold text-white disabled:opacity-40">إرسال تأكيد التنفيذ</button></div>}
      {(detail.can_approve_resume || detail.can_close_instruction) && <div className="mt-3 space-y-2 rounded border bg-white p-3">
        <p className="text-sm">{isRemoval(detail) ? "اعتماد التأكيد يغلق مهمة المتابعة ويرفع إيقافها فقط؛ الإلغاء النهائي يتم من سلة. إلغاء منتج لا يلغي الطلب كاملًا." : "القرار لصاحب التعليمات بعد الاتفاق مع الموظف."}</p>
        <textarea aria-label="ملاحظة صاحب التعليمات" value={resultNote} disabled={submitting} onChange={(event) => {setResultNote(event.target.value); setConfirmed(false);}} placeholder="ملاحظتك أو سبب إعادة التنفيذ" className="w-full rounded border p-2" />
        <label className="block text-sm"><input type="checkbox" checked={confirmed} disabled={submitting} onChange={(event) => setConfirmed(event.target.checked)} /> راجعت رد الموظف وأؤكد القرار</label>
        {detail.can_close_instruction && <button disabled={submitting || !confirmed} onClick={() => resolveInstruction("complete")} className="rounded border px-3 py-2">إنهاء التعليمات بعد الاتفاق</button>}
        {detail.can_approve_resume && <><button disabled={submitting || !confirmed} onClick={() => resolveInstruction("approve")} className="rounded border px-3 py-2">{isRemoval(detail) ? "اعتماد التأكيد وإغلاق المهمة" : "الموافقة واستكمال التجهيز"}</button><button disabled={submitting || !confirmed || !taskIds.length || !resultNote.trim()} onClick={() => resolveInstruction("reject")} className="rounded border px-3 py-2">إعادة القطع المحددة للتنفيذ مع استمرار الإيقاف</button></>}
      </div>}
      <p className="mt-2 text-xs">{isNote(detail) ? "التأكيد يسجل وضوح الملاحظة فقط ولا يوقف المسار." : "فتح التنبيه لا يعني تنفيذ التعليمات أو فك الإيقاف. يبقى الطلب بانتظار تأكيد التنفيذ والمراجعة."}</p><button disabled={submitting} onClick={() => { generation.current++; setDetail(null); }} className="mt-2 rounded border px-3 py-1">إغلاق التفاصيل</button>
    </article>}
    {!detailOnly && <div className="mt-3 flex flex-wrap gap-2">{supportsOperationalPush() ? <><button disabled={busy} onClick={() => changePush(true)} className="rounded-xl bg-emerald-700 px-3 py-2 text-xs font-bold text-white">تفعيل إشعارات الهاتف</button><button disabled={busy} onClick={() => changePush(false)} className="rounded-xl border px-3 py-2 text-xs">إيقاف إشعارات الهاتف</button></> : <p className="text-xs text-slate-500">إشعارات الهاتف غير مدعومة هنا. التنبيهات داخل التطبيق متاحة.</p>}</div>}
    {pushMessage && <p role="status" className="mt-2 text-sm">{pushMessage}</p>}
  </section>;
}
