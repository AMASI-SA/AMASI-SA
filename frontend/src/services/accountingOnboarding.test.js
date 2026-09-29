import api from "../lib/api";
import * as service from "./accountingOnboarding";
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
    await service.uploadOnboardingEvidence({ file, purpose: "opening_balance", sectionId: "suppliers" });
    const [url, form] = api.post.mock.calls[0];
    expect(url).toBe("/accounting-module/financial-accounts/opening-balances/evidence");
    expect(form.get("file")).toBe(file); expect(form.get("section_id")).toBe("suppliers"); expect(form.has("sha256")).toBe(false);
    expect(() => service.uploadOnboardingEvidence({ file, purpose: "cutover", sectionId: "suppliers" })).toThrow();
});
test("errors never expose raw server exception text", () => {
    const message = service.onboardingErrorMessage({ response: { status: 500, data: { detail: "secret internal trace" } } });
    expect(message).not.toContain("secret");
    expect(service.onboardingErrorMessage({ response: { status: 409, data: { detail: { code: "onboarding_version_conflict" } } } })).toContain("أعد تحميل");
});
