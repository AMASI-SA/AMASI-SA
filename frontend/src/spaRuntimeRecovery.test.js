import {
  SPA_RECOVERY_POLICY,
  canAttemptAutomaticRecovery,
  isApplicationShellHealthy,
  readSpaFailures,
  recordSpaFailure,
  safeRoute,
} from "./spaRuntimeRecovery";

class MemoryStorage {
  constructor() {
    this.values = new Map();
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }

  removeItem(key) {
    this.values.delete(key);
  }
}

describe("SPA runtime recovery", () => {
  test("redacts sensitive query values from diagnostics", () => {
    expect(safeRoute({
      pathname: "/integrations-v2/snapchat/callback",
      search: "?code=secret-code&state=secret-state&provider=snapchat",
    })).toBe(
      "/integrations-v2/snapchat/callback?code=%5Bredacted%5D&state=%5Bredacted%5D&provider=snapchat",
    );
  });

  test("stores only a bounded number of diagnostics", () => {
    const storage = new MemoryStorage();
    for (let index = 0; index < SPA_RECOVERY_POLICY.max_failures + 5; index += 1) {
      recordSpaFailure(
        "test_failure",
        new Error(`failure-${index}`),
        {},
        storage,
        1000 + index,
      );
    }

    const failures = readSpaFailures(storage);
    expect(failures).toHaveLength(SPA_RECOVERY_POLICY.max_failures);
    expect(failures.at(-1).error.message).toBe(
      `failure-${SPA_RECOVERY_POLICY.max_failures + 4}`,
    );
  });

  test("allows one automatic recovery per route inside the cooldown", () => {
    const storage = new MemoryStorage();
    const route = "/fulfillment-v2";

    expect(canAttemptAutomaticRecovery({
      route,
      storage,
      now: 1000,
      cooldownMs: 5000,
    })).toBe(true);
    expect(canAttemptAutomaticRecovery({
      route,
      storage,
      now: 2000,
      cooldownMs: 5000,
    })).toBe(false);
    expect(canAttemptAutomaticRecovery({
      route,
      storage,
      now: 7000,
      cooldownMs: 5000,
    })).toBe(true);
  });

  test("recognizes the application shell and crash fallback as healthy UI", () => {
    const root = document.createElement("div");
    expect(isApplicationShellHealthy(root)).toBe(false);

    root.innerHTML = '<main data-testid="main-content"></main>';
    expect(isApplicationShellHealthy(root)).toBe(true);

    root.innerHTML = '<main data-mezan-crash-recovery="true"></main>';
    expect(isApplicationShellHealthy(root)).toBe(true);

    root.innerHTML = '<main data-testid="auth-loading"></main>';
    expect(isApplicationShellHealthy(root)).toBe(true);

    root.innerHTML = '<main data-testid="auth-unavailable"></main>';
    expect(isApplicationShellHealthy(root)).toBe(true);
  });
});
