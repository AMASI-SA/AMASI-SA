import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { useAuth } from "../context/AuthContext";
import api from "../lib/api";
import { formatRiyadhDateTime } from "../lib/tzUtils";

const COUNT_PATH = "/integrations/qoyod/unsent-orders/header-count";
const DASHBOARDS = ["/dashboard-advanced", "/dashboard-v2", "/legacy-dashboard"];

export default function QoyodUnsentHeaderAlert() {
    const { user } = useAuth();
    const { pathname } = useLocation();
    const financial = pathname === "/integrations-v2/qoyod";
    if (!user?.is_owner || !user.id || (!financial && !DASHBOARDS.includes(pathname))) return null;
    return <SavedUnsentCount key={`${user.id}:${financial}`} financial={financial} />;
}

function SavedUnsentCount({ financial }) {
    const [snapshot, setSnapshot] = useState(null);
    const [refreshing, setRefreshing] = useState(false);
    const [notice, setNotice] = useState("");
    const [checkedAt, setCheckedAt] = useState(Date.now);
    const request = useRef(null);

    const load = useCallback(async (manual = false) => {
        if (document.hidden) return;
        if (request.current) {
            if (!manual || request.current.manual) return;
            request.current.controller.abort();
        }

        const controller = new AbortController();
        request.current = { controller, manual };
        if (manual) setRefreshing(true);
        try {
            const options = { signal: controller.signal };
            const { data } = manual
                ? await api.post(COUNT_PATH + "/refresh", null, options)
                : await api.get(COUNT_PATH, options);
            if (controller.signal.aborted) return;
            const known = Number.isInteger(data?.count) && data.count >= 0 && Number.isFinite(Date.parse(data.as_of));
            if (known) {
                setSnapshot(previous => previous && Date.parse(data.as_of) <= Date.parse(previous.asOf)
                    ? previous : { count: data.count, asOf: data.as_of });
            } else if (!data?.partial && !manual && data?.count === null && data?.as_of === null) {
                setSnapshot(null);
            }
            if (data?.partial) {
                setNotice("لم يكتمل فحص الطلبات.");
            } else if (manual) {
                setNotice(known ? "" : "تعذّر تحديث العدد.");
            }
        } catch {
            if (!controller.signal.aborted && manual) setNotice("تعذّر تحديث العدد.");
        } finally {
            if (request.current?.controller === controller) {
                request.current = null;
                setCheckedAt(Date.now());
                if (manual) setRefreshing(false);
            }
        }
    }, []);

    useEffect(() => {
        const read = () => {
            if (document.hidden && request.current && !request.current.manual) {
                request.current.controller.abort();
                request.current = null;
            }
            if (!document.hidden) load();
        };
        read();
        const timer = window.setInterval(read, 60_000);
        document.addEventListener("visibilitychange", read);
        return () => {
            window.clearInterval(timer);
            document.removeEventListener("visibilitychange", read);
            request.current?.controller.abort();
            request.current = null;
        };
    }, [load]);

    const old = snapshot && checkedAt - Date.parse(snapshot.asOf) >= 60 * 60_000;
    return (
        <div dir="rtl" role="status" data-testid="qoyod-unsent-header-alert"
            className={snapshot?.count > 0 ? "border-b border-amber-400 bg-amber-100 text-amber-950" : "border-b bg-slate-50 text-slate-700"}>
            <div className="mx-auto flex max-w-[1900px] flex-wrap items-center gap-3 px-4 py-2 text-sm">
                {snapshot ? <>
                    <span>طلبات لم تُرسل إلى قيود حسب آخر فحص · <strong data-testid="qoyod-unsent-header-alert-count">{snapshot.count.toLocaleString("ar-SA")}</strong></span>
                    <time dateTime={snapshot.asOf}>{old ? "آخر فحص قديم" : "آخر فحص"} · {formatRiyadhDateTime(snapshot.asOf)} بتوقيت الرياض</time>
                </> : <span>عدد الطلبات غير المرسلة إلى قيود غير متوفر بعد.</span>}
                {notice && <span>{notice}</span>}
                {financial ? (
                    <button type="button" disabled={refreshing} onClick={() => load(true)} className="rounded border px-3 py-1 font-bold">
                        {refreshing ? "جارٍ تحديث العدد…" : "تحديث العدد"}
                    </button>
                ) : (
                    <Link to="/integrations-v2/qoyod" data-testid="qoyod-unsent-header-alert-link" className="font-bold underline">فتح مركز قيود</Link>
                )}
            </div>
        </div>
    );
}
