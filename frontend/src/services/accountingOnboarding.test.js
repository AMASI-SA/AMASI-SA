import api from "../lib/api";
import * as service from "./accountingOnboarding";
const financialBase = "/api/financial-provider-apps/accounting-module/financial-accounts";
jest.mock("../lib/api", () => ({ get: jest.fn(), put: jest.fn(), post: jest.fn() }));
beforeEach(() => { jest.clearAllMocks(); for (const method of Object.values(api)) method.mockResolvedValue({ data: { fixture: true } }); });
test("session persistence uses only Track A paths and explicit CAS payload, no financial operation", async () => {
    const payload = { version: 4, idempotency_key: "stable-key", status: "incomplete", reason: "fixture", evidence_file_id: null, data: { lines: [] } };
    await service.saveOnboardingSection("id/a", "providers", payload);
    expect(api.put).toHaveBeenCalledWith("/accounting-module/onboarding/sessions/id%2Fa/sections/providers", payload);
    expect(api.post).not.toHaveBeenCalled();
    expect(() => service.saveOnboardingSection("id", "advertising", payload)).toThrow("onboarding_section_invalid");
    for (const name of Object.keys(service)) expect(name).not.toMatch(/postOpening|activate|transition|handoff/i);
});
test("resume, identities, readiness and preview follow fixed V1 contract", async () => {
    await service.getOnboardingSession("session"); await service.getOnboardingIdentities("external_person"); await service.getOnboardingReadiness("session");
    expect(api.get.mock.calls.map(args => args[0])).toEqual(["/accounting-module/onboarding/sessions/session", "/accounting-module/onboarding/identities/external_person", "/accounting-module/onboarding/sessions/session/readiness"]);
    await service.previewOnboardingSession("session", { version: 2, idempotency_key: "key", note: "fixture" });
    expect(api.post.mock.calls[0][0]).toBe("/accounting-module/onboarding/sessions/session/preview");
});
test("evidence sends original file and server purpose/section, never client hash as proof", async () => {
    const file = new File(["fixture bytes"], "proof.txt");
    await service.uploadOnboardingEvidence({ file, financialBase, purpose: "opening_balance", sectionId: "suppliers" });
    const [url, form] = api.post.mock.calls[0];
    expect(url).toBe("/financial-provider-apps/accounting-module/financial-accounts/opening-balances/evidence");
    expect(form.get("file")).toBe(file); expect(form.get("section_id")).toBe("suppliers"); expect(form.has("sha256")).toBe(false);
    expect(() => service.uploadOnboardingEvidence({ file, financialBase, purpose: "cutover", sectionId: "suppliers" })).toThrow();
});
test("errors never expose raw server exception text", () => {
    const message = service.onboardingErrorMessage({ response: { status: 500, data: { detail: "secret internal trace" } } });
    expect(message).not.toContain("secret");
    expect(service.onboardingErrorMessage({ response: { status: 409, data: { detail: { code: "onboarding_version_conflict" } } } })).toContain("أعد تحميل");
});

test("external person uses existing general registry exact id", async () => {
    const person = { id: "persisted-id", kind: "general", name: "Person", phone: "123", notes: "Evidence" };
    api.post.mockResolvedValue({ data: person });
    const result = await service.createOnboardingExternalPerson({ name: person.name, phone: person.phone, notes: person.notes, entity_id: "forged", kind: "supplier" });
    expect(api.post).toHaveBeenCalledWith("/counterparties", { kind: "general", name: "Person", phone: "123", notes: "Evidence" }); expect(result.id).toBe("persisted-id");
    api.post.mockResolvedValue({ data: { ...person, id: undefined, entity_id: "fake" } });
    await expect(service.createOnboardingExternalPerson({ name: "Person" })).rejects.toThrow("response_invalid");
});

test("definitions and account lists preserve published envelopes", async () => {
    const definitions = { schema_version: 1, financial_base: financialBase, sections: ["banks_cash"], opening_categories: { provider_receivable: { section: "providers" } }, financial_account_rules: { cash: { section: "banks_cash" } }, live_actions_enabled: false };
    api.get.mockResolvedValueOnce({ data: definitions }).mockResolvedValueOnce({ data: { items: [{ id: "cash", account_type: "cash", currency: "SAR" }] } });
    expect(await service.getOnboardingDefinitions()).toEqual(definitions); expect(await service.getOnboardingFinancialAccounts(definitions.financial_base)).toEqual({ items: [{ id: "cash", account_type: "cash", currency: "SAR" }] });
    expect(api.get.mock.calls.map(call => call[0])).toEqual(["/accounting-module/onboarding/definitions", "/financial-provider-apps/accounting-module/financial-accounts"]);
});

test("known local validation errors are useful while unknown text stays private", () => {
    for (const code of ["onboarding_amount_invalid", "onboarding_cutover_required", "onboarding_inventory_account_required", "onboarding_account_currency_mismatch", "onboarding_financial_account_unresolved", "onboarding_not_applicable_conflict", "onboarding_reload_required"]) {
        const message = service.onboardingErrorMessage(new Error(code)); expect(message).not.toContain(code); expect(message).not.toContain("تعذر إكمال الطلب");
    }
    expect(service.onboardingErrorMessage(new Error("secret stack data"))).not.toContain("secret");
    expect(typeof service.onboardingErrorMessage({ response: { data: { detail: "__proto__" } } })).toBe("string");
    expect(service.onboardingErrorMessage({ response: { status: 409, data: { detail: { message: "duplicate" } } } })).toContain("موجود");
});


test("financial endpoints require exact definitions base and never fall back", () => {
    for (const invalid of [undefined, "", "/accounting-module/financial-accounts", "/api/accounting-module/financial-accounts", financialBase + "/", "https://example.com" + financialBase]) {
        expect(() => service.getOnboardingFinancialAccounts(invalid)).toThrow("onboarding_financial_base_invalid");
        expect(() => service.uploadOnboardingEvidence({ financialBase: invalid, file: new File(["x"], "x.txt"), purpose: "cutover" })).toThrow("onboarding_financial_base_invalid");
    }
    expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});

test("supplier link and explicit entity balance blockers show safe useful messages", () => {
    for (const code of ["supplier_link_required", "entity_balance_required", "onboarding_supplier_link_required", "onboarding_entity_balance_required"]) {
        const message = service.onboardingErrorMessage({ response: { data: { detail: { code } } } });
        expect(message).not.toContain("تعذر إكمال الطلب"); expect(message).not.toContain(code);
    }
});
