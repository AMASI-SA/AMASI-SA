import api from "../lib/api";
import {
    publicationAmountToMicro, previewSnapchatPublication, approveSnapchatPublication,
    executeSnapchatPublication, reconcileSnapchatPublication, getSnapchatPublicationByKey,
} from "./snapchatCampaignPublication";

jest.mock("../lib/api", () => ({ post: jest.fn(), get: jest.fn() }));
beforeEach(() => { jest.clearAllMocks(); api.post.mockResolvedValue({ data: {} }); api.get.mockResolvedValue({ data: {} }); });

test.each([["25", 25000000], ["25.000001", 25000001], ["0.000001", 1]])(
    "native amount %s converts exactly without foreign exchange", (text, expected) => {
        expect(publicationAmountToMicro(text)).toBe(expected);
    },
);
test.each(["", "-1", "NaN", "1e2", "0", "1.0000001", "25 USD"])("rejects missing or ambiguous amount %s", (value) => {
    expect(() => publicationAmountToMicro(value)).toThrow();
});
test("preview sends one immutable request without retry after transport uncertainty", async () => {
    const request = { account_id: "account-1", daily_budget_micro: 25000000, idempotency_key: "same-request" };
    api.post.mockRejectedValueOnce(new Error("timeout"));
    await expect(previewSnapchatPublication(request)).rejects.toThrow("timeout");
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][1]).toBe(request);
});
test("approval binds token/revision and never invokes execution", async () => {
    await approveSnapchatPublication({ publication_id: "job", confirm_token: "secret-token", revision: 4 });
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0]).toEqual([
        "/integrations-v2/snapchat_ads/management/publications/job/approve",
        { confirm_token: "secret-token", expected_revision: 4 },
    ]);
});
test("execution and reconciliation always address the existing durable job", async () => {
    await executeSnapchatPublication("job/one");
    await reconcileSnapchatPublication("job/one");
    await getSnapchatPublicationByKey("stable-key");
    expect(api.post.mock.calls.map((call) => call[0])).toEqual([
        "/integrations-v2/snapchat_ads/management/publications/job%2Fone/execute",
        "/integrations-v2/snapchat_ads/management/publications/job%2Fone/reconcile",
    ]);
    expect(api.get).toHaveBeenCalledWith("/integrations-v2/snapchat_ads/management/publications/current",
        { params: { idempotency_key: "stable-key" } });
});

