import fs from "fs";
import path from "path";

import {
    AUTH_RECOVERY_DELAYS_MS,
    authRecoveryDelayMs,
    browserCanRetryAuth,
} from "./authRecoveryPolicy";

describe("automatic auth recovery policy", () => {
    test("uses a bounded exponential-style retry schedule", () => {
        expect(AUTH_RECOVERY_DELAYS_MS).toEqual([
            2_000,
            5_000,
            10_000,
            20_000,
            30_000,
        ]);
        expect(authRecoveryDelayMs(-1)).toBe(2_000);
        expect(authRecoveryDelayMs(0)).toBe(2_000);
        expect(authRecoveryDelayMs(2)).toBe(10_000);
        expect(authRecoveryDelayMs(99)).toBe(30_000);
    });

    test("does not probe while offline or while the page is hidden", () => {
        expect(browserCanRetryAuth({ online: true, hidden: false })).toBe(true);
        expect(browserCanRetryAuth({ online: false, hidden: false })).toBe(false);
        expect(browserCanRetryAuth({ online: true, hidden: true })).toBe(false);
    });

    test("AuthProvider retries on timer, online, focus, and visibility recovery", () => {
        const source = fs.readFileSync(
            path.join(__dirname, "AuthContext.jsx"),
            "utf8",
        );

        expect(source).toContain("authRecoveryDelayMs(");
        expect(source).toContain('window.addEventListener("online"');
        expect(source).toContain('window.addEventListener("focus"');
        expect(source).toContain('document.addEventListener("visibilitychange"');
        expect(source).toContain("startAuthProbe();");
        expect(source).not.toContain("window.location.reload()");
    });
});
