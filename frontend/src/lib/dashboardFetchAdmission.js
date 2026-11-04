/**
 * Dashboard-only request admission: don't add a background summary refresh
 * while a dashboard summary request is already in flight in this browser tab.
 * Explicit period/filter loads always proceed; request sequence fencing in the
 * caller still prevents stale responses from overwriting fresh data.
 *
 * This changes no API, server process, schedule, invoice or order behavior.
 */
export function createDashboardFetchAdmission() {
    let foregroundInFlight = 0;
    let backgroundInFlight = 0;

    return {
        start(background = false) {
            if (background && (foregroundInFlight > 0 || backgroundInFlight > 0)) {
                return false;
            }
            if (background) backgroundInFlight += 1;
            else foregroundInFlight += 1;
            return true;
        },
        finish(background = false) {
            if (background) backgroundInFlight = Math.max(0, backgroundInFlight - 1);
            else foregroundInFlight = Math.max(0, foregroundInFlight - 1);
        },
        snapshot() {
            return { foregroundInFlight, backgroundInFlight };
        },
    };
}
