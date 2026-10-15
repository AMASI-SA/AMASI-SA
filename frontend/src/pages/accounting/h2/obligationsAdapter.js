import api from "../../../lib/api";

export const G_BASE = "/accounting-module/onboarding";
export const G_VIEWS = {
    facts: { label: "الالتزامات والضرائب", path: "/typed-facts" },
    fees: { label: "سياسات رسوم المزود", path: "/fee-policies" },
    prepaid: { label: "المقدم والالتزامات المتكررة", path: "/prepaid-candidates" },
    persons: { label: "الأطراف الخارجية", path: "/identities/external_person" },
};
export const FACT_LABELS = { prepaid_expense: "مصروف مقدم موثق", accrued_expense: "مصروف مستحق", other_payable: "التزام آخر", other_receivable: "ذمة مدينة أخرى", sales_vat_payable: "ضريبة مبيعات مستحقة", input_vat: "ضريبة مدخلات" };

export async function loadObligations(view, cutover, client = api) {
    if (!Object.hasOwn(G_VIEWS, view)) throw new Error("unknown_view");
    const definitions = (await client.get(`${G_BASE}/definitions`)).data;
    if (definitions?.ssot_setup_version !== 1 || definitions?.external_person_registry !== "mz2_external_persons_v2") {
        return { state: "blocked", reason: "native_setup_contract_not_ready" };
    }
    if (view === "prepaid" && !/^\d{4}-\d{2}-\d{2}$/.test(cutover || "")) return { state: "date_required" };
    const response = await client.get(`${G_BASE}${G_VIEWS[view].path}`, view === "prepaid" ? { params: { cutover } } : undefined);
    const data = response.data;
    if (data?.status === "not_ready" || data?.state === "not_ready") return { state: "blocked", reason: "native_source_not_ready" };
    if (!Array.isArray(data?.items)) throw new Error("native_response_invalid");
    if (view === "prepaid" && data.source !== "operating_recurring_obligations_v2") return { state: "blocked", reason: "native_source_not_ready" };
    return { state: "ready", items: data.items, blockers: Array.isArray(data.blockers) ? data.blockers : [] };
}

export function obligationsFailure(error) {
    const status = error?.response?.status;
    return [404, 409, 423, 501, 503].includes(status)
        ? { state: "blocked", reason: "native_source_not_ready" }
        : { state: "error" };
}
