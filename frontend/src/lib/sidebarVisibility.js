/**
 * Iter-124 — User-controlled sidebar page visibility.
 *
 * Stored in localStorage as an array of nav-item testids that the user
 * wants HIDDEN.  Items NOT in the list are visible (default).
 *
 * This is intentionally client-side only — no backend round-trip, no
 * sync between devices.  Each merchant terminal can keep its own
 * sidebar layout.  If we ever want cross-device sync, we can promote
 * this to the user profile document without changing the consumer API.
 */
const STORAGE_KEY = "mezan.sidebar.hidden_pages";

export function loadHiddenPages() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return new Set();
        const arr = JSON.parse(raw);
        return new Set(Array.isArray(arr) ? arr : []);
    } catch {
        return new Set();
    }
}

export function saveHiddenPages(setOrArray) {
    const arr = Array.from(setOrArray instanceof Set ? setOrArray : (setOrArray || []));
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(arr));
        // Notify the Sidebar (which lives in a sibling component tree)
        // so it re-reads without needing a page refresh.
        window.dispatchEvent(new CustomEvent("mezan:sidebar-visibility-changed", {
            detail: { hidden: arr },
        }));
    } catch { /* private mode etc. */ }
}

export const SIDEBAR_VISIBILITY_EVENT = "mezan:sidebar-visibility-changed";
