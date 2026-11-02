// The date picker includes both selected days; the API uses an exclusive end.
// Orders use the store's Riyadh day, independent of the device timezone.
export function orderSearchDateRange({ dateFrom, dateTo }) {
    const end = new Date(`${dateTo}T00:00:00Z`);
    end.setUTCDate(end.getUTCDate() + 1);
    return {
        created_from: `${dateFrom}T00:00:00+03:00`,
        created_to: `${end.toISOString().slice(0, 10)}T00:00:00+03:00`,
    };
}

export function orderSearchEndDay(value) {
    if (!value) return "";
    const end = new Date(`${value.slice(0, 10)}T00:00:00Z`);
    if (Number.isNaN(end.getTime())) return "";
    // Existing deep links may use a specific time rather than an exclusive day.
    if (value.includes("T00:00:00+03:00")) end.setUTCDate(end.getUTCDate() - 1);
    return end.toISOString().slice(0, 10);
}
