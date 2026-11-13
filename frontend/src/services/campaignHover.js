import api from "../lib/api";

// Components preload one batch. The server cache is tenant-scoped; a revision
// distinguishes new Salla order lists from an older cached financial snapshot.
// No request is initiated by mouse/focus/tap handlers.
export async function getCampaignHoverBatch(ids, { snapshotRevision } = {}) {
    const unique = [...new Set(ids.map(String).filter(Boolean))].sort().slice(0, 20);
    if (!unique.length) return {};
    const params = { campaign_ids: unique.join(",") };
    if (typeof snapshotRevision === "string" && /^[a-z0-9:-]{1,64}$/.test(snapshotRevision)) {
        params.snapshot_revision = snapshotRevision;
    }
    try {
        const { data } = await api.get("/orders-v2/campaign-hover", { params, timeout: 12000 });
        return Object.fromEntries(unique.map((id) => [id,
            data?.campaigns?.[id] || { campaign_id: id, status: "report_unavailable" },
        ]));
    } catch {
        return Object.fromEntries(unique.map((id) => [id,
            { campaign_id: id, status: "report_unavailable" },
        ]));
    }
}
