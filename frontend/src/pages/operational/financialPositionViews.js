// These are pages over the same saved financial snapshot. Navigation never
// calculates money or starts a source refresh.
export const FINANCIAL_PAGES = [
    { id: "summary", label: "الملخص المالي", description: "الإجماليات الستة للأرصدة والتوقعات والربح" },
    { id: "providers", label: "منصات الدفع", description: "مستحقات المنصات وعمولاتها ومطابقة التحصيل" },
    { id: "couriers", label: "شركات الشحن", description: "أجور الشركات والتحصيل والتفاصيل الموثقة" },
    { id: "drivers", label: "مناديب المتجر", description: "أجور المناديب والعهد والتحصيل" },
    { id: "employees", label: "الموظفون والعهد", description: "الرواتب والأرصدة وعهد الموظفين" },
    { id: "accounts", label: "البنوك والصناديق", description: "السيولة والبنوك وأجهزة الشبكة والصناديق" },
    { id: "suppliers", label: "الموردون", description: "مستحقات الموردين ومطابقة الفواتير" },
    { id: "advertising", label: "الإعلانات", description: "الأرصدة الإعلانية وتحويل العملات" },
    { id: "other", label: "المحافظ والجهات الأخرى", description: "محافظ العملاء وبقية الأرصدة" },
    { id: "expected", label: "الطلبات والتوقعات", description: "الطلبات المفتوحة والتكاليف والتحصيل المتوقع وما يحتاج مطابقة" },
];

export const financialPage = view => FINANCIAL_PAGES.find(page => page.id === view) || FINANCIAL_PAGES[0];

export function financialViewFromSearch(search) {
    const value = new URLSearchParams(search).get("view");
    return FINANCIAL_PAGES.some(page => page.id === value) ? value : "summary";
}

export function financialViewHref(view, location = window.location) {
    const query = new URLSearchParams(location.search);
    query.set("view", financialPage(view).id);
    return `${location.pathname}?${query}${location.hash || ""}`;
}

export function hasFinancialSnapshot(report) {
    return Number.isSafeInteger(report?.revision) && report.revision >= 0
        && typeof report.session_scope === "string" && report.session_scope.length > 0
        && typeof report.as_of === "string" && Number.isFinite(Date.parse(report.as_of))
        && typeof report.started_at === "string" && Number.isFinite(Date.parse(report.started_at));
}

export const financialSnapshotKey = report => JSON.stringify([
    report?.session_scope, report?.revision, report?.as_of, report?.started_at,
]);

export function sameFinancialSnapshot(expected, received) {
    return hasFinancialSnapshot(expected) && hasFinancialSnapshot(received)
        && financialSnapshotKey(expected) === financialSnapshotKey(received);
}

export function financialSnapshotParams(report) {
    return { expected_revision: report.revision, expected_as_of: report.as_of,
        expected_started_at: report.started_at };
}
