import { createOnboardingSessionController } from "./onboardingSessionController";
jest.mock("./accountingOnboarding", () => ({}));
const session = version => ({ id: "s", schema_version: 1, existing: false, version, status: "draft", sections: {} });
const make = transport => createOnboardingSessionController(transport, () => "stable-fixture-key");
test("setup saves serialize with latest global version and resume from server", async () => {
    const transport = { getOnboardingSession: jest.fn().mockResolvedValue(session(4)), saveOnboardingSection: jest.fn().mockResolvedValueOnce(session(5)).mockResolvedValueOnce(session(6)) };
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
    const transport = { getOnboardingSession: jest.fn().mockResolvedValue(session(1)), reviewOnboardingSession: jest.fn().mockResolvedValue({ ...session(2), status: "reviewed" }) };
    const controller = make(transport); await controller.load("s"); await controller.review("fixture");
    await expect(controller.saveSection("equity", {})).rejects.toThrow("locked");
    expect(Object.keys(controller).join(" ")).not.toMatch(/post|activate|handoff|transition/i);
});

test("direct V1 envelope rejects wrapper aliases and unsupported schema", async () => {
    for (const invalid of [{ session: session(1) }, { ...session(1), id: undefined, session_id: "s" }, { ...session(1), schema_version: 2 }, { ...session(1), existing: undefined }, { ...session(1), sections: [] }]) {
        const controller = make({ getOnboardingSession: jest.fn().mockResolvedValue(invalid) });
        await expect(controller.load("s")).rejects.toThrow("onboarding_response_invalid"); expect(controller.snapshot()).toBeNull();
    }
});

test("queued payload snapshot and controller-owned CAS cannot be mutated by caller", async () => {
    let release;
    const transport = { getOnboardingSession: jest.fn().mockResolvedValue(session(1)), saveOnboardingSection: jest.fn().mockImplementationOnce(() => new Promise(resolve => { release = resolve; })).mockResolvedValueOnce(session(3)) };
    const controller = make(transport); await controller.load("s");
    const first = controller.saveSection("banks_cash", { data: { lines: [] } });
    const payload = { version: 999, idempotency_key: "caller-key", data: { lines: [{ original_amount: "0.00" }] } };
    const second = controller.saveSection("providers", payload); payload.data.lines[0].original_amount = "900";
    await Promise.resolve(); release(session(2)); await Promise.all([first, second]);
    expect(transport.saveOnboardingSection.mock.calls[1][2]).toEqual({ version: 2, idempotency_key: "stable-fixture-key", data: { lines: [{ original_amount: "0.00" }] } });
});

test("same key different payload 409 requires reload without rekeying", async () => {
    const requests = new Map(); let stored = session(1);
    const transport = { getOnboardingSession: jest.fn(async () => ({ ...stored })), saveOnboardingSection: jest.fn(async (id, section, payload) => {
        const digest = JSON.stringify({ section, payload }), old = requests.get(payload.idempotency_key);
        if (old && old !== digest) throw { response: { status: 409, data: { detail: { code: "onboarding_idempotency_conflict" } } } };
        if (old) return { ...stored, existing: true };
        requests.set(payload.idempotency_key, digest); stored = session(stored.version + 1); return { ...stored };
    }) };
    const controller = make(transport); await controller.load("s"); await controller.saveSection("providers", { reason: "first" });
    await expect(controller.saveSection("providers", { reason: "second" })).rejects.toMatchObject({ response: { status: 409 } });
    expect(controller.needsReload()).toBe(true); await expect(controller.retry()).rejects.toThrow("reload_required");
    expect(transport.saveOnboardingSection).toHaveBeenCalledTimes(2); expect(controller.snapshot().version).toBe(2);
});

test("retry permits latest server replay version and retains identical intent", async () => {
    const transport = { getOnboardingSession: jest.fn().mockResolvedValue(session(1)), saveOnboardingSection: jest.fn().mockRejectedValueOnce(new Error("lost")).mockResolvedValueOnce({ ...session(4), existing: true, status: "reviewed" }) };
    const controller = make(transport); await controller.load("s"); await expect(controller.saveSection("equity", {})).rejects.toThrow("lost");
    await controller.retry(); expect(controller.snapshot().version).toBe(4); await expect(controller.saveCutover({})).rejects.toThrow("reload_required");
    expect(transport.saveOnboardingSection.mock.calls[0]).toEqual(transport.saveOnboardingSection.mock.calls[1]);
});

test("invalid successful mutation requires reload while retaining intent", async () => {
    const controller = make({ getOnboardingSession: jest.fn().mockResolvedValue(session(1)), saveOnboardingSection: jest.fn().mockResolvedValue(session(4)) });
    await controller.load("s"); await expect(controller.saveSection("equity", {})).rejects.toThrow("response_version_invalid");
    expect(controller.needsReload()).toBe(true); expect(controller.hasPendingRequest()).toBe(true); expect(controller.snapshot().version).toBe(1);
    await expect(controller.retry()).rejects.toThrow("reload_required");
});

test("handed-off session loads read-only", async () => {
    const controller = make({ getOnboardingSession: jest.fn().mockResolvedValue({ ...session(7), status: "handed_off" }) }); await controller.load("s");
    await expect(controller.saveCutover({})).rejects.toThrow("locked"); await expect(controller.preview("note")).rejects.toThrow("locked"); await expect(controller.review("note")).rejects.toThrow("locked");
});

test("create retry preserves original key and exact server identity", async () => {
    const transport = { createOnboardingSession: jest.fn().mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce({ ...session(1), id: "onboarding-server-id", existing: true }) };
    const controller = make(transport); await expect(controller.create({ cutover_at: "2026-10-01T00:00:00+03:00" })).rejects.toThrow("network"); await controller.retry();
    expect(transport.createOnboardingSession.mock.calls[0]).toEqual(transport.createOnboardingSession.mock.calls[1]); expect(controller.snapshot().id).toBe("onboarding-server-id");
});


test("advanced replay requires explicit load before any new section replacement", async () => {
    const transport = { getOnboardingSession: jest.fn().mockResolvedValue(session(1)),
        saveOnboardingSection: jest.fn().mockRejectedValueOnce(new Error("lost response"))
            .mockResolvedValueOnce({ ...session(4), existing: true }).mockResolvedValueOnce(session(5)) };
    const controller = make(transport); await controller.load("s");
    await expect(controller.saveSection("banks_cash", { reason: "original" })).rejects.toThrow("lost response");
    await controller.retry();
    expect(controller.snapshot().version).toBe(4); expect(controller.needsReload()).toBe(true);
    expect(controller.hasPendingRequest()).toBe(false);
    await expect(controller.saveSection("providers", { reason: "stale local" })).rejects.toThrow("reload_required");
    await expect(controller.preview("preview")).rejects.toThrow("reload_required");
    await expect(controller.retry()).rejects.toThrow("reload_required");
    expect(transport.saveOnboardingSection).toHaveBeenCalledTimes(2);
    transport.getOnboardingSession.mockResolvedValue(session(4)); await controller.load("s");
    expect(controller.needsReload()).toBe(false);
    await controller.saveSection("providers", { reason: "restored" });
    expect(transport.saveOnboardingSection.mock.calls[2][2].version).toBe(4);
});
