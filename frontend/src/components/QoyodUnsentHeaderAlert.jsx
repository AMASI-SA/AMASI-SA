import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { WarningCircle } from "@phosphor-icons/react";

import {
    isQoyodRequestAbort,
    loadQoyodUnsentOrders,
} from "../lib/qoyodUnsentOrdersClient";

const QOYOD_SYNC_START_DATE = "2026-07-01";
const REFRESH_INTERVAL_MS = 60_000;

export function eligibleQoyodUnsentCount(payload, now = Date.now()) {
    const evidence = payload?.invoice_presence;
    if (payload?.scan_truncated || evidence?.complete !== true
        || !Number.isFinite(Date.parse(evidence.expires_at))
        || Date.parse(evidence.expires_at) <= now) return null;
    const value = evidence.count;
    return Number.isInteger(value) && value >= 0 ? value : null;
}

export default function QoyodUnsentHeaderAlert() {
    const [payload, setPayload] = useState(null);
    const count = eligibleQoyodUnsentCount(payload);

    const refresh = useCallback(async (signal) => {
        try {
            const payload = await loadQoyodUnsentOrders(
                {
                    from_date: QOYOD_SYNC_START_DATE,
                    limit: 5000,
                    status: "verified_absent",
                },
                { signal },
            );
            if (!signal.aborted) {
                setPayload(payload);
            }
        } catch (error) {
            if (isQoyodRequestAbort(error)) return;
            // This owner-only check must never break the store header.
            // Never retain a numeric claim when the refresh fails.
            setPayload(null);
        }
    }, []);

    useEffect(() => {
        let running = false;
        let controller = null;
        const run = async () => {
            if (running) return;
            running = true;
            controller = new AbortController();
            try {
                await refresh(controller.signal);
            } finally {
                running = false;
            }
        };
        run();
        const timer = window.setInterval(run, REFRESH_INTERVAL_MS);
        return () => {
            window.clearInterval(timer);
            controller?.abort();
        };
    }, [refresh]);

    if (count === 0 && !payload?.invoice_presence?.verification_required) return null;

    return (
        <div
            className="border-b border-amber-400 bg-amber-100 text-amber-950"
            dir="rtl"
            role="status"
            aria-live="polite"
            data-testid="qoyod-unsent-header-alert"
        >
            <div className="mx-auto flex max-w-[1900px] flex-wrap items-center gap-2 px-4 py-2 sm:px-6">
                <WarningCircle
                    size={22}
                    weight="fill"
                    className="shrink-0 text-amber-600"
                />
                <div className="min-w-0 flex-1 text-sm font-extrabold">
                    {count === null ? "قيود: يلزم تحديث المطابقة للتحقق من الطلبات غير المرسلة" : <>
                        قيود: <span data-testid="qoyod-unsent-header-alert-count">{count.toLocaleString("ar-SA")}</span>{" "}
                        طلب مؤهل دون فاتورة مطابقة في آخر تحقق، منذ 1 يوليو 2026
                        <span className="mr-2 text-xs">({new Date(payload.invoice_presence.verified_at).toLocaleString("ar-SA", { timeZone: "Asia/Riyadh" })})</span>
                        {payload.invoice_presence.verification_required > 0 && <span className="mr-2">· {payload.invoice_presence.verification_required} طلب يحتاج تحققًا</span>}
                    </>}

                </div>
                <Link
                    to={count === null ? "/integrations-v2/qoyod?tab=reconciliation" : "/integrations-v2/qoyod?tab=exceptions&verified_absent=1"}
                    className="rounded-lg border border-amber-600 bg-white px-3 py-1.5 text-xs font-black text-amber-900 transition hover:bg-amber-50"
                    data-testid="qoyod-unsent-header-alert-link"
                >
                    مراجعة الطلبات
                </Link>
            </div>
        </div>
    );
}
