import { createOnboardingSessionController } from "./onboardingSessionController";
jest.mock("./accountingOnboarding", () => ({}));
const session = version => ({ id: "s", version, status: "draft", sections: {} });
const make = transport => createOnboardingSessionController(transport, () => "stable-fixture-key");
test("setup saves serialize with latest global version and resume from server", async () => {
    const transport = { getOnboardingSession: jest.fn().mockResolvedValue(session(4)), saveOnboardingSection: jest.fn().mockResolvedValueOnce({ session: session(5) }).mockResolvedValueOnce({ session: session(6) }) };
    const controller = make(transport); await controller.load("s");
    await Promise.all([controller.saveSection("banks_cash", { status: "incomplete", data: { lines: [] } }), controller.saveSection("providers", { status: "incomplete", data: { lines: [] } })]);
    expect(transport.saveOnboardingSection.mock.calls.map(call => call[2].version)).toEqual([4, 5]);
    expect(controller.snapshot().version).toBe(6);
    const resumed = make({ getOnboardingSession: jest.fn().mockResolvedValue(session(6)) }); await resumed.load("s"); expect(resumed.snapshot()).toEqual(controller.snapshot());
});
test("lost response retry uses identical key/version/payload; queued edits never overwrite it", async () => {
    const transport = { getOnboardingSession: jest.fn().mockResolvedValue(session(1)), saveOnboardingSection: jest.fn().mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce(session(2)) };
    const controller = make(transport); await controller.load("s");
    const payload = { status: "incomplete", data: { lines: [{ original_amount: "0.00" }] } };
    await expect(controller.saveSection("banks_cash", payload)).rejects.toThrow("network"); payload.data.lines[0].original_amount = "99";
    await expect(controller.saveSection("equity", payload)).rejects.toThrow("pending_request");
    await controller.retry(); expect(transport.saveOnboardingSection.mock.calls[0]).toEqual(transport.saveOnboardingSection.mock.calls[1]);
    expect(transport.saveOnboardingSection.mock.calls[1][2].data.lines[0].original_amount).toBe("0.00");
});
test("stale CAS requires explicit reload, never blind retry", async () => {
    const transport = { getOnboardingSession: jest.fn().mockResolvedValue(session(1)), saveOnboardingSection: jest.fn().mockRejectedValue({ response: { status: 409 } }) };
    const controller = make(transport); await controller.load("s");
    await expect(controller.saveSection("providers", {})).rejects.toBeDefined();
    expect(controller.needsReload()).toBe(true); await expect(controller.retry()).rejects.toThrow("reload_required");
    expect(transport.saveOnboardingSection).toHaveBeenCalledTimes(1);
    transport.getOnboardingSession.mockResolvedValue(session(7)); await controller.load("s"); expect(controller.snapshot().version).toBe(7); expect(controller.hasPendingRequest()).toBe(false);
});
test("review locks all setup edits and controller contains no live actions", async () => {
    const transport = { getOnboardingSession: jest.fn().mockResolvedValue(session(1)), reviewOnboardingSession: jest.fn().mockResolvedValue({ session: { ...session(2), status: "reviewed" } }) };
    const controller = make(transport); await controller.load("s"); await controller.review("fixture");
    await expect(controller.saveSection("equity", {})).rejects.toThrow("locked");
    expect(Object.keys(controller).join(" ")).not.toMatch(/post|activate|handoff|transition/i);
});
