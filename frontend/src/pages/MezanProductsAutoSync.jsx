import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { useOptionalAuth } from "../context/AuthContext";
import { managedWebPages } from "../lib/webPageAccess";

import ProductGoogleTaxonomyPilotPanel from "../components/products/ProductGoogleTaxonomyPilotPanel";
import { getRecentProductsSyncNextAt, RECENT_PRODUCTS_SYNC_INTERVAL_MS, syncRecentProductsV2 } from "../services/mezanProductsV2";
import MezanProductsWorkspace from "./MezanProductsWorkspace";

export default function MezanProductsAutoSync() {
    const { user } = useOptionalAuth() || {};
    const grant = managedWebPages(user)?.find((page) => page.id === "products.catalog");
    return grant ? <EmployeeCatalogue mode={grant.mode} /> : <OwnerProductsWorkspace ownerId={user?.id} />;
}

function EmployeeCatalogue({ mode }) {
    const location = useLocation();
    return <MezanProductsWorkspace key={location.key} catalogSearch={location.search} canEditDraft={mode === "write"} />;
}

function OwnerProductsWorkspace({ ownerId }) {
    useEffect(() => {
        let active = true;
        let pending = false;
        let timer;
        const schedule = (nextAt) => {
            window.clearTimeout(timer);
            if (active) timer = window.setTimeout(refreshRecent, Math.max(1000, nextAt - Date.now()));
        };
        async function refreshRecent() {
            if (!active || pending || document.visibilityState === "hidden" || navigator.onLine === false) return;
            pending = true;
            let nextAt = Date.now() + 60_000;
            try {
                const result = await syncRecentProductsV2({ ownerId });
                if (!active) return;
                const serverNext = Date.parse(result?.next_retry_at || result?.next_sync_at || "");
                nextAt = getRecentProductsSyncNextAt(ownerId)
                    || (Number.isFinite(serverNext) ? serverNext : Date.now() + RECENT_PRODUCTS_SYNC_INTERVAL_MS);
                if ((result?.created || 0) > 0 || (result?.updated || 0) > 0) {
                    window.dispatchEvent(new CustomEvent("mezan:products-recent-sync", { detail: result }));
                }
            } catch {
                // Keep the open product editor and local V2 catalogue usable.
                nextAt = getRecentProductsSyncNextAt(ownerId) || nextAt;
            } finally {
                pending = false;
                schedule(nextAt);
            }
        }
        schedule(getRecentProductsSyncNextAt(ownerId) || Date.now() + RECENT_PRODUCTS_SYNC_INTERVAL_MS);
        const onVisible = () => { if (document.visibilityState === "visible") refreshRecent(); };
        document.addEventListener("visibilitychange", onVisible);
        window.addEventListener("online", onVisible);
        return () => {
            active = false;
            window.clearTimeout(timer);
            document.removeEventListener("visibilitychange", onVisible);
            window.removeEventListener("online", onVisible);
        };
    }, [ownerId]);

    // Never key/remount the workspace: a background sync must not erase unsaved edits.
    return <div className="space-y-4" dir="rtl">
        <ProductGoogleTaxonomyPilotPanel />
        <MezanProductsWorkspace />
    </div>;
}
