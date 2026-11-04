import { createDashboardFetchAdmission } from "./dashboardFetchAdmission";

test("background polling waits for the explicit dashboard period load", () => {
    const gate = createDashboardFetchAdmission();
    expect(gate.start(false)).toBe(true);
    expect(gate.start(true)).toBe(false);
    expect(gate.snapshot()).toEqual({ foregroundInFlight: 1, backgroundInFlight: 0 });
    gate.finish(false);
    expect(gate.start(true)).toBe(true);
    gate.finish(true);
});

test("changing filters may issue two explicit requests, but neither overlaps background polling", () => {
    const gate = createDashboardFetchAdmission();
    expect(gate.start(false)).toBe(true);
    expect(gate.start(false)).toBe(true);
    gate.finish(false);
    expect(gate.start(true)).toBe(false);
    gate.finish(false);
    expect(gate.start(true)).toBe(true);
    gate.finish(true);
});

test("dashboard background reload is single-flight", () => {
    const gate = createDashboardFetchAdmission();
    expect(gate.start(true)).toBe(true);
    expect(gate.start(true)).toBe(false);
    gate.finish(true);
    expect(gate.start(true)).toBe(true);
    gate.finish(true);
});

test("foreground request is never blocked behind background reporting", () => {
    const gate = createDashboardFetchAdmission();
    expect(gate.start(true)).toBe(true);
    expect(gate.start(false)).toBe(true);
    gate.finish(true);
    expect(gate.start(true)).toBe(false);
    gate.finish(false);
    expect(gate.start(true)).toBe(true);
    gate.finish(true);
});

test("failed/cancelled requests release without negative counts", () => {
    const gate = createDashboardFetchAdmission();
    gate.finish(true);
    gate.finish(false);
    expect(gate.snapshot()).toEqual({ foregroundInFlight: 0, backgroundInFlight: 0 });
    expect(gate.start(false)).toBe(true);
    gate.finish(false);
    expect(gate.snapshot()).toEqual({ foregroundInFlight: 0, backgroundInFlight: 0 });
});
