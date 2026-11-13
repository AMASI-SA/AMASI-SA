import { useEffect, useMemo, useState } from "react";
import { resolveOrderCampaign } from "../lib/orderCampaignAttribution";
import { getCampaignHoverBatch } from "../services/campaignHover";

const SNAPSHOT_BUCKET_MS = 180_000;
const COALESCE_MS = 350;

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
            scalar(order?.amount_total ?? order?.total_amount_sar ?? order?.total_amount),
            scalar(order?.items_count ?? order?.quantity),
            resolveOrderCampaign(order || {}).campaignId || "",
            items.map((item) => [scalar(item?.id ?? item?.product_id), scalar(item?.quantity)]),
        ];
    });
    const counts = ["count", "orders_count", "total_orders", "total_count"]
        .map((key) => scalar(totals?.[key]));
    const content = JSON.stringify([rows, counts]);
    // The bucket is observed only when the existing order reader supplies data.
    // There is deliberately no background timer or polling in this hook.
    return `${Math.floor(now / SNAPSHOT_BUCKET_MS).toString(36)}:${fingerprint(content)}`;
}

export default function useCampaignHover(orders, { limit = 20, visibleCount = Infinity, totals = {} } = {}) {
    const snapshot = useMemo(() => {
        const rows = (Array.isArray(orders) ? orders : []).slice(0, visibleCount);
        const ids = [...new Set(rows.map((order) => resolveOrderCampaign(order).campaignId)
            .filter(Boolean))].slice(0, Math.min(20, Math.max(1, limit))).sort();
        return {
            idsKey: ids.join(","),
            revision: ids.length ? campaignSnapshotRevision(rows, totals) : "",
        };
    }, [orders, totals, limit, visibleCount]);
    const [reports, setReports] = useState({});

    useEffect(() => {
        let active = true;
        setReports({});
        if (!snapshot.idsKey) return undefined;
        const ids = snapshot.idsKey.split(",");
        // Coalesce bursts of already-delivered order changes, not hover events.
        const timer = setTimeout(() => {
            getCampaignHoverBatch(ids, { snapshotRevision: snapshot.revision }).then(
                (rows) => { if (active) setReports(rows); },
                () => { if (active) setReports(Object.fromEntries(ids.map((id) => [
                    id, { campaign_id: id, status: "report_unavailable" },
                ]))); },
            );
        }, COALESCE_MS);
        return () => { active = false; clearTimeout(timer); };
    }, [snapshot.idsKey, snapshot.revision]);

    return reports;
}
