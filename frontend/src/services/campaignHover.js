import api from "../lib/api";

// Components preload one batch and keep the result in their own state. The
// server owns the tenant-scoped cache: a module-global browser result cache
// could otherwise survive logout and show another account's financial data.
// No request is initiated by mouse/focus/tap handlers.
export async function getCampaignHoverBatch(ids) {
    const unique = [...new Set(ids.map(String).filter(Boolean))].sort().slice(0, 20);
    if (!unique.length) return {};
    try {
        const { data } = await api.get("/orders-v2/campaign-hover", {
            params: { campaign_ids: unique.join(",") }, timeout: 12000,
        });
        return Object.fromEntries(unique.map((id) => [id,
            data?.campaigns?.[id] || { campaign_id: id, status: "report_unavailable" },
        ]));
    } catch {
        return Object.fromEntries(unique.map((id) => [id,
            { campaign_id: id, status: "report_unavailable" },
        ]));
    }
}
