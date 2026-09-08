import api from "../lib/api";

const inflight = new Map();

function boundedControls(value = {}) {
    return {
        page: Math.max(1, Math.trunc(Number(value.page) || 1)),
        page_size: Math.min(100, Math.max(1, Math.trunc(Number(value.pageSize) || 25))),
        search: String(value.search || "").trim().slice(0, 100),
        active_only: value.activeOnly === true,
        sort_by: ["default", "spend", "name"].includes(value.sortBy) ? value.sortBy : "default",
        sort_direction: value.sortDirection === "asc" ? "asc" : "desc",
    };
}

export function entityReadPath(level) {
    if (level === "campaign") return "/integrations-v2/snapchat-v2/campaigns";
    if (level === "ad_group") return "/integrations-v2/snapchat-v2/ad-squads";
    if (level === "ad") return "/integrations-v2/snapchat-v2/ads";
    throw new Error("invalid_snapchat_entity_level");
}

export function readSnapchatEntityPage({
    level,
    dateFrom,
    dateTo,
    controls,
    campaignId = "",
    adSquadId = "",
} = {}) {
    const params = {
        date_from: dateFrom,
        date_to: dateTo,
        timezone: "account",
        action_report_time: "conversion",
        ...boundedControls(controls),
        campaign_id: campaignId || undefined,
        ad_squad_id: adSquadId || undefined,
    };
    const path = entityReadPath(level);
    const key = JSON.stringify([path, params]);
    if (inflight.has(key)) return inflight.get(key);
    const request = api.get(path, { params }).finally(() => {
        if (inflight.get(key) === request) inflight.delete(key);
    });
    inflight.set(key, request);
    return request;
}

export function resetSnapchatEntityReadCoalescerForTests() {
    inflight.clear();
}

export { boundedControls };
