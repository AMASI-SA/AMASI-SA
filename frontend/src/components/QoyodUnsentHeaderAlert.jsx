import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { WarningCircle } from "@phosphor-icons/react";

import api from "../lib/api";

const QOYOD_BASE = "/integrations/qoyod";
const QOYOD_SYNC_START_DATE = "2026-07-01";
const REFRESH_INTERVAL_MS = 15_000;

export function eligibleQoyodUnsentCount(payload) {
    const value = Number(payload?.counts?.["لم يُرسل"] ?? 0);
    if (!Number.isFinite(value) || value <= 0) return 0;
    return Math.floor(value);
}

export default function QoyodUnsentHeaderAlert() {
    const [count, setCount] = useState(0);

    const refresh = useCallback(async () => {
        try {
            const { data } = await api.get(`${QOYOD_BASE}/unsent-orders`, {
                params: {
                    from_date: QOYOD_SYNC_START_DATE,
                    limit: 5000,
                    status: "لم يُرسل",
                },
            });
            setCount(eligibleQoyodUnsentCount(data));
        } catch (_) {
            // This owner-only check must never break the store header.
            // Keep the last confirmed count until the next successful refresh.
        }
    }, []);

    useEffect(() => {
        refresh();
        const timer = window.setInterval(refresh, REFRESH_INTERVAL_MS);
        return () => window.clearInterval(timer);
    }, [refresh]);

    if (count === 0) return null;

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
                    تنبيه قيود: يوجد{" "}
                    <span
                        className="inline-flex min-w-7 items-center justify-center rounded-full bg-amber-500 px-2 py-0.5 text-sm font-black text-slate-950"
                        data-testid="qoyod-unsent-header-alert-count"
                    >
                        {count.toLocaleString("ar-SA")}
                    </span>{" "}
                    طلب مؤهل منذ 1 يوليو 2026 لم يُرسل إلى قيود
                </div>
                <Link
                    to="/integrations-v2/qoyod?tab=exceptions"
                    className="rounded-lg border border-amber-600 bg-white px-3 py-1.5 text-xs font-black text-amber-900 transition hover:bg-amber-50"
                    data-testid="qoyod-unsent-header-alert-link"
                >
                    مراجعة الطلبات
                </Link>
            </div>
        </div>
    );
}
