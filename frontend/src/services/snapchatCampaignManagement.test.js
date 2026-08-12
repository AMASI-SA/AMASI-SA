import api from "../lib/api";
import {
    approveSnapchatManagementProposal,
    createSnapchatManagementProposal,
    executeSnapchatManagementProposal,
    getSnapchatManagementReadiness,
    listSnapchatManagementProposals,
    microToNativeAmount,
    nativeAmountToMicro,
    normalizeSnapchatManagementProposal,
    normalizeSnapchatManagementPreviewJob,
    normalizeSnapchatManagementReadiness,
    pollSnapchatManagementPreviewJob,
    pollSnapchatManagementProposal,
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
            .mockResolvedValueOnce({ data: { preview_job_id: "job-1", status: "queued" } })
            .mockResolvedValueOnce({ data: { proposal_id: "proposal-1", action: "campaign.create", status: "previewed", revision: 1, confirm_token: "1234567890123456", confirmation_phrase: "تراجع proposal" } })
            .mockResolvedValueOnce({ data: { proposal_id: "proposal-1", action: "campaign.create", status: "approved", revision: 2 } })
            .mockResolvedValueOnce({ data: { proposal_id: "proposal-1", action: "campaign.create", status: "completed", revision: 2, confirmation_phrase: "تراجع proposal" } })
            .mockResolvedValueOnce({ data: { proposal_id: "proposal-1", action: "campaign.create", status: "rolled_back" } });
        api.get.mockResolvedValueOnce({
            data: { preview_job_id: "job-1", status: "ready", proposal_id: "proposal-1" },
        });

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
            "/integrations-v2/snapchat_ads/management/preview-jobs",
            "/integrations-v2/snapchat_ads/management/proposals",
            "/integrations-v2/snapchat_ads/management/proposals/proposal-1/approve",
            "/integrations-v2/snapchat_ads/management/proposals/proposal-1/execute",
            "/integrations-v2/snapchat_ads/management/proposals/proposal-1/rollback",
        ]);
        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/management/preview-jobs/job-1",
        );
        expect(api.post.mock.calls.filter(([url]) => (
            url === "/integrations-v2/snapchat_ads/management/proposals"
        ))).toHaveLength(1);
        expect(api.post.mock.calls[2][1]).toEqual({
            confirm_token: "1234567890123456",
            expected_revision: 1,
        });
        expect(api.post.mock.calls[4][1]).toEqual({
            confirmation_phrase: "تراجع proposal",
            reason: "verified rollback",
        });
    });

    test("passes product scope measurable expectations and non-authoritative context to preview", async () => {
        api.post
            .mockResolvedValueOnce({
                data: { preview_job_id: "job-context-1", status: "queued" },
            })
            .mockResolvedValueOnce({
                data: {
                    proposal_id: "proposal-context-1",
                    action: "campaign.create",
                    status: "previewed",
                },
            });
        api.get.mockResolvedValueOnce({
            data: {
                preview_job_id: "job-context-1",
                status: "ready",
                proposal_id: "proposal-context-1",
            },
        });
        const products = [{
            product_id: "710474094",
            product_variant_id: "variant-1",
            product_name: "المشط",
        }];
        const expectedOutcome = {
            primary_goal: "grow_sales_while_protecting_contribution_profit",
            sales_direction: "increase",
            contribution_profit_direction: "stable",
            evaluation_horizons_hours: [24, 72, 168],
        };
        const supportingEvidence = [{
            kind: "user_context",
            value: "قد تكون السيولة أعلى بعد نزول الرواتب",
            source: "snapchat_management_panel:user",
            verification_status: "user_suggestion",
            confidence: 0,
            used_in_decision: false,
            weight: 0,
        }];

        await createSnapchatManagementProposal({
            action: "campaign.create",
            account_id: "account-1",
            payload: { name: "Safe", status: "PAUSED" },
            reason: "قياس أثر إنشاء حملة جديدة",
            idempotency_key: "context-preview-1",
            products,
            expected_outcome: expectedOutcome,
            supporting_evidence: supportingEvidence,
            trend_override_reason: "التحسن الحديث غير مكتمل الإسناد",
        });

        expect(api.post).toHaveBeenNthCalledWith(
            1,
            "/integrations-v2/snapchat_ads/management/preview-jobs",
            expect.objectContaining({
                products,
                expected_outcome: expectedOutcome,
                supporting_evidence: supportingEvidence,
                trend_override_reason: "التحسن الحديث غير مكتمل الإسناد",
            }),
        );
        expect(api.post).toHaveBeenNthCalledWith(
            2,
            "/integrations-v2/snapchat_ads/management/proposals",
            expect.objectContaining({
                products,
                expected_outcome: expectedOutcome,
                supporting_evidence: supportingEvidence,
                trend_override_reason: "التحسن الحديث غير مكتمل الإسناد",
            }),
        );
    });

    test("normalizes and polls the timeout-safe preview job without a write retry", async () => {
        expect(normalizeSnapchatManagementPreviewJob({
            preview_job_id: "job-1",
            status: "queued",
            provider_write_reached: false,
        })).toMatchObject({
            preview_job_id: "job-1",
            status: "queued",
            provider_write_reached: false,
            provider_write_state: "not_attempted",
        });
        const wait = jest.fn().mockResolvedValue(undefined);
        const load = jest.fn()
            .mockResolvedValueOnce({ preview_job_id: "job-1", status: "running" })
            .mockResolvedValueOnce({
                preview_job_id: "job-1", status: "ready", proposal_id: "proposal-1",
            });
        await expect(pollSnapchatManagementPreviewJob({
            previewJobId: "job-1",
            attempts: 3,
            intervalMs: 1,
            wait,
            load,
        })).resolves.toMatchObject({ status: "ready", proposal_id: "proposal-1" });
        expect(load).toHaveBeenCalledTimes(2);
        expect(wait).toHaveBeenCalledTimes(1);
        expect(api.post).not.toHaveBeenCalled();
    });

    test("preview poll timeout never claims or starts a second job", async () => {
        const wait = jest.fn().mockResolvedValue(undefined);
        const load = jest.fn().mockResolvedValue({
            preview_job_id: "job-1", status: "running",
        });
        await expect(pollSnapchatManagementPreviewJob({
            previewJobId: "job-1",
            attempts: 2,
            intervalMs: 1,
            wait,
            load,
        })).rejects.toMatchObject({
            code: "snapchat_management_preview_poll_timeout",
            preview_job_id: "job-1",
        });
        expect(load).toHaveBeenCalledTimes(2);
        expect(api.post).not.toHaveBeenCalled();
    });

    test("converts native currency to Snapchat micro-currency exactly", () => {
        expect(nativeAmountToMicro(50)).toBe(50_000_000);
        expect(nativeAmountToMicro("12.25")).toBe(12_250_000);
        expect(microToNativeAmount(12_250_000)).toBe(12.25);
    });

    test("preserves safe provider failure details", () => {
        expect(normalizeSnapchatManagementProposal({
            proposal_id: "proposal-1",
            action: "ad.create",
            status: "failed",
            failed_at: "2026-08-11T19:57:54+00:00",
            provider_entity_id: "ad-1",
            failure: {
                code: "snapchat_management_request_failed",
                provider_error_message: (
                    "Creative type WEB_VIEW requires REMOTE_WEBPAGE"
                ),
            },
        })).toMatchObject({
            failed_at: "2026-08-11T19:57:54+00:00",
            provider_entity_id: "ad-1",
            failure: {
                code: "snapchat_management_request_failed",
                provider_error_message: (
                    "Creative type WEB_VIEW requires REMOTE_WEBPAGE"
                ),
            },
        });
    });

    test("polls read-only until the background execution completes", async () => {
        const wait = jest.fn().mockResolvedValue(undefined);
        const load = jest.fn()
            .mockResolvedValueOnce([{
                proposal_id: "proposal-1", status: "executing",
            }])
            .mockResolvedValueOnce([{
                proposal_id: "proposal-1", status: "completed",
            }]);
        await expect(pollSnapchatManagementProposal({
            proposalId: "proposal-1",
            attempts: 3,
            intervalMs: 1,
            wait,
            load,
        })).resolves.toMatchObject({
            proposal: { proposal_id: "proposal-1", status: "completed" },
        });
        expect(load).toHaveBeenCalledTimes(2);
        expect(wait).toHaveBeenCalledTimes(1);
    });

    test("returns a failed background execution with its provider detail", async () => {
        const load = jest.fn().mockResolvedValue([{
            proposal_id: "proposal-1",
            status: "failed",
            failure: { provider_error_message: "Creative type mismatch" },
        }]);
        await expect(pollSnapchatManagementProposal({
            proposalId: "proposal-1",
            attempts: 2,
            wait: jest.fn(),
            load,
        })).resolves.toMatchObject({
            proposal: {
                status: "failed",
                failure: { provider_error_message: "Creative type mismatch" },
            },
        });
        expect(load).toHaveBeenCalledTimes(1);
    });

    test("poll timeout never triggers another provider write", async () => {
        const wait = jest.fn().mockResolvedValue(undefined);
        const load = jest.fn().mockResolvedValue([{
            proposal_id: "proposal-1", status: "executing",
        }]);
        await expect(pollSnapchatManagementProposal({
            proposalId: "proposal-1",
            attempts: 2,
            intervalMs: 1,
            wait,
            load,
        })).rejects.toMatchObject({
            code: "snapchat_management_execution_poll_timeout",
        });
        expect(load).toHaveBeenCalledTimes(2);
        expect(wait).toHaveBeenCalledTimes(1);
        expect(api.post).not.toHaveBeenCalled();
    });
});
