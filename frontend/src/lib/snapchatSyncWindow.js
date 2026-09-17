export const SNAPCHAT_SYNC_DAYS = 30;
export const SNAPCHAT_SYNC_WINDOW_MESSAGE = "المزامنة متاحة لآخر 30 يومًا فقط بتوقيت الرياض. يمكنك عرض الصرف المحفوظ للفترات الأقدم.";

export function latestSnapchatSyncRange(now = Date.now()) {
    const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(new Date(now));
    const part = (type) => parts.find((item) => item.type === type)?.value;
    const dateTo = `${part("year")}-${part("month")}-${part("day")}`;
    const first = new Date(`${dateTo}T00:00:00Z`);
    first.setUTCDate(first.getUTCDate() - (SNAPCHAT_SYNC_DAYS - 1));
    return { dateFrom: first.toISOString().slice(0, 10), dateTo };
}

export function snapchatSyncRangeAllowed(range, now = Date.now()) {
    if (!range) return false;
    const { dateFrom, dateTo } = range;
    const validDate = (value) => {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return false;
        const parsed = new Date(`${value}T00:00:00Z`);
        return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
    };
    if (!validDate(dateFrom) || !validDate(dateTo) || dateTo < dateFrom) return false;
    const window = latestSnapchatSyncRange(now);
    return dateFrom >= window.dateFrom && dateTo <= window.dateTo;
}
