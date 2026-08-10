import api from "../lib/api";
import {
    approveSnapchatManagementProposal,
    createSnapchatManagementProposal,
    executeSnapchatManagementProposal,
    getSnapchatManagementReadiness,
    listSnapchatManagementProposals,
    microToNativeAmount,
    nativeAmountToMicro,
    normalizeSnapchatManagementReadiness,
    rollbackSnapchatManagementProposal,
} from "./snapchatCampaignManagement";

jest.mock("../lib/api", () => ({
    get: jest.fn(),
    post: jest.fn(),
}));

describe("snapchatCampaignManagement", () => {
    beforeEach(() => {
        api.get.mockReset();
        api.post.mockReset();
    });

    test("normalizes readiness without making Salla a dependency", () => {
        expect(normalizeSnapchatManagementReadiness({
            execution_enabled: true,
            activation_enabled: false,
            salla_permission_dependency: false,
            accounts: [{
                account_id: "account-1",
                display_name: "AMASI",
                role: "general",
                management_allowed: true,
            }],
        })).toMatchObject({
            execution_enabled: true,
            activation_enabled: false,
            salla_permission_dependency: false,
            accounts: [{ account_id: "account-1", role: "general", management_allowed: true }],
        });
    });

    test("loads readiness and proposals from the governed endpoints", async () => {
        api.get
            .mockResolvedValueOnce({ data: { proposal_enabled: true, accounts: [] } })
            .mockResolvedValueOnce({ data: { proposals: [{ proposal_id: "proposal-1", action: "campaign.create", status: "previewed" }] } });

        await expect(getSnapchatManagementReadiness()).resolves.toMatchObject({ proposal_enabled: true });
        await expect(listSnapchatManagementProposals({ limit: 500 })).resolves.toEqual([
            expect.objectContaining({ proposal_id: "proposal-1", action: "campaign.create" }),
        ]);
        expect(api.get).toHaveBeenNthCalledWith(1, "/integrations-v2/snapchat_ads/management/readiness");
        expect(api.get).toHaveBeenNthCalledWith(
            2,
            "/integrations-v2/snapchat_ads/management/proposals",
            { params: { limit: 100 } },
        );
    });

    test("uses preview approval execution and rollback endpoints in order", async () => {
        api.post
            .mockResolvedValueOnce({ data: { proposal_id: "proposal-1", action: "campaign.create", status: "previewed", revision: 1, confirm_token: "1234567890123456", confirmation_phrase: "تراجع proposal" } })
            .mockResolvedValueOnce({ data: { proposal_id: "proposal-1", action: "campaign.create", status: "approved", revision: 2 } })
            .mockResolvedValueOnce({ data: { proposal_id: "proposal-1", action: "campaign.create", status: "completed", revision: 2, confirmation_phrase: "تراجع proposal" } })
            .mockResolvedValueOnce({ data: { proposal_id: "proposal-1", action: "campaign.create", status: "rolled_back" } });

        const preview = await createSnapchatManagementProposal({
            action: "campaign.create",
            account_id: "account-1",
            payload: { name: "Safe", status: "PAUSED" },
            reason: "safe preview",
            idempotency_key: "safe-preview-1",
        });
        const approved = await approveSnapchatManagementProposal(preview);
        const completed = await executeSnapchatManagementProposal(approved.proposal_id);
        await rollbackSnapchatManagementProposal(completed, "verified rollback");

        expect(api.post.mock.calls.map(([url]) => url)).toEqual([
            "/integrations-v2/snapchat_ads/management/proposals",
            "/integrations-v2/snapchat_ads/management/proposals/proposal-1/approve",
            "/integrations-v2/snapchat_ads/management/proposals/proposal-1/execute",
            "/integrations-v2/snapchat_ads/management/proposals/proposal-1/rollback",
        ]);
        expect(api.post.mock.calls[1][1]).toEqual({
            confirm_token: "1234567890123456",
            expected_revision: 1,
        });
        expect(api.post.mock.calls[3][1]).toEqual({
            confirmation_phrase: "تراجع proposal",
            reason: "verified rollback",
        });
    });

    test("converts native currency to Snapchat micro-currency exactly", () => {
        expect(nativeAmountToMicro(50)).toBe(50_000_000);
        expect(nativeAmountToMicro("12.25")).toBe(12_250_000);
        expect(microToNativeAmount(12_250_000)).toBe(12.25);
    });
});
