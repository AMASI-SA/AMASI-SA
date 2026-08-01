import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import SnapchatStandaloneAccountCards from "./SnapchatStandaloneAccountCards";

const MERGED_CARD_SELECTOR = '[data-testid="snapchat-ads-section"]';
const HOST_TEST_ID = "dashboard-snapchat-standalone-host";

export default function DashboardSnapchatAccountsPlacement({ active = false }) {
    const [host, setHost] = useState(null);
    const [mergedCard, setMergedCard] = useState(null);
    const [ready, setReady] = useState(false);

    useEffect(() => {
        if (!active) {
            setHost(null);
            setMergedCard(null);
            setReady(false);
            return undefined;
        }

        let disposed = false;
        let frame = null;
        let currentHost = null;
        let currentMerged = null;

        const restore = () => {
            if (currentMerged) {
                currentMerged.style.removeProperty("display");
                currentMerged.removeAttribute("aria-hidden");
            }
            if (currentHost?.parentNode) currentHost.parentNode.removeChild(currentHost);
            currentHost = null;
            currentMerged = null;
        };

        const ensurePlacement = () => {
            if (disposed) return;
            const candidate = document.querySelector(MERGED_CARD_SELECTOR);
            if (!candidate?.parentElement) {
                frame = window.requestAnimationFrame(ensurePlacement);
                return;
            }

            if (currentMerged !== candidate) {
                if (currentMerged) {
                    currentMerged.style.removeProperty("display");
                    currentMerged.removeAttribute("aria-hidden");
                }
                currentMerged = candidate;
                setMergedCard(candidate);
            }

            if (!currentHost || !currentHost.isConnected) {
                const existing = document.querySelector(`[data-testid="${HOST_TEST_ID}"]`);
                currentHost = existing || document.createElement("div");
                currentHost.className = "mt-6";
                currentHost.setAttribute("data-testid", HOST_TEST_ID);
                setHost(currentHost);
            }

            if (candidate.nextElementSibling !== currentHost) {
                candidate.insertAdjacentElement("afterend", currentHost);
            }
        };

        ensurePlacement();
        const observer = new MutationObserver(ensurePlacement);
        observer.observe(document.body, { childList: true, subtree: true });

        return () => {
            disposed = true;
            if (frame) window.cancelAnimationFrame(frame);
            observer.disconnect();
            restore();
        };
    }, [active]);

    useEffect(() => {
        if (!mergedCard) return undefined;
        if (ready) {
            mergedCard.style.setProperty("display", "none", "important");
            mergedCard.setAttribute("aria-hidden", "true");
        } else {
            mergedCard.style.removeProperty("display");
            mergedCard.removeAttribute("aria-hidden");
        }
        return () => {
            mergedCard.style.removeProperty("display");
            mergedCard.removeAttribute("aria-hidden");
        };
    }, [mergedCard, ready]);

    if (!active || !host) return null;

    return createPortal(
        <SnapchatStandaloneAccountCards onReadyChange={setReady} />,
        host,
    );
}

export { HOST_TEST_ID, MERGED_CARD_SELECTOR };
