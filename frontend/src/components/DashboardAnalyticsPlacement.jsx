import { useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import GoogleAnalyticsRealtimeCards from "./GoogleAnalyticsRealtimeCards";
import GoogleAnalyticsTrafficSourcesCard from "./GoogleAnalyticsTrafficSourcesCard";

const PROFIT_SUMMARY_SELECTOR = '[data-testid="profit-summary-card"]';
const HOST_TEST_ID = "dashboard-ga4-analytics-wrap";
const LEGACY_SECTION_SELECTORS = [
    '[data-testid="dashboard-salary-accrual-section"]',
];
const LEGACY_SECTION_HEADINGS = new Set([
    "الأداء الشهري",
    "آخر التحاليل",
]);

function hideDashboardSection(node) {
    if (!(node instanceof HTMLElement)) return;
    node.setAttribute("data-dashboard-pruned", "true");
    node.setAttribute("aria-hidden", "true");
    node.style.setProperty("display", "none", "important");
}

/**
 * Remove obsolete legacy-only cards from the merchant Dashboard surface.
 *
 * These blocks duplicate information already available in dedicated pages or
 * in the executive profit summary.  We hide their outer card before paint and
 * re-apply the rule whenever Dashboard refreshes replace DOM nodes.
 */
export function pruneLegacyDashboardSections(root = document) {
    LEGACY_SECTION_SELECTORS.forEach((selector) => {
        root.querySelectorAll(selector).forEach(hideDashboardSection);
    });

    root.querySelectorAll("h2").forEach((heading) => {
        const title = String(heading.textContent || "").trim();
        if (!LEGACY_SECTION_HEADINGS.has(title)) return;
        const card = heading.closest("div.rounded-xl")
            || heading.parentElement?.parentElement;
        hideDashboardSection(card);
    });
}

/**
 * Mount the Google Analytics cards immediately after the executive profit
 * summary without coupling the generic Layout to Dashboard internals.
 *
 * The Dashboard refreshes its data every minute, so the host is re-checked
 * with a MutationObserver and reattached if React replaces the surrounding
 * dashboard nodes during a refresh.
 */
export default function DashboardAnalyticsPlacement({ active = false }) {
    const [host, setHost] = useState(null);

    useLayoutEffect(() => {
        if (!active) {
            setHost(null);
            return undefined;
        }

        let disposed = false;
        let frame = null;
        let currentHost = null;

        const removeHost = () => {
            if (currentHost?.parentNode) currentHost.parentNode.removeChild(currentHost);
            currentHost = null;
            if (!disposed) setHost(null);
        };

        const ensurePlacement = () => {
            if (disposed) return;
            pruneLegacyDashboardSections(document);

            const profitSummary = document.querySelector(PROFIT_SUMMARY_SELECTOR);
            if (!profitSummary?.parentElement) {
                frame = window.requestAnimationFrame(ensurePlacement);
                return;
            }

            if (!currentHost || !currentHost.isConnected) {
                const existing = document.querySelector(`[data-testid="${HOST_TEST_ID}"]`);
                currentHost = existing || document.createElement("div");
                currentHost.className = "mt-6 space-y-6";
                currentHost.setAttribute("data-testid", HOST_TEST_ID);
                setHost(currentHost);
            }

            if (profitSummary.nextElementSibling !== currentHost) {
                profitSummary.insertAdjacentElement("afterend", currentHost);
            }
        };

        ensurePlacement();
        const observer = new MutationObserver(ensurePlacement);
        observer.observe(document.body, { childList: true, subtree: true });

        return () => {
            disposed = true;
            if (frame) window.cancelAnimationFrame(frame);
            observer.disconnect();
            removeHost();
        };
    }, [active]);

    if (!active || !host) return null;

    return createPortal(
        <>
            <GoogleAnalyticsRealtimeCards />
            <GoogleAnalyticsTrafficSourcesCard />
        </>,
        host,
    );
}

export {
    HOST_TEST_ID,
    LEGACY_SECTION_HEADINGS,
    LEGACY_SECTION_SELECTORS,
    PROFIT_SUMMARY_SELECTOR,
};
