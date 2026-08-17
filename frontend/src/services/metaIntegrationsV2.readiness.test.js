import { normalizeMetaManagementReadiness } from "./metaIntegrationsV2";

test("normalizes a read-only Meta management readiness response", () => {
    const result = normalizeMetaManagementReadiness({
        provider: "meta_ads",
        token_valid: true,
        scopes: ["ads_read", "ads_management"],
        missing_scopes: ["business_management"],
        accounts: [{
            account_id: "act_1",
            display_name: "اماسي",
            tasks: ["ADVERTISE"],
            readable: true,
            role_verified: true,
            write_task_present: true,
            ready: false,
        }],
        capabilities: { ad_status_update: false },
        write_ready: false,
        read_only_check: true,
        source_only: true,
        provider_write_reached: false,
        campaign_write_reached: false,
        accounting_write_reached: false,
        qoyod_write_reached: false,
    });
    expect(result.accounts[0].tasks).toEqual(["ADVERTISE"]);
    expect(result.missing_scopes).toEqual(["business_management"]);
    expect(result.write_ready).toBe(false);
});

test("rejects a readiness response that reports a provider write", () => {
    expect(() => normalizeMetaManagementReadiness({
        source_only: true,
        provider_write_reached: true,
    })).toThrow("unsafe_meta_management_readiness_response");
});
