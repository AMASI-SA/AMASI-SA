import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { WarningCircle } from "@phosphor-icons/react";
import api from "../lib/api";
import { useOptionalAuth } from "../context/AuthContext";

export function validSummary(value) {
    return value?.source === "local_invoice_mirror" && Number.isInteger(value.count)
        && value.count >= 0 && Number.isFinite(Date.parse(value.updated_at));
}
const dateLabel = value => Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString("ar-SA", { timeZone: "Asia/Riyadh" }) : "غير متوفر";

export default function QoyodUnsentHeaderAlert() {
    const user = useOptionalAuth()?.user;
    const ownerId = user?.role === "owner" ? user.id : null;
    const [state, setState] = useState({ ownerId: null, summary: null, error: "", busy: false });
    const active = useRef(null);
    const request = useRef(null);
    const summary = state.ownerId === ownerId ? state.summary : null;
    const busy = state.ownerId === ownerId && state.busy;
    const error = state.ownerId === ownerId ? state.error : "";

    useEffect(() => {
        const session = {};
        active.current = session;
        setState({ ownerId, summary: null, error: "", busy: false });
        if (!ownerId) return;
        const controller = new AbortController();
        request.current = controller;
        // One small saved-document read on mount. No timers, provider sync,
        // visibility refresh, route refresh or automatic retry.
        api.get("/integrations/qoyod/header-count", { signal: controller.signal }).then(({ data }) => {
            if (!controller.signal.aborted && active.current === session) {
                setState({ ownerId, summary: validSummary(data.local_summary) ? data.local_summary : null, error: "", busy: false });
            }
        }).catch(() => {
            if (!controller.signal.aborted && active.current === session)
                setState({ ownerId, summary: null, error: "تعذر قراءة آخر عدد محفوظ", busy: false });
        });
        return () => { active.current = null; request.current?.abort(); controller.abort(); };
    }, [ownerId]);

    const refresh = async () => {
        if (!ownerId || request.current?.manual) return;
        request.current?.abort(); // late mount response cannot replace this result
        const controller = new AbortController();
        controller.manual = true;
        request.current = controller;
        const session = active.current;
        setState(previous => ({ ...previous, ownerId, busy: true, error: "" }));
        try {
            const { data } = await api.post("/integrations/qoyod/header-count/refresh", null, { signal: controller.signal });
            if (!validSummary(data.local_summary)) throw new Error("incomplete summary");
            if (!controller.signal.aborted && active.current === session)
                setState({ ownerId, summary: data.local_summary, busy: false, error: "" });
        } catch (err) {
            if (!controller.signal.aborted && active.current === session)
                setState(previous => ({ ...previous, busy: false, error: err.response?.status === 429
                    ? "يوجد تحديث جارٍ أو حديث؛ أعد المحاولة بعد دقيقة"
                    : "تعذر تحديث العدد؛ آخر نتيجة محفوظة لم تتغير" }));
        } finally {
            if (request.current === controller) controller.manual = false;
        }
    };
    if (!ownerId) return null;
    return <div className="border-b border-amber-400 bg-amber-100 text-amber-950" dir="rtl" role="status" aria-live="polite" data-testid="qoyod-unsent-header-alert">
        <div className="mx-auto flex max-w-[1900px] flex-wrap items-center gap-2 px-4 py-2 sm:px-6">
            <WarningCircle size={22} weight="fill" className="shrink-0 text-amber-600" />
            <div className="min-w-0 flex-1 text-sm font-extrabold">
                {summary ? <>
                    قيود: المتبقي <span data-testid="qoyod-unsent-header-alert-count">{summary.count.toLocaleString("ar-SA")}</span> طلبًا حسب الفواتير المحفوظة في ميزان
                    <span className="mr-2 text-xs">آخر حساب: {dateLabel(summary.updated_at)} · آخر مزامنة للفواتير: {dateLabel(summary.latest_invoice_sync_at)}</span>
                </> : "قيود: لم يُحسب العدد بعد؛ اضغط تحديث العدد"}
                <span className="mr-2 text-xs">تحديث يدوي فقط · لا يجلب فواتير من قيود</span>
                {error && <span className="mr-2 text-xs" data-testid="qoyod-summary-error">{error}</span>}
            </div>
            <button onClick={refresh} disabled={busy} className="rounded border px-3 py-1.5 text-xs font-bold">{busy ? "جارٍ حساب العدد…" : "تحديث العدد"}</button>
            <Link to="/integrations-v2/qoyod?tab=invoices" className="rounded-lg border border-amber-600 bg-white px-3 py-1.5 text-xs font-black text-amber-900" data-testid="qoyod-unsent-header-alert-link">مراجعة الفواتير</Link>
        </div>
    </div>;
}
