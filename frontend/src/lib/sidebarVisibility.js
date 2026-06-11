/**
 * Iter-141 — User-controlled sidebar page visibility.
 *
 * Stored as `settings.sidebar_hidden_pages` in the merchant's user
 * document so the choice mirrors across every device they log in
 * from.  A localStorage cache (`mezan.sidebar.hidden_pages_cache`)
 * holds the last-known list so the Sidebar can render before the
 * /api/settings round-trip completes.
 *
 * Public API (UNCHANGED from Iter-124 — drop-in upgrade):
 *   loadHiddenPages()              → Set<string>   (cached, sync)
 *   saveHiddenPages(setOrArray)    → fires the event + persists
 *   refreshHiddenPagesFromServer() → async, refreshes cache from API
 *   SIDEBAR_VISIBILITY_EVENT       → 'mezan:sidebar-visibility-changed'
 */
import api from "./api";


const CACHE_KEY = "mezan.sidebar.hidden_pages_cache";
// Iter-124 → Iter-141 migration: old per-device list lived under this
// key.  If present on first load, we merge it into the user setting
// once then drop the old key.
const LEGACY_KEY = "mezan.sidebar.hidden_pages";
export const SIDEBAR_VISIBILITY_EVENT = "mezan:sidebar-visibility-changed";


function _readCache() {
    try {
        const raw = localStorage.getItem(CACHE_KEY) ?? localStorage.getItem(LEGACY_KEY);
        if (!raw) return [];
        const arr = JSON.parse(raw);
        return Array.isArray(arr) ? arr : [];
    } catch { return []; }
}


function _writeCache(arr) {
    try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(arr));
        // Drop the legacy per-device list — once promoted to the
        // user settings doc it's no longer needed.
        localStorage.removeItem(LEGACY_KEY);
    } catch { /* private mode etc. */ }
}


function _broadcast(arr) {
    window.dispatchEvent(new CustomEvent(SIDEBAR_VISIBILITY_EVENT, {
        detail: { hidden: arr },
    }));
}


/** Synchronous reader — returns the cached value (last loaded from
 *  /api/settings, or the legacy localStorage list on first run). */
export function loadHiddenPages() {
    return new Set(_readCache());
}


/** Pull the canonical list from /api/settings and update the cache.
 *  Returns the fresh Set.  Safe to call once at app boot. */
export async function refreshHiddenPagesFromServer() {
    try {
        const { data } = await api.get("/settings");
        const arr = Array.isArray(data?.sidebar_hidden_pages)
            ? data.sidebar_hidden_pages : [];

        // First-run migration: if the legacy localStorage list exists
        // and the server is empty, push the local list up so the user
        // doesn't appear to "lose" their hidden pages on first login.
        const legacy = (() => {
            try {
                const raw = localStorage.getItem(LEGACY_KEY);
                return raw ? JSON.parse(raw) : null;
            } catch { return null; }
        })();
        if (arr.length === 0 && Array.isArray(legacy) && legacy.length > 0) {
            await api.put("/settings", {
                ...data,
                sidebar_hidden_pages: legacy,
            });
            _writeCache(legacy);
            _broadcast(legacy);
            return new Set(legacy);
        }

        _writeCache(arr);
        _broadcast(arr);
        return new Set(arr);
    } catch (e) {
        console.warn("sidebar visibility refresh failed", e);
        return new Set(_readCache());
    }
}


/** Persist the new list to the server, update the cache, and notify
 *  the Sidebar.  Optimistic — UI updates instantly, then the API
 *  call runs in the background.  If the server save fails we revert
 *  the cache and re-broadcast so the Sidebar can rollback. */
export function saveHiddenPages(setOrArray) {
    const arr = Array.from(setOrArray instanceof Set ? setOrArray : (setOrArray || []));
    const prev = _readCache();

    // Optimistic update
    _writeCache(arr);
    _broadcast(arr);

    // Push to server (don't block).
    (async () => {
        try {
            const { data: current } = await api.get("/settings");
            await api.put("/settings", {
                ...current,
                sidebar_hidden_pages: arr,
            });
        } catch (e) {
            console.error("sidebar visibility save failed", e);
            // Rollback
            _writeCache(prev);
            _broadcast(prev);
        }
    })();
}
