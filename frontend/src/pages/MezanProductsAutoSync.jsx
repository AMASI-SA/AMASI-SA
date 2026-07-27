import { useEffect, useState } from "react";

import { syncRecentProductsV2 } from "../services/mezanProductsV2";
import MezanProductsWorkspace from "./MezanProductsWorkspace";

const AUTO_SYNC_INTERVAL_MS = 60_000;

export default function MezanProductsAutoSync() {
    const [revision, setRevision] = useState(0);

    useEffect(() => {
        let active = true;
        async function refreshRecent() {
            if (document.visibilityState === "hidden") return;
            try {
                const result = await syncRecentProductsV2({ force: true });
                if (!active) return;
                if ((result?.created || 0) > 0 || (result?.updated || 0) > 0) {
                    setRevision((value) => value + 1);
                }
            } catch {
                // The workspace remains usable during temporary Salla outages.
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

    return <MezanProductsWorkspace key={revision} />;
}
