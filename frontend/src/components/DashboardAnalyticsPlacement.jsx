/*
 * Retired with the legacy dashboard UI.
 *
 * The advanced dashboard renders its own GA4, advertising, and executive
 * profit panels. Keeping the former DOM-relocation runtime active would mount
 * duplicate polling components and retain document-wide observers.
 */
export default function DashboardAnalyticsPlacement() {
    return null;
}

export function pruneLegacyDashboardSections() {
    // Compatibility export for older focused tests/imports. No DOM mutation is
    // required now that the legacy dashboard implementation is not rendered.
}
