import api from "../lib/api";
import { applyGoogleSuggestionToEmptyFields, suggestGoogleCommercialPlan, validateGoogleCommercialPlanSuggestion } from "./googleCommercialPlanSuggestion";

jest.mock("../lib/api", () => ({ __esModule: true, default: { post: jest.fn() } }));
const instruction = "ركز على الشتاء بالسعودية واستهدف الرجال الباحثين عن هدية من 2026-11-01 إلى 2026-12-31 والرسالة دفء الشتاء https://example.com/shawls";
const response = () => ({ instruction, fields: { season: "الشتاء", starts_on: "2026-11-01", ends_on: "2026-12-31", market: "SA",
    audience: "الرجال الباحثين عن هدية", message: "دفء الشتاء", category_url: "https://example.com/shawls" },
    evidence: { season: "الشتاء", starts_on: "2026-11-01", ends_on: "2026-12-31", market: "السعودية", audience: "الرجال الباحثين عن هدية", message: "دفء الشتاء", category_url: "https://example.com/shawls" },
    missing_fields: [], manual_fields: ["category_ids", "pinned_product_ids", "excluded_product_ids", "exclusions", "limits"],
    status: "unreviewed_suggestion", execution_authorized: false, provider_writes: 0, persisted: false });
beforeEach(() => api.post.mockReset());

test("sends only current instruction and abort signal, validates unpersisted source-backed response", async () => {
    api.post.mockResolvedValue({ data: response() });
    const signal = new AbortController().signal;
    expect(await suggestGoogleCommercialPlan(` ${instruction} `, { signal })).toEqual(response());
    expect(api.post).toHaveBeenCalledWith("/integrations-v2/google_ads/commercial-plan-suggestions", { instruction }, { signal });
});

test.each(["", " ", null, 1, "a".repeat(6001)])("invalid input is rejected without API call", async (input) => {
    await expect(suggestGoogleCommercialPlan(input)).rejects.toThrow(); expect(api.post).not.toHaveBeenCalled();
});

test.each([
    (r) => { r.execution_authorized = true; }, (r) => { r.provider_writes = 1; }, (r) => { r.persisted = true; },
    (r) => { r.fields.limits = { daily_budget_native: "999" }; }, (r) => { r.execute = true; },
    (r) => { r.evidence.market = "قطر"; },
    (r) => { r.fields.message = "خصم 90%"; }, (r) => { r.evidence.starts_on = "الشتاء"; },
    (r) => { r.fields.market = "SAU"; }, (r) => { r.fields.market = "QA"; }, (r) => { r.manual_fields = []; },
    (r) => { r.missing_fields = ["season"]; }, (r) => { r.fields.season = null; },
    (r) => { delete r.fields.ends_on; }, (r) => { r.evidence.execute = "الشتاء"; },
    (r) => { r.fields.starts_on = "2026-02-30"; r.evidence.starts_on = "2026-02-30"; r.instruction += " 2026-02-30"; },
    (r) => { r.fields.category_url = "https://user:secret@example.com/shawls"; r.evidence.category_url = r.fields.category_url; r.instruction += ` ${r.fields.category_url}`; },
])("malformed or scope-expanding responses are rejected", (mutate) => {
    const result = response(); mutate(result);
    expect(() => validateGoogleCommercialPlanSuggestion(result, result.instruction)).toThrow();
});

test("response to different instruction is rejected", () => {
    expect(() => validateGoogleCommercialPlanSuggestion(response(), "توجيه جديد")).toThrow();
});

test.each(["٢٠٢٦-١١-٠١", "۲۰۲۶-۱۱-۰۱"])("accepts deterministic digit normalization for ISO dates from %s", (date) => {
    const result = response();
    result.instruction = instruction.replace("2026-11-01", date); result.evidence.starts_on = date;
    expect(validateGoogleCommercialPlanSuggestion(result, result.instruction)).toEqual(result);
    expect(result.evidence.starts_on).toBe(date);
});

test("accepts explicit missing fields without guessing dates or products", () => {
    const result = response(); result.fields.starts_on = null; result.fields.ends_on = null;
    delete result.evidence.starts_on; delete result.evidence.ends_on; result.missing_fields = ["starts_on", "ends_on"];
    expect(validateGoogleCommercialPlanSuggestion(result, instruction)).toEqual(result);
});

test("application preserves all nonempty owner fields and never adds limits or identifiers", () => {
    const form = { instruction, season: "اختيار المالك", starts_on: "", ends_on: "", market: "QA", audience: "", message: "", category_url: "",
        limits: { currency_code: "SAR", daily_budget_native: "50" }, category_ids: "9", pinned_product_ids: "123", excluded_product_ids: "456", exclusions: "لا الأطفال", calendar: "hijri_season" };
    const result = applyGoogleSuggestionToEmptyFields(form, response());
    expect(result).toEqual({ ...form, starts_on: "2026-11-01", ends_on: "2026-12-31", audience: "الرجال الباحثين عن هدية", message: "دفء الشتاء", category_url: "https://example.com/shawls" });
    expect(form.audience).toBe(""); expect(result.limits).toBe(form.limits);
});
