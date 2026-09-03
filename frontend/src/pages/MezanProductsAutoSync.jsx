import { useEffect } from "react";

import ProductGoogleTaxonomyPilotPanel from "../components/products/ProductGoogleTaxonomyPilotPanel";
import { syncRecentProductsV2 } from "../services/mezanProductsV2";
import MezanProductsWorkspace from "./MezanProductsWorkspace";

const AUTO_SYNC_INTERVAL_MS = 5 * 60_000;

export default function MezanProductsAutoSync() {
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        if (params.get("product") || params.get("focus")) return undefined;
        let active = true;
        async function refreshRecent() {
            if (document.visibilityState === "hidden") return;
            try {
                const result = await syncRecentProductsV2({ force: false });
                if (!active) return;
                if ((result?.created || 0) > 0 || (result?.updated || 0) > 0) {
                    window.dispatchEvent(new CustomEvent("mezan:products-recent-sync", { detail: result }));
                }
            } catch {
                // Keep the open product editor and local V2 catalogue usable.
            }
        }
        const timer = window.setInterval(refreshRecent, AUTO_SYNC_INTERVAL_MS);
        const onVisible = () => { if (document.visibilityState === "visible") refreshRecent(); };
        document.addEventListener("visibilitychange", onVisible);
        return () => {
            active = false;
            window.clearInterval(timer);
            document.removeEventListener("visibilitychange", onVisible);
        };
    }, []);

    // Never key/remount the workspace: a background sync must not erase unsaved edits.
    return <div className="space-y-4" dir="rtl">
        <ProductGoogleTaxonomyPilotPanel />
        <MezanProductsWorkspace />
    </div>;
}
