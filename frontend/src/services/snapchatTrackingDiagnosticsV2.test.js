import api from "../lib/api";
import {
    diagnoseSnapchatTracking,
    normalizeSnapchatTrackingDiagnostics,
} from "./snapchatTrackingDiagnosticsV2";

jest.mock("../lib/api", () => ({
    post: jest.fn(),
}));

describe("Snapchat tracking diagnostics V2 client", () => {
    beforeEach(() => {
        api.post.mockReset();
    });

    test("accepts a complete read-only diagnostics response", () => {
        const result = normalizeSnapchatTrackingDiagnostics({
            run_id: "run-1",
            status: "complete",
            accounts_attempted: 9,
            accounts_complete: 9,
            pixels_found: 2,
            pixels_complete: 2,
            domains_observed: 3,
            diagnostics_saved: 8,
            recommendations_count: 4,
            errors_count: 0,
            source_only: true,
            provider_write_reached: false,
            event_write_reached: false,
            accounting_write_reached: false,
            qoyod_write_reached: false,
        });
        expect(result).toMatchObject({
            run_id: "run-1",
            status: "complete",
            accounts_complete: 9,
            pixels_found: 2,
            domains_observed: 3,
            recommendations_count: 4,
        });
    });

    test("fails closed if any provider or event write is reported", () => {
        const result = normalizeSnapchatTrackingDiagnostics({
            status: "complete",
            source_only: true,
            provider_write_reached: false,
            event_write_reached: true,
            accounting_write_reached: false,
            qoyod_write_reached: false,
        });
        expect(result.status).toBe("failed");
    });

    test("posts the bounded seven-day diagnostic request", async () => {
        api.post.mockResolvedValue({
            data: {
                run_id: "run-2",
                status: "partial",
                pixels_found: 1,
                pixels_complete: 0,
                domains_observed: 2,
                diagnostics_saved: 2,
                recommendations_count: 0,
                errors_count: 1,
                source_only: true,
                provider_write_reached: false,
                event_write_reached: false,
                accounting_write_reached: false,
                qoyod_write_reached: false,
            },
        });
        const result = await diagnoseSnapchatTracking({ days: 7 });
        expect(api.post).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/tracking-diagnostics",
            { days: 7 },
        );
        expect(result.status).toBe("partial");
        expect(result.pixels_found).toBe(1);
    });

    test("rejects a range outside the official seven-day pixel window", async () => {
        await expect(diagnoseSnapchatTracking({ days: 8 })).rejects.toThrow(
            "invalid_tracking_diagnostic_days",
        );
        expect(api.post).not.toHaveBeenCalled();
    });
});
