import api from "../lib/api";

// Deduplicate prefetches for the visible order page. No work runs on hover.
const cache = new Map();
const TTL_MS = 180_000;
function stillSameAccountDay(rows) {
    return Object.values(rows || {}).every((row) => {
        if (!row?.timezone || !row?.date) return true;
        try {
            const parts = new Intl.DateTimeFormat("en-US", {
                timeZone: row.timezone, year: "numeric", month: "2-digit", day: "2-digit",
            }).formatToParts(new Date());
            const get = (type) => parts.find((p) => p.type === type)?.value;
            return `${get("year")}-${get("month")}-${get("day")}` === row.date;
        } catch { return false; }
    });
}
export function getCampaignHoverBatch(ids) {
    const key = [...new Set(ids)].sort().join(",");
    if (!key) return Promise.resolve({});
    const found = cache.get(key);
    if (found && Date.now() < found.expires && (!found.rows || stillSameAccountDay(found.rows))) return found.promise;
    const promise = api.get("/orders-v2/campaign-hover", {
        params: { campaign_ids: key },
        timeout: 12000,
    }).then(({ data }) => {
        const rows = data?.campaigns || {};
        const entry = cache.get(key);
        if (entry?.promise === promise) entry.rows = rows;
        return rows;
    });
    cache.set(key, { expires: Date.now() + TTL_MS, promise });
    promise.catch(() => { if (cache.get(key)?.promise === promise) cache.delete(key); });
    if (cache.size > 20) cache.delete(cache.keys().next().value);
    return promise;
}
