import { useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import DashboardAdsSpendCard from "./DashboardAdsSpendCard";
import GoogleAnalyticsRealtimeCards from "./GoogleAnalyticsRealtimeCards";
import GoogleAnalyticsTrafficSourcesCard from "./GoogleAnalyticsTrafficSourcesCard";

const PROFIT_SUMMARY_SELECTOR = '[data-testid="profit-summary-card"]';
const FILTER_SELECTOR = '[data-testid="advanced-filters"]';
const HOST_TEST_ID = "dashboard-ga4-analytics-wrap";
const GRID_TEST_ID = "dashboard-unified-reports-grid";
const GA_HOST_TEST_ID = "dashboard-unified-ga4-host";
const PROFIT_HOST_TEST_ID = "dashboard-unified-profit-host";
const ADS_HOST_TEST_ID = "dashboard-unified-ads-host";
const TRAFFIC_HOST_TEST_ID = "dashboard-ga4-traffic-host";
const LIVE_PROFIT_ATTRIBUTE = "data-dashboard-unified-profit-live";
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
 * in the executive profit summary. We hide their outer card before paint and
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

function readDashboardDateRange(root = document) {
    const filters = root.querySelector(FILTER_SELECTOR);
    const fromDate = filters?.getAttribute("data-from-date") || "";
    const toDate = filters?.getAttribute("data-to-date") || fromDate;
    return { fromDate, toDate };
}

function sameRange(left, right) {
    return left.fromDate === right.fromDate && left.toDate === right.toDate;
}

export function profitSummaryCandidates(root = document) {
    return [...root.querySelectorAll(PROFIT_SUMMARY_SELECTOR)]
        .filter((node) => node instanceof HTMLElement);
}

/**
 * React owns the executive summary. Moving its DOM node into the report grid
 * works until React refreshes the selected date; React then creates a new live
 * node at the original position while the moved node becomes a stale duplicate.
 * Prefer the candidate outside the placement host, because that is the newest
 * React-owned node. The placement observer swaps it into the centre column.
 */
export function newestLiveProfitCandidate(root = document, profitHost = null) {
    const candidates = profitSummaryCandidates(root);
    const outsideHost = candidates.filter((node) => node.parentElement !== profitHost);
    return outsideHost[outsideHost.length - 1]
        || candidates[candidates.length - 1]
        || null;
}

function compactGaStyles() {
    return `
        [data-dashboard-compact-ga4] [data-testid="ga4-realtime-section"] {
            height: 100%;
            padding: 0.75rem !important;
            border-width: 2px;
        }
        [data-dashboard-compact-ga4] [data-testid="ga4-realtime-cards-grid"] {
            grid-template-columns: minmax(0, 1fr) !important;
            gap: 0.75rem !important;
        }
        [data-dashboard-compact-ga4] [data-testid="ga4-key-events-card"] {
            display: none !important;
        }
        [data-dashboard-compact-ga4] [data-testid="ga4-top-pages-card"],
        [data-dashboard-compact-ga4] [data-testid="ga4-active-users-card"] {
            min-height: 0 !important;
            padding: 0.875rem !important;
        }
        [data-dashboard-compact-ga4] [data-testid="ga4-top-pages-card"] {
            max-height: 330px;
            overflow: hidden;
        }
        [data-dashboard-compact-ga4] [data-testid="ga4-active-users-card"] > div:last-child {
            height: 9rem !important;
        }
        [data-dashboard-compact-ga4] [data-testid="ga4-realtime-section-loading"] > div {
            grid-template-columns: minmax(0, 1fr) !important;
        }
        [data-dashboard-compact-ga4] [data-testid="ga4-realtime-section-loading"] > div > :nth-child(3) {
            display: none !important;
        }
    `;
}

/**
 * Build the requested full-screen report row without coupling Dashboard.jsx
 * to integrations that are mounted by the global Layout.
 *
 * Desktop order (RTL): GA4 live report on the right, executive profit summary
 * in the centre, and the yellow advertising-spend report on the left. On
 * smaller screens the same three reports stack vertically.
 *
 * The advertising card reads the exact `from/to` range exposed by
 * AdvancedFilters, so choosing today, yesterday, or a custom period refreshes
 * both the profit summary and advertising chart from the same date source.
 */
export default function DashboardAnalyticsPlacement({ active = false }) {
    const [hosts, setHosts] = useState(null);
    const [dateRange, setDateRange] = useState({ fromDate: "", toDate: "" });

    useLayoutEffect(() => {
        if (!active) {
            setHosts(null);
            return undefined;
        }

        let disposed = false;
        let frame = null;
        let currentGrid = null;
        let currentGaHost = null;
        let currentProfitHost = null;
        let currentAdsHost = null;
        let currentTrafficHost = null;
        let currentProfit = null;
        let originalParent = null;
        let originalNextSibling = null;

        const syncRange = () => {
            const next = readDashboardDateRange(document);
            setDateRange((current) => sameRange(current, next) ? current : next);
        };

        const rememberOriginalPosition = (candidate) => {
            originalParent = candidate?.parentElement || null;
            originalNextSibling = candidate?.nextSibling || null;
        };

        const restoreProfit = () => {
            if (!currentProfit || !originalParent?.isConnected) return;
            currentProfit.removeAttribute(LIVE_PROFIT_ATTRIBUTE);
            if (originalNextSibling?.parentElement === originalParent) {
                originalParent.insertBefore(currentProfit, originalNextSibling);
            } else {
                originalParent.appendChild(currentProfit);
            }
            currentProfit.classList.remove("h-full");
        };

        const removeHosts = () => {
            restoreProfit();
            if (currentGrid?.parentNode) currentGrid.parentNode.removeChild(currentGrid);
            if (currentTrafficHost?.parentNode) {
                currentTrafficHost.parentNode.removeChild(currentTrafficHost);
            }
            currentGrid = null;
            currentGaHost = null;
            currentProfitHost = null;
            currentAdsHost = null;
            currentTrafficHost = null;
            currentProfit = null;
            originalParent = null;
            originalNextSibling = null;
            if (!disposed) setHosts(null);
        };

        const createColumn = (testid) => {
            const column = document.createElement("div");
            column.className = "min-w-0 h-full";
            column.setAttribute("data-testid", testid);
            return column;
        };

        const placeProfitCandidate = (candidate) => {
            if (!candidate || !currentProfitHost) return false;
            if (candidate === currentProfit && candidate.parentElement === currentProfitHost) {
                return false;
            }

            // A date/filter refresh may have produced a new React-owned card in
            // the original Dashboard flow. Drop the stale moved node and place
            // the newest live card in the same centre host. This keeps one
            // interactive summary and prevents the frozen duplicate below.
            rememberOriginalPosition(candidate);
            if (currentProfit && currentProfit !== candidate) {
                currentProfit.removeAttribute(LIVE_PROFIT_ATTRIBUTE);
                if (currentProfit.parentElement === currentProfitHost) {
                    currentProfitHost.removeChild(currentProfit);
                }
            }
            currentProfit = candidate;
            currentProfit.setAttribute(LIVE_PROFIT_ATTRIBUTE, "true");
            currentProfit.classList.add("h-full");
            currentProfitHost.replaceChildren(currentProfit);
            return true;
        };

        const createPlacement = (candidate) => {
            rememberOriginalPosition(candidate);
            if (!originalParent) return false;

            currentGrid = document.createElement("div");
            currentGrid.className = "mt-6 grid grid-cols-1 gap-4 xl:grid-cols-3 xl:items-stretch";
            currentGrid.setAttribute("data-testid", GRID_TEST_ID);
            currentGrid.setAttribute("dir", "rtl");

            currentGaHost = createColumn(GA_HOST_TEST_ID);
            currentProfitHost = createColumn(PROFIT_HOST_TEST_ID);
            currentAdsHost = createColumn(ADS_HOST_TEST_ID);
            currentTrafficHost = document.createElement("div");
            currentTrafficHost.className = "mt-6";
            currentTrafficHost.setAttribute("data-testid", TRAFFIC_HOST_TEST_ID);

            // RTL auto-placement: first child = right, second = centre,
            // third = left.
            currentGrid.append(currentGaHost, currentProfitHost, currentAdsHost);
            originalParent.insertBefore(currentGrid, candidate);
            placeProfitCandidate(candidate);
            currentGrid.insertAdjacentElement("afterend", currentTrafficHost);

            setHosts({
                ga: currentGaHost,
                ads: currentAdsHost,
                traffic: currentTrafficHost,
            });
            return true;
        };

        const ensurePlacement = () => {
            if (disposed) return;
            pruneLegacyDashboardSections(document);
            syncRange();

            if (currentGrid && !currentGrid.isConnected) {
                removeHosts();
            }

            const candidate = newestLiveProfitCandidate(document, currentProfitHost);
            if (!candidate) {
                frame = window.requestAnimationFrame(ensurePlacement);
                return;
            }

            if (!currentGrid) {
                if (!createPlacement(candidate)) {
                    frame = window.requestAnimationFrame(ensurePlacement);
                }
                return;
            }

            placeProfitCandidate(candidate);
        };

        ensurePlacement();
        const observer = new MutationObserver(ensurePlacement);
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ["data-from-date", "data-to-date"],
        });

        return () => {
            disposed = true;
            if (frame) window.cancelAnimationFrame(frame);
            observer.disconnect();
            removeHosts();
        };
    }, [active]);

    if (!active || !hosts) return null;

    return (
        <>
            {createPortal(
                <div className="h-full" data-dashboard-compact-ga4="true">
                    <style>{compactGaStyles()}</style>
                    <GoogleAnalyticsRealtimeCards />
                </div>,
                hosts.ga,
            )}
            {createPortal(
                <DashboardAdsSpendCard
                    fromDate={dateRange.fromDate}
                    toDate={dateRange.toDate}
                />,
                hosts.ads,
            )}
            {createPortal(
                <GoogleAnalyticsTrafficSourcesCard />,
                hosts.traffic,
            )}
        </>
    );
}

export {
    ADS_HOST_TEST_ID,
    FILTER_SELECTOR,
    GA_HOST_TEST_ID,
    GRID_TEST_ID,
    HOST_TEST_ID,
    LEGACY_SECTION_HEADINGS,
    LEGACY_SECTION_SELECTORS,
    LIVE_PROFIT_ATTRIBUTE,
    PROFIT_HOST_TEST_ID,
    PROFIT_SUMMARY_SELECTOR,
    TRAFFIC_HOST_TEST_ID,
    readDashboardDateRange,
};
