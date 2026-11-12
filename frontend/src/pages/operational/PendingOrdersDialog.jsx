import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import PaginatedReportTable from "./PaginatedReportTable";
import { deliveryOrderLabel, formatPositionCount } from "./financialPositionPresentation";
import { formatRiyadhDateTime } from "../../lib/dates";
import { operationalApi, requestId } from "./api";
import { canReadOperationalPage } from "./visibleOperationalRead";
import "./financialPosition.css";

const confirmationLabel = "تأكيد التوصيل بعد بداية التشغيل";
const savedMessage = state => state === "applied" ? "تم تطبيق التأكيد في التقرير" : "تم حفظ التأكيد؛ بانتظار تحديث الأرصدة";
const text = value => typeof value === "string" && value.trim().length > 0;
const instant = value => text(value) ? Date.parse(value) : NaN;
const isSavedState = value => ["pending_reconciliation", "applied"].includes(value);
const validRecord = value => text(value?.id) && Number.isFinite(instant(value.confirmed_at)) && isSavedState(value.state);
const targetKey = target => JSON.stringify([target.order_id, target.order_number, target.party_type, target.party_id]);

function confirmationTarget(row) {
    const metadata = row?.delivery_after_start_confirmation;
    if (!text(row?.order_id) || !text(row?.order_number) || !text(metadata?.party_id)
        || !["courier", "store_driver"].includes(metadata?.party_type)) return null;
    return { order_id: row.order_id, order_number: row.order_number, party_type: metadata.party_type, party_id: metadata.party_id };
}

function savedFor(row, local, shared) {
    const metadata = row?.delivery_after_start_confirmation;
    const fromReport = isSavedState(metadata?.state) ? { id: metadata.confirmation_id, confirmed_at: metadata.confirmed_at, state: metadata.state } : null;
    return [fromReport, local, shared].find(record => record?.state === "applied") || fromReport || local || shared;
}

function validPreview(value, scope, periodStart) {
    return value?.session_scope === scope && Number.isFinite(instant(periodStart))
        && instant(value.period_start) === instant(periodStart)
        && typeof value.can_confirm === "boolean"
        && ["available", "pending_reconciliation", "applied", "unavailable"].includes(value.confirmation_state)
        && Array.isArray(value.issues) && value.issues.every(issue => text(issue?.code) && text(issue.message))
        && (value.recorded_confirmation == null || validRecord(value.recorded_confirmation))
        && (value.recorded_confirmation ? !value.can_confirm && value.confirmation_state === value.recorded_confirmation.state
            : !isSavedState(value.confirmation_state))
        && (!value.can_confirm || (value.confirmation_state === "available" && !value.recorded_confirmation
            && /^[a-f0-9]{64}$/.test(value.preview_hash || "")));
}

export function distinctPendingOrders(details = []) {
    const orders = new Map();
    for (const item of Array.isArray(details) ? details : []) {
        const id = item?.order_id || item?.order_number;
        if (!id) continue;
        const key = String(id);
        const existing = orders.get(key);
        const row = existing || { ...item, order_id: item.order_id, reasons: [] };
        if (existing && item.delivery_after_start_confirmation) {
            const previous = row.delivery_after_start_confirmation, next = item.delivery_after_start_confirmation;
            if (row.order_number && item.order_number && row.order_number !== item.order_number
                || previous && (previous.party_type !== next.party_type || previous.party_id !== next.party_id)) {
                row.delivery_after_start_confirmation = { can_confirm: false, state: "unavailable" };
            } else if (!previous || isSavedState(next.state)) {
                row.delivery_after_start_confirmation = previous?.state === "applied" ? previous : next;
            } else if (next.can_confirm !== true || next.state !== "available") {
                row.delivery_after_start_confirmation = { ...previous, can_confirm: false };
            }
        }
        if (!row.order_number && item.order_number) row.order_number = item.order_number;
        for (const reason of item.reasons || []) {
            if (reason?.message && !row.reasons.some(existing => existing.code === reason.code && existing.message === reason.message)) {
                row.reasons.push(reason);
            }
        }
        orders.set(key, row);
    }
    return [...orders.values()];
}

export default function PendingOrdersDialog({ count, details, name = "هذه الجهة", confirmationControl }) {
    const { allowed = false, sessionScope, periodStart, recordedConfirmations = {}, onSaved } = confirmationControl || {};
    const contextKey = JSON.stringify([sessionScope || "", periodStart || ""]);
    const [opening, setOpening] = useState(null);
    const open = opening === contextKey;
    const [search, setSearch] = useState("");
    const [actions, setActions] = useState({}), [busyKey, setBusyKey] = useState(null);
    const [active, setActive] = useState(canReadOperationalPage);
    const dialogRef = useRef(null), triggerRef = useRef(null);
    const lifetime = useRef({ alive: true, generation: 0 }), pending = useRef(null), localRecords = useRef({});
    const current = useRef(null);
    const allRows = useMemo(() => distinctPendingOrders(details), [details]);
    const rows = useMemo(() => allRows.filter(row => !search.trim() ||
        [row.order_number, row.order_id].some(value => String(value || "").includes(search.trim()))), [allRows, search]);
    current.current = { contextKey, allowed, sessionScope, periodStart, open, allRows, recordedConfirmations, onSaved };

    const cancelPreview = useCallback(() => {
        lifetime.current.generation += 1;
        const request = pending.current;
        if (request?.stage !== "preview") return;
        request.controller.abort();
        pending.current = null;
        if (lifetime.current.alive) {
            setBusyKey(null);
            setActions(previous => ({ ...previous, [request.key]: { error: "توقفت المعاينة؛ أعد المحاولة من النافذة الظاهرة." } }));
        }
    }, []);

    useEffect(() => {
        lifetime.current.alive = true;
        const activity = () => {
            const visible = canReadOperationalPage();
            setActive(visible);
            if (!visible) cancelPreview();
        };
        document.addEventListener("visibilitychange", activity);
        window.addEventListener("online", activity);
        window.addEventListener("offline", activity);
        return () => {
            lifetime.current.alive = false;
            cancelPreview();
            document.removeEventListener("visibilitychange", activity);
            window.removeEventListener("online", activity);
            window.removeEventListener("offline", activity);
        };
    }, [cancelPreview]);

    useEffect(() => {
        cancelPreview();
        pending.current = null;
        localRecords.current = {};
        setOpening(null); setSearch(""); setActions({}); setBusyKey(null);
    }, [contextKey, cancelPreview]);
    useEffect(() => { if (!allowed) cancelPreview(); }, [allowed, cancelPreview]);

    const confirmDelivery = async row => {
        const target = confirmationTarget(row);
        if (!target || pending.current) return;
        const key = targetKey(target), metadata = row.delivery_after_start_confirmation;
        if (!allowed || !text(sessionScope) || !Number.isFinite(instant(periodStart)) || !open || !canReadOperationalPage()
            || metadata.can_confirm !== true || metadata.state !== "available"
            || savedFor(row, localRecords.current[key], recordedConfirmations[key]) || actions[key]?.blocked) return;
        const request = { key, target, contextKey, generation: lifetime.current.generation, stage: "preview", controller: new AbortController() };
        const stillRelevant = () => lifetime.current.alive && current.current.contextKey === request.contextKey;
        const canContinue = () => stillRelevant() && pending.current === request
            && lifetime.current.generation === request.generation && current.current.open && current.current.allowed
            && !request.controller.signal.aborted && canReadOperationalPage();
        const stillEligible = () => {
            const latest = current.current.allRows.find(item => {
                const identity = confirmationTarget(item);
                return identity && targetKey(identity) === key;
            });
            return canContinue() && latest?.delivery_after_start_confirmation?.can_confirm === true
                && latest.delivery_after_start_confirmation.state === "available"
                && !savedFor(latest, localRecords.current[key], current.current.recordedConfirmations[key]);
        };
        const remember = record => {
            localRecords.current[key] = record;
            setActions(previous => ({ ...previous, [key]: { record } }));
        };
        const body = { ...target, expected_session_scope: sessionScope, visible: true };
        let recorded = null;
        pending.current = request; setBusyKey(key); setActions(previous => ({ ...previous, [key]: {} }));
        try {
            const preview = await operationalApi.previewDeliveryAfterStart(body, { signal: request.controller.signal });
            if (!stillEligible()) return;
            if (!validPreview(preview, sessionScope, periodStart)) throw new Error("تغيرت بيانات الطلب أو الجلسة؛ حدّث التقرير قبل إعادة المحاولة.");
            if (preview.recorded_confirmation) { remember(preview.recorded_confirmation); return; }
            if (!preview.can_confirm || preview.confirmation_state !== "available") {
                setActions(previous => ({ ...previous, [key]: { blocked: true,
                    error: preview.issues.map(issue => issue.message).join("؛ ") || "التأكيد غير متاح لهذا الطلب؛ حدّث التقرير وراجع السبب." } }));
                return;
            }
            if (!stillEligible()) return;
            const id = requestId();
            if (!text(id)) throw new Error("تعذر تجهيز التأكيد؛ حدّث التقرير قبل إعادة المحاولة.");
            request.stage = "confirm";
            // A submitted proof is an explicit write. Closing or hiding cancels
            // its follow-up presentation, never the write or an automatic retry.
            const result = await operationalApi.confirmDeliveryAfterStart({ ...body, request_id: id,
                expected_preview_hash: preview.preview_hash, confirmed_after_start: true });
            if (!stillRelevant()) return;
            if (result?.ok !== true || result.session_scope !== sessionScope
                || result.order_id !== target.order_id || result.order_number !== target.order_number || !validRecord(result.recorded_confirmation)) {
                throw new Error("تعذر التحقق من حفظ التأكيد؛ حدّث التقرير قبل إعادة المحاولة.");
            }
            if (result.pending_reconciliation !== (result.recorded_confirmation.state === "pending_reconciliation")) {
                throw new Error("تعذر التحقق من حفظ التأكيد؛ حدّث التقرير قبل إعادة المحاولة.");
            }
            recorded = result.recorded_confirmation;
            remember(recorded);
            if (canContinue()) await current.current.onSaved?.({ ...target, ...recorded, session_scope: sessionScope }, { refreshAllowed: true });
        } catch (error) {
            if (!stillRelevant() || (request.stage === "preview" && request.controller.signal.aborted)) return;
            const denied = [401, 403].includes(error?.response?.status);
            const message = recorded ? "تم حفظ التأكيد، لكن تعذر تحديث الأرصدة؛ حدّث التقرير عند الحاجة."
                : request.stage === "confirm" ? error?.response?.data?.detail?.message || "تعذر التحقق من حفظ التأكيد؛ حدّث التقرير قبل إعادة المحاولة."
                    : error?.response?.data?.detail?.message || error?.message || "تعذرت المعاينة؛ حدّث التقرير قبل إعادة المحاولة.";
            setActions(previous => ({ ...previous, [key]: { ...(recorded ? { record: recorded } : {}), blocked: denied || request.stage === "confirm" || error instanceof Error, error: message } }));
        } finally {
            if (pending.current === request) {
                pending.current = null;
                if (stillRelevant()) setBusyKey(null);
            }
        }
    };
    useEffect(() => {
        const dialog = dialogRef.current;
        if (!open || !dialog) return;
        if (typeof dialog.showModal === "function") dialog.showModal();
        else dialog.setAttribute("open", "");
        dialog.querySelector("input")?.focus();
        return () => { if (typeof dialog.close === "function") dialog.close(); triggerRef.current?.focus(); };
    }, [open, count]);
    const close = () => { cancelPreview(); setOpening(null); setSearch(""); };
    if (!Number.isSafeInteger(count) || count <= 0) return <bdi>{formatPositionCount(count)}</bdi>;
    return <>
        <button ref={triggerRef} type="button" className="ofp-pending-orders" onClick={() => setOpening(contextKey)} aria-label={`${count} طلبات غير مؤكدة — ${name}`}>
            <bdi>{formatPositionCount(count)}</bdi> طلبات غير مؤكدة
        </button>
        {open && createPortal(<dialog ref={dialogRef} className="ofp-order-review ofp-no-print" dir="rtl" role="dialog" aria-modal="true" aria-label={`طلبات غير مؤكدة — ${name}`}
            onCancel={event => { event.preventDefault(); close(); }} onClick={event => { if (event.target === event.currentTarget) close(); }}>
            <div><div className="ofp-review-header"><h2>طلبات غير مؤكدة — {name}</h2>
                    <button type="button" className="ofp-button" aria-label="إغلاق نافذة الطلبات" onClick={close}>إغلاق</button></div>
                <p>عدد الطلبات التي تحتاج مراجعة: <bdi>{formatPositionCount(count)}</bdi>. قد يكون للطلب أكثر من سبب، ويُحسب مرة واحدة. تبقى المبالغ المثبتة ضمن الرصيد المحتسب.</p>
                <label className="ofp-review-search">البحث برقم الطلب
                    <input value={search} type="search" onChange={event => setSearch(event.target.value)} aria-label="البحث في الطلبات غير المؤكدة" />
                </label>
                <PaginatedReportTable rows={rows} pageSize={10} label="الطلبات غير المؤكدة" resetKey={search}>
                    {page => <div className="ofp-table-scroll"><table><thead><tr><th scope="col">رقم الطلب</th><th scope="col">سبب عدم التأكد</th></tr></thead>
                        <tbody>{page.map((row, index) => {
                            const target = confirmationTarget(row), key = target && targetKey(target);
                            const action = actions[key] || {}, metadata = row.delivery_after_start_confirmation;
                            const record = savedFor(row, action.record, recordedConfirmations[key]);
                            const canConfirm = allowed && text(sessionScope) && Number.isFinite(instant(periodStart)) && target
                                && metadata.can_confirm === true && metadata.state === "available" && !record && !action.blocked;
                            return <tr key={row.order_id || row.order_number || index}>
                            <td><bdi>{deliveryOrderLabel(row)}</bdi></td>
                            <td>{row.reasons.length ? row.reasons.map((reason, reasonIndex) => <p key={`${reason.code}:${reasonIndex}`}>{reason.message}</p>) : "سبب المراجعة غير متوفر في هذه اللقطة."}
                                {canConfirm && <button type="button" className="ofp-button" aria-label={confirmationLabel} disabled={busyKey !== null || !active}
                                    onClick={() => confirmDelivery(row)}>{confirmationLabel}</button>}
                                {busyKey === key && <p role="status">جارٍ التحقق وحفظ التأكيد…</p>}
                                {record && <p role="status">{savedMessage(record.state)}{record.confirmed_at && <> · وقت التسجيل: <bdi>{formatRiyadhDateTime(record.confirmed_at)}</bdi></>}</p>}
                                {action.error && <p role="alert">{action.error}</p>}
                            </td>
                        </tr>; })}</tbody></table></div>}
                </PaginatedReportTable>
                {!rows.length && <p role="status">{allRows.length ? "لا توجد طلبات تطابق البحث." : "تفاصيل الطلبات غير متوفرة في هذه اللقطة؛ حدّث التقرير لقراءة أحدث تفاصيل محفوظة."}</p>}
                {allRows.length !== count && <p role="status">تفاصيل متاحة لـ{allRows.length} من {count} طلبًا. لم تُعتبر بقية الطلبات مكتملة.</p>}
            </div></dialog>, document.body)}
    </>;
}
