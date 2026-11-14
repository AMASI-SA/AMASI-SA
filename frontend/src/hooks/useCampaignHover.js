import { useEffect, useMemo, useState } from "react";
import { campaignHoverKey } from "../lib/orderCampaignAttribution";
import { getCampaignHoverBatch } from "../services/campaignHover";
import { dashboardCardQuery } from "../services/dashboardCardSnapshot";
import { todaySA } from "../lib/dates";

const SNAPSHOT_BUCKET_MS = 180_000;
const COALESCE_MS = 350;
const EMPTY_TOTALS = Object.freeze({});
const EMPTY_REPORTS = Object.freeze({});

const scalar = (value) => value == null ? null
    : ["string", "number", "boolean"].includes(typeof value) ? value : null;

// A cache-change fingerprint only, never an authorization or campaign identity.
// Excludes customer names, contact information and raw order payloads.
function fingerprint(text) {
    let first = 0x811c9dc5;
    let second = 0x9e3779b9;
    for (let index = 0; index < text.length; index += 1) {
        const code = text.charCodeAt(index);
        first = Math.imul(first ^ code, 0x01000193) >>> 0;
        second = Math.imul(second ^ code, 0x85ebca6b) >>> 0;
    }
    return first.toString(16).padStart(8, "0")
        + second.toString(16).padStart(8, "0");
}

export function campaignSnapshotRevision(orders, totals = {}, now = Date.now()) {
    const rows = (Array.isArray(orders) ? orders : []).map((order) => {
        const status = order?.order_status ?? order?.status;
        const items = Array.isArray(order?.items) ? order.items : [];
        return [
            String(order?.order_number ?? order?.id ?? ""),
            scalar(order?.order_date ?? order?.created_at),
            scalar(typeof status === "object" ? status?.slug ?? status?.id ?? status?.name : status),
            scalar(order?.totals?.total_sar ?? order?.total_amount_sar),
            scalar(order?.totals?.total ?? order?.amount_total ?? order?.total_amount),
            scalar(order?.totals?.currency ?? order?.original_currency ?? order?.currency),
            scalar(order?.totals?.exchange_rate_to_sar ?? order?.exchange_rate_to_sar),
            scalar(order?.items_count ?? order?.quantity),
            campaignHoverKey(order || {}),
            items.map((item) => [scalar(item?.order_item_id ?? item?.id ?? item?.product_id), scalar(item?.quantity), scalar(item?.type)]),
        ];
    });
    const counts = ["count", "orders", "orders_count", "total_orders", "total_count", "paid_orders", "pending_payment_orders", "cancelled_orders", "refunded_orders", "units", "paid_sales", "total_sales", "operating_expenses_total"]
        .map((key) => scalar(totals?.[key]));
    const content = JSON.stringify([rows, counts]);
    // The bucket is observed only when the existing order reader supplies data.
    // There is deliberately no background timer or polling in this hook.
    return `${Math.floor(now / SNAPSHOT_BUCKET_MS).toString(36)}:${fingerprint(content)}`;
}

export default function useCampaignHover(orders, { limit = 20, visibleCount = Infinity, totals = EMPTY_TOTALS, operatingContext } = {}) {
    // Standalone pages observe the store day only on existing order-data reads.
    // A dashboard passes its selection, or null while that basis is pending.
    const storeContext = useMemo(() => {
        const day = todaySA();
        return { from: day, to: day };
    }, [orders, totals]);
    const selectedContext = operatingContext === undefined ? storeContext : operatingContext;
    const operatingKey = selectedContext?.from ? dashboardCardQuery(selectedContext) : null;
    const snapshot = useMemo(() => {
        const rows = (Array.isArray(orders) ? orders : []).slice(0, visibleCount);
        const ids = [...new Set(rows.map((order) => campaignHoverKey(order))
            .filter(Boolean))].slice(0, Math.min(20, Math.max(1, limit))).sort();
        return {
            idsKey: ids.join(","),
            revision: ids.length ? campaignSnapshotRevision(rows, totals) : "",
        };
    }, [orders, totals, limit, visibleCount]);
    const requestKey = snapshot.idsKey && operatingKey
        ? JSON.stringify([snapshot.idsKey, snapshot.revision, operatingKey]) : null;
    const [result, setResult] = useState({ requestKey: null, reports: EMPTY_REPORTS });

    useEffect(() => {
        let active = true;
        setResult({ requestKey, reports: EMPTY_REPORTS });
        if (!requestKey) return undefined;
        const ids = snapshot.idsKey.split(",");
        const query = new URLSearchParams(operatingKey);
        const context = { from: query.get("from_date"), to: query.get("to_date") };
        for (const name of ["payment_methods", "shipping_companies", "report_statuses"]) {
            context[name] = query.get(name)?.split(",") || [];
        }
        // Coalesce bursts of already-delivered order changes, not hover events.
        const timer = setTimeout(() => {
            getCampaignHoverBatch(ids, { snapshotRevision: snapshot.revision, operatingContext: context }).then(
                (reports) => { if (active) setResult({ requestKey, reports }); },
                () => { if (active) setResult({ requestKey, reports: Object.fromEntries(ids.map((id) => [
                    id, { campaign_id: id, status: "report_unavailable" },
                ])) }); },
            );
        }, COALESCE_MS);
        return () => { active = false; clearTimeout(timer); };
    }, [snapshot.idsKey, snapshot.revision, operatingKey, requestKey]);

    // Effects run after rendering; never expose the prior scope in that render.
    return result.requestKey === requestKey ? result.reports : EMPTY_REPORTS;
}
