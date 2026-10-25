import api from "../lib/api";
import {
    getSnapchatAutonomyExecutions, listSnapchatAutonomy, microToNativeAmount,
    nativeAmountToMicro, revokeSnapchatAutonomy, saveSnapchatAutonomy,
} from "./snapchatAutonomy";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() } }));
beforeEach(() => jest.clearAllMocks());

test("native micro conversion preserves six decimals without binary rounding", () => {
    expect(nativeAmountToMicro("5.01")).toBe(5010000);
    expect(nativeAmountToMicro("0.000001")).toBe(1);
    expect(nativeAmountToMicro("0", { allowZero: true })).toBe(0);
    expect(microToNativeAmount(5010000)).toBe("5.01");
    expect(microToNativeAmount(115000001)).toBe("115.000001");
});

test.each(["", "-1", "1e3", "NaN", "Infinity", "0", "1.0000001", "50000001"])("invalid delegated amount %s is rejected", (value) => {
    expect(() => nativeAmountToMicro(value)).toThrow();
});

test("policy endpoints use explicit account and revision; never call an ad approval endpoint", async () => {
    api.get.mockResolvedValue({ data: { items: [] } });
    api.put.mockResolvedValue({ data: { revision: 2 } });
    api.post.mockResolvedValue({ data: { configured_mode: "disabled" } });
    await listSnapchatAutonomy();
    const body = { expected_revision: 1, mode: "shadow" };
    await saveSnapchatAutonomy("account/a", body);
    await revokeSnapchatAutonomy("account/a", 2);
    await getSnapchatAutonomyExecutions("account/a");
    expect(api.put).toHaveBeenCalledWith("/ads-manager/ai-monitor/snapchat/autonomy/account%2Fa", body);
    expect(api.post).toHaveBeenCalledWith("/ads-manager/ai-monitor/snapchat/autonomy/account%2Fa/revoke", { expected_revision: 2 });
    expect(api.get).toHaveBeenLastCalledWith("/ads-manager/ai-monitor/snapchat/autonomy/account%2Fa/executions");
    expect(api.post.mock.calls).toHaveLength(1);
});
