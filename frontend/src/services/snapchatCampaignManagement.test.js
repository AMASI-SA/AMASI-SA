import api from "../lib/api";
import {
    approveSnapchatManagementProposal,
    clearSnapchatManagementPreviewResume,
    createSnapchatManagementProposal,
    diagnoseSnapchatManagementPixels,
    executeSnapchatManagementProposal,
    getCurrentSnapchatManagementPreviewJob,
    getSnapchatEntitySettings,
    getSnapchatManagementPreviewResume,
    getSnapchatManagementReadiness,
    listSnapchatManagementProposals,
    microToNativeAmount,
    nativeAmountToMicro,
    normalizeSnapchatEntitySettings,
    normalizeSnapchatManagementProposal,
    normalizeSnapchatManagementPreviewJob,
    normalizeSnapchatManagementReadiness,
    pollSnapchatManagementPreviewJob,
    pollSnapchatManagementProposal,
    reconcileSnapchatManagementProposal,
    rollbackSnapchatManagementProposal,
    resumeSnapchatManagementProposal,
    snapchatBidLabel,
    snapchatFinancialFieldReady,
    snapchatFinancialSettingsReady,
    startSnapchatManagementPreviewJob,
    verifiedSnapchatManagementEntityId,
} from "./snapchatCampaignManagement";

jest.mock("../lib/api", () => ({
    get: jest.fn(),
    post: jest.fn(),
}));

describe("snapchatCampaignManagement", () => {
    beforeEach(() => {
        api.get.mockReset();
        api.post.mockReset();
        window.sessionStorage.clear();
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
                pixels: [{
                    pixel_id: "pixel-1",
                    display_name: "AMASI Pixel",
                    effective_status: "ACTIVE",
                    has_event_data: true,
                }],
            }],
        })).toMatchObject({
            execution_enabled: true,
            activation_enabled: false,
            salla_permission_dependency: false,
            accounts: [{
                account_id: "account-1",
                role: "general",
                management_allowed: true,
                pixels: [{
                    pixel_id: "pixel-1",
                    display_name: "AMASI Pixel",
                    effective_status: "ACTIVE",
                    has_event_data: true,
                }],
                pixel_selection_required: false,
            }],
        });
    });

    test("requires explicit Pixel selection when an account exposes multiple pixels", () => {
        expect(normalizeSnapchatManagementReadiness({
            accounts: [{
                account_id: "account-1",
                pixel_selection_required: false,
                pixels: [
                    { pixel_id: "pixel-1" },
                    { pixel_id: "pixel-2" },
                    { display_name: "missing id" },
                ],
            }],
        }).accounts[0]).toMatchObject({
            pixel_selection_required: true,
            pixels: [
                { pixel_id: "pixel-1" },
                { pixel_id: "pixel-2" },
            ],
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

    test("discovers Pixels through the bounded read-only tracking diagnostic", async () => {
        api.post.mockResolvedValueOnce({
            data: {
                status: "complete",
                pixels_found: 1,
                source_only: true,
                provider_write_reached: false,
            },
        });

        await expect(diagnoseSnapchatManagementPixels({ days: 7 })).resolves.toMatchObject({
            status: "complete",
            pixels_found: 1,
            source_only: true,
        });
        expect(api.post).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/tracking-diagnostics",
            expect.objectContaining({
                days: 7,
                idempotency_key: expect.stringMatching(/^management-pixel-/),
            }),
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

    test("reconciles an uncertain proposal through the read-only provider workflow", async () => {
        api.post.mockResolvedValueOnce({
            data: {
                proposal_id: "proposal-uncertain",
                action: "ad_squad.create",
                status: "completed",
                provider_write_reached: true,
                provider_write_state: "confirmed",
                provider_write_uncertain: false,
                provider_entity_id: "squad-1",
                verification: { verified: true, entity_id: "squad-1" },
            },
        });

        await expect(reconcileSnapchatManagementProposal("proposal-uncertain"))
            .resolves.toMatchObject({
                status: "completed",
                verified_entity_id: "squad-1",
            });
        expect(api.post).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/management/proposals/proposal-uncertain/reconcile",
        );
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
                safety_protocol_version: 2,
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
                safety_protocol_version: 2,
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

    test("retries preview start transport failures with the exact same request", async () => {
        const networkError = new Error("Network Error");
        const wait = jest.fn().mockResolvedValue(undefined);
        api.post
            .mockRejectedValueOnce(networkError)
            .mockRejectedValueOnce({ response: { status: 520 } })
            .mockResolvedValueOnce({
                data: { preview_job_id: "job-recovered", status: "queued" },
            });

        await expect(startSnapchatManagementPreviewJob({
            action: "campaign.create",
            account_id: "account-1",
            payload: { name: "Safe" },
            reason: "safe transport retry",
            idempotency_key: "same-preview-request-1",
        }, {
            attempts: 3,
            intervalMs: 1,
            wait,
        })).resolves.toMatchObject({
            preview_job_id: "job-recovered",
            status: "queued",
        });

        expect(api.post).toHaveBeenCalledTimes(3);
        expect(api.post.mock.calls[0][1]).toBe(api.post.mock.calls[1][1]);
        expect(api.post.mock.calls[1][1]).toBe(api.post.mock.calls[2][1]);
        expect(api.post.mock.calls[2][1].idempotency_key).toBe(
            "same-preview-request-1",
        );
        expect(wait).toHaveBeenCalledTimes(2);
    });

    test("preview start retry is capped and excludes non-transient responses", async () => {
        const wait = jest.fn().mockResolvedValue(undefined);
        const transient = { response: { status: 503 } };
        api.post.mockRejectedValue(transient);
        await expect(startSnapchatManagementPreviewJob({
            action: "campaign.create",
            account_id: "account-1",
            payload: { name: "Safe" },
            reason: "bounded preview transport retry",
            idempotency_key: "bounded-preview-request-1",
        }, {
            attempts: 99,
            intervalMs: 1,
            wait,
        })).rejects.toBe(transient);
        expect(api.post).toHaveBeenCalledTimes(3);
        expect(wait).toHaveBeenCalledTimes(2);

        api.post.mockReset();
        wait.mockClear();
        const badRequest = { response: { status: 400 } };
        api.post.mockRejectedValue(badRequest);
        await expect(startSnapchatManagementPreviewJob({
            action: "campaign.create",
            account_id: "account-1",
            payload: { name: "Safe" },
            reason: "do not retry validation",
            idempotency_key: "non-transient-preview-1",
        }, { wait })).rejects.toBe(badRequest);
        expect(api.post).toHaveBeenCalledTimes(1);
        expect(wait).not.toHaveBeenCalled();
    });

    test("preview polling absorbs transient GET failures without another POST", async () => {
        const wait = jest.fn().mockResolvedValue(undefined);
        const load = jest.fn()
            .mockRejectedValueOnce({ response: { status: 504 } })
            .mockRejectedValueOnce(new Error("Network Error"))
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
        expect(load).toHaveBeenCalledTimes(3);
        expect(wait).toHaveBeenCalledTimes(2);
        expect(api.post).not.toHaveBeenCalled();
    });

    test("keeps a non-reconciled stale result pending until a ready reconciliation", async () => {
        const wait = jest.fn().mockResolvedValue(undefined);
        const load = jest.fn()
            .mockResolvedValueOnce({
                preview_job_id: "job-stale",
                status: "failed",
                terminal_reconciled: false,
            })
            .mockResolvedValueOnce({
                preview_job_id: "job-stale",
                status: "ready",
                proposal_id: "proposal-after-stale",
                terminal_reconciled: true,
            });
        await expect(pollSnapchatManagementPreviewJob({
            previewJobId: "job-stale",
            attempts: 2,
            intervalMs: 1,
            wait,
            load,
        })).resolves.toMatchObject({
            status: "ready",
            proposal_id: "proposal-after-stale",
        });
        expect(load).toHaveBeenCalledTimes(2);
        expect(api.post).not.toHaveBeenCalled();
    });

    test("stores the exact request before start and never stores a confirmation token", async () => {
        let storedDuringStart;
        api.post
            .mockImplementationOnce(async () => {
                storedDuringStart = window.sessionStorage.getItem(
                    "mezan:snapchat-management-preview:v2",
                );
                return { data: { preview_job_id: "job-stored", status: "queued" } };
            })
            .mockResolvedValueOnce({
                data: {
                    proposal_id: "proposal-stored",
                    action: "campaign.create",
                    status: "previewed",
                    confirm_token: "secret-confirm-token",
                },
            });
        api.get.mockResolvedValueOnce({
            data: {
                preview_job_id: "job-stored",
                status: "ready",
                proposal_id: "proposal-stored",
                terminal_reconciled: true,
            },
        });
        await createSnapchatManagementProposal({
            action: "campaign.create",
            account_id: "account-1",
            payload: { name: "Stored safely" },
            reason: "safe storage preview",
            idempotency_key: "stored-preview-001",
        }, { ownerId: "owner-1" });

        const during = JSON.parse(storedDuringStart);
        const after = JSON.parse(window.sessionStorage.getItem(
            "mezan:snapchat-management-preview:v2",
        ));
        expect(during.owner_id).toBe("owner-1");
        expect(during.preview_job_id).toBeNull();
        expect(during.request.idempotency_key).toBe("stored-preview-001");
        expect(after.preview_job_id).toBe("job-stored");
        expect(JSON.stringify(after)).not.toContain("secret-confirm-token");
        expect(JSON.stringify(after)).not.toContain("confirm_token");
    });

    test("resumes a saved job with GET and an idempotent claim but no start POST", async () => {
        api.post
            .mockResolvedValueOnce({
                data: { preview_job_id: "job-resume", status: "queued" },
            })
            .mockResolvedValueOnce({
                data: {
                    proposal_id: "proposal-resume",
                    action: "campaign.create",
                    status: "previewed",
                    confirm_token: "first-token",
                },
            });
        api.get.mockResolvedValueOnce({
            data: {
                preview_job_id: "job-resume",
                status: "ready",
                proposal_id: "proposal-resume",
                terminal_reconciled: true,
            },
        });
        await createSnapchatManagementProposal({
            action: "campaign.create",
            account_id: "account-1",
            payload: { name: "Resume" },
            reason: "resume the exact preview",
            idempotency_key: "resume-preview-001",
        }, { ownerId: "owner-1" });

        api.post.mockReset();
        api.get.mockReset();
        api.get.mockResolvedValueOnce({
            data: {
                preview_job_id: "job-resume",
                status: "ready",
                proposal_id: "proposal-resume",
                terminal_reconciled: true,
            },
        });
        api.post.mockResolvedValueOnce({
            data: {
                proposal_id: "proposal-resume",
                action: "campaign.create",
                status: "previewed",
                confirm_token: "rotated-token",
            },
        });
        await expect(resumeSnapchatManagementProposal({
            ownerId: "owner-1",
        })).resolves.toMatchObject({
            proposal_id: "proposal-resume",
            confirm_token: "rotated-token",
        });
        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/management/preview-jobs/job-resume",
        );
        expect(api.post).toHaveBeenCalledTimes(1);
        expect(api.post).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/management/proposals",
            expect.objectContaining({ idempotency_key: "resume-preview-001" }),
        );
    });

    test("coalesces concurrent resumes so reopening cannot rotate two tokens", async () => {
        window.sessionStorage.setItem(
            "mezan:snapchat-management-preview:v2",
            JSON.stringify({
                version: 2,
                owner_id: "owner-1",
                saved_at: Date.now(),
                idempotency_key: "resume-coalesced-001",
                preview_job_id: "job-coalesced",
                request: {
                    action: "campaign.create",
                    account_id: "account-1",
                    payload: { name: "Coalesced" },
                    reason: "one token only",
                    idempotency_key: "resume-coalesced-001",
                },
            }),
        );
        let finishRead;
        api.get.mockImplementationOnce(() => new Promise((resolve) => {
            finishRead = resolve;
        }));
        api.post.mockResolvedValueOnce({
            data: {
                proposal_id: "proposal-coalesced",
                action: "campaign.create",
                status: "previewed",
                confirm_token: "only-current-token",
            },
        });

        const first = resumeSnapchatManagementProposal({ ownerId: "owner-1" });
        const reopened = resumeSnapchatManagementProposal({ ownerId: "owner-1" });
        expect(reopened).toBe(first);
        expect(api.get).toHaveBeenCalledTimes(1);

        finishRead({
            data: {
                preview_job_id: "job-coalesced",
                status: "ready",
                proposal_id: "proposal-coalesced",
                terminal_reconciled: true,
            },
        });
        await expect(Promise.all([first, reopened])).resolves.toEqual([
            expect.objectContaining({ confirm_token: "only-current-token" }),
            expect.objectContaining({ confirm_token: "only-current-token" }),
        ]);
        expect(api.get).toHaveBeenCalledTimes(1);
        expect(api.post).toHaveBeenCalledTimes(1);
        expect(api.post).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/management/proposals",
            expect.objectContaining({ idempotency_key: "resume-coalesced-001" }),
        );
    });

    test("coalesces create with reopen resume into one start and one token claim", async () => {
        let finishRead;
        api.post
            .mockResolvedValueOnce({
                data: { preview_job_id: "job-create-resume", status: "queued" },
            })
            .mockResolvedValueOnce({
                data: {
                    proposal_id: "proposal-create-resume",
                    action: "campaign.create",
                    status: "previewed",
                    confirm_token: "single-create-resume-token",
                },
            });
        api.get.mockImplementationOnce(() => new Promise((resolve) => {
            finishRead = resolve;
        }));
        const request = {
            action: "campaign.create",
            account_id: "account-1",
            payload: { name: "Create then reopen" },
            reason: "coalesce create and resume",
            idempotency_key: "create-resume-001",
        };

        const created = createSnapchatManagementProposal(
            request,
            { ownerId: "owner-1" },
        );
        const reopened = resumeSnapchatManagementProposal({ ownerId: "owner-1" });
        expect(reopened).toBe(created);
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(api.get).toHaveBeenCalledTimes(1);

        finishRead({
            data: {
                preview_job_id: "job-create-resume",
                status: "ready",
                proposal_id: "proposal-create-resume",
                terminal_reconciled: true,
            },
        });
        await expect(Promise.all([created, reopened])).resolves.toEqual([
            expect.objectContaining({ confirm_token: "single-create-resume-token" }),
            expect.objectContaining({ confirm_token: "single-create-resume-token" }),
        ]);
        expect(api.post.mock.calls.filter(([url]) => (
            url.endsWith("/preview-jobs")
        ))).toHaveLength(1);
        expect(api.post.mock.calls.filter(([url]) => (
            url.endsWith("/proposals")
        ))).toHaveLength(1);
    });

    test("recovers a lost 202 by exact idempotency without creating another job", async () => {
        const lost = { response: { status: 520 } };
        api.post
            .mockRejectedValueOnce(lost)
            .mockRejectedValueOnce(lost)
            .mockRejectedValueOnce(lost)
            .mockResolvedValueOnce({
                data: {
                    proposal_id: "proposal-lost-202",
                    action: "campaign.create",
                    status: "previewed",
                    confirm_token: "claim-token",
                },
            });
        api.get
            .mockResolvedValueOnce({
                data: {
                    preview_job_id: "job-lost-202",
                    status: "ready",
                    proposal_id: "proposal-lost-202",
                    terminal_reconciled: true,
                },
            })
            .mockResolvedValueOnce({
                data: {
                    preview_job_id: "job-lost-202",
                    status: "ready",
                    proposal_id: "proposal-lost-202",
                    terminal_reconciled: true,
                },
            });
        await expect(createSnapchatManagementProposal({
            action: "campaign.create",
            account_id: "account-1",
            payload: { name: "Lost 202" },
            reason: "recover accepted preview",
            idempotency_key: "lost-202-preview-001",
        }, { ownerId: "owner-1" })).resolves.toMatchObject({
            proposal_id: "proposal-lost-202",
        });
        expect(api.post.mock.calls.filter(([url]) => (
            url.endsWith("/preview-jobs")
        ))).toHaveLength(3);
        expect(api.get).toHaveBeenNthCalledWith(
            1,
            "/integrations-v2/snapchat_ads/management/preview-jobs/current",
            { params: { idempotency_key: "lost-202-preview-001" } },
        );
        expect(api.post.mock.calls.filter(([url]) => (
            url.endsWith("/proposals")
        ))).toHaveLength(1);
    });

    test("approval clears only the matching owner's saved preview", async () => {
        api.post
            .mockResolvedValueOnce({
                data: { preview_job_id: "job-clear", status: "queued" },
            })
            .mockResolvedValueOnce({
                data: {
                    proposal_id: "proposal-clear",
                    action: "campaign.create",
                    status: "previewed",
                    revision: 1,
                    confirm_token: "confirm-clear-token",
                },
            });
        api.get.mockResolvedValueOnce({
            data: {
                preview_job_id: "job-clear",
                status: "ready",
                proposal_id: "proposal-clear",
                terminal_reconciled: true,
            },
        });
        const proposal = await createSnapchatManagementProposal({
            action: "campaign.create",
            account_id: "account-1",
            payload: { name: "Clear" },
            reason: "clear after approval",
            idempotency_key: "clear-preview-001",
        }, { ownerId: "owner-1" });
        expect(getSnapchatManagementPreviewResume("owner-1")).not.toBeNull();

        api.post.mockReset();
        api.post.mockResolvedValueOnce({
            data: {
                proposal_id: "proposal-clear",
                action: "campaign.create",
                status: "approved",
                revision: 2,
            },
        });
        await approveSnapchatManagementProposal(proposal, { ownerId: "owner-1" });
        expect(getSnapchatManagementPreviewResume("owner-1")).toBeNull();
        clearSnapchatManagementPreviewResume("owner-1");
    });

    test("preview polling rejects a non-transient GET failure immediately", async () => {
        const unauthorized = { response: { status: 401 } };
        const wait = jest.fn().mockResolvedValue(undefined);
        const load = jest.fn().mockRejectedValue(unauthorized);
        await expect(pollSnapchatManagementPreviewJob({
            previewJobId: "job-1",
            attempts: 180,
            intervalMs: 1,
            wait,
            load,
        })).rejects.toBe(unauthorized);
        expect(load).toHaveBeenCalledTimes(1);
        expect(wait).not.toHaveBeenCalled();
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

    test("exposes a canonical entity id only after verified completion", () => {
        const completed = {
            proposal_id: "proposal-verified-id",
            action: "campaign.create",
            status: "completed",
            provider_write_reached: true,
            provider_write_state: "confirmed",
            provider_write_uncertain: false,
            provider_entity_id: "campaign-verified-1",
            verification: {
                verified: true,
                entity_id: "campaign-verified-1",
            },
        };

        expect(verifiedSnapchatManagementEntityId(completed))
            .toBe("campaign-verified-1");
        expect(normalizeSnapchatManagementProposal(completed)).toMatchObject({
            provider_entity_id: "campaign-verified-1",
            verified_entity_id: "campaign-verified-1",
        });
    });

    test("maps protocol-v2 lifecycle states for the existing UI while preserving the provider state", () => {
        expect(normalizeSnapchatManagementProposal({
            proposal_id: "proposal-v2",
            action: "ad_squad.create",
            status: "approved_v2",
            safety_protocol_version: 2,
        })).toMatchObject({
            status: "approved",
            provider_status: "approved_v2",
            safety_protocol_version: 2,
        });
    });

    test.each([
        ["execution is not complete", { status: "executing" }],
        ["readback did not verify", { verification: { verified: false, entity_id: "campaign-verified-1" } }],
        ["readback id is missing", { verification: { verified: true } }],
        ["provider id is missing", { provider_entity_id: null }],
        ["provider write was not reached", { provider_write_reached: false }],
        ["write is not confirmed", { provider_write_state: "attempting" }],
        ["write outcome is uncertain", { provider_write_uncertain: true }],
        ["write certainty is missing", { provider_write_uncertain: undefined }],
        ["provider and readback ids conflict", {
            provider_entity_id: "campaign-other",
            verification: { verified: true, entity_id: "campaign-verified-1" },
        }],
    ])("rejects a non-canonical entity id when %s", (_label, change) => {
        const value = {
            proposal_id: "proposal-untrusted-id",
            action: "campaign.create",
            status: "completed",
            provider_write_reached: true,
            provider_write_state: "confirmed",
            provider_write_uncertain: false,
            provider_entity_id: "campaign-verified-1",
            verification: {
                verified: true,
                entity_id: "campaign-verified-1",
            },
            ...change,
        };
        expect(verifiedSnapchatManagementEntityId(value)).toBeNull();
        expect(normalizeSnapchatManagementProposal(value).verified_entity_id).toBeNull();
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

    test("preserves verified Pixel eligibility evidence", () => {
        expect(normalizeSnapchatManagementProposal({
            proposal_id: "proposal-pixel",
            action: "ad_squad.create",
            status: "previewed",
            pixel_eligibility: {
                verified: true,
                pixel_id: "pixel-1",
                account_id: "account-1",
                optimization_goal: "PIXEL_PURCHASE",
                conversion_window: "SWIPE_7DAY",
                eligibility_status: "ELIGIBLE",
            },
        })).toMatchObject({
            pixel_eligibility: {
                verified: true,
                pixel_id: "pixel-1",
                optimization_goal: "PIXEL_PURCHASE",
                conversion_window: "SWIPE_7DAY",
                eligibility_status: "ELIGIBLE",
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

    test("converts micro values exactly and keeps missing different from zero", () => {
        expect(microToNativeAmount(1_500_000)).toBe(1.5);
        expect(nativeAmountToMicro("1.5")).toBe(1_500_000);
        expect(microToNativeAmount(null)).toBeNull();
        expect(microToNativeAmount("")).toBeNull();
        expect(nativeAmountToMicro("")).toBeNull();
        expect(microToNativeAmount(0)).toBe(0);
    });

    test("labels bid_micro according to bid strategy semantics", () => {
        expect(snapchatBidLabel("TARGET_COST")).toBe("Target Cost");
        expect(snapchatBidLabel("LOWEST_COST_WITH_MAX_BID")).toBe("Max Bid");
        expect(snapchatBidLabel("AUTO_BID")).toBe("Bid");
        expect(snapchatBidLabel(null)).toBe("Bid");
    });

    test("only exposes USD conversion for USD accounts", () => {
        const freshSettingsAt = new Date(Date.now() - 120_000).toISOString();
        const usd = normalizeSnapchatEntitySettings({
            unified_entity_id: "u-1",
            provider_entity_id: "p-1",
            mapping_verified: true,
            ad_account_id: "account-1",
            account_currency: "USD",
            settings_synced_at: freshSettingsAt,
            daily_budget_micro: 20_000_000,
            daily_budget_usd: 999,
            bid_micro: 5_000_000,
            bid_usd: 999,
            ad_squads_daily_budget_micro: 30_000_000,
            ad_squads_daily_budget_usd: 999,
            quality: {
                settings_status: "settings_complete",
                freshness_seconds: 120,
                freshness_threshold_seconds: 1800,
                financial_controls_allowed: true,
                financial_field_controls: {
                    daily_budget: { allowed: true },
                    bid: { allowed: true },
                },
            },
        });
        expect(usd.daily_budget_usd).toBe(20);
        expect(usd.bid_usd).toBe(5);
        expect(usd.ad_squads_daily_budget_usd).toBe(30);
        expect(snapchatFinancialSettingsReady(usd, "account-1")).toBe(true);
        expect(snapchatFinancialFieldReady(usd, "daily_budget_micro", "account-1")).toBe(true);
        expect(snapchatFinancialFieldReady(usd, "bid_strategy", "account-1")).toBe(true);
        expect(snapchatFinancialFieldReady(usd, "daily_budget_micro", "account-other")).toBe(false);

        const missingCurrencyProof = normalizeSnapchatEntitySettings({
            unified_entity_id: "u-no-currency",
            provider_entity_id: "p-no-currency",
            mapping_verified: true,
            ad_account_id: "account-1",
            currency: "USD",
            daily_budget_micro: 20_000_000,
            settings_synced_at: "2026-08-28T10:00:00Z",
            quality: {
                settings_status: "settings_complete",
                freshness_seconds: 120,
                freshness_threshold_seconds: 1800,
                financial_field_controls: { daily_budget: { allowed: true } },
            },
        });
        expect(missingCurrencyProof.account_currency).toBeNull();
        expect(missingCurrencyProof.daily_budget_usd).toBeNull();
        expect(snapchatFinancialFieldReady(
            missingCurrencyProof,
            "daily_budget_micro",
            "account-1",
        )).toBe(false);

        const sar = normalizeSnapchatEntitySettings({
            unified_entity_id: "u-2",
            provider_entity_id: "p-2",
            mapping_status: "verified",
            ad_account_id: "account-1",
            account_currency: "SAR",
            settings_synced_at: "2026-08-28T10:00:00Z",
            daily_budget_micro: 20_000_000,
            bid_micro: 5_000_000,
            quality: {
                settings_status: "settings_complete",
                financial_controls_allowed: true,
            },
        });
        expect(sar.daily_budget_usd).toBeNull();
        expect(sar.bid_usd).toBeNull();
        expect(snapchatFinancialSettingsReady(sar, "account-1")).toBe(false);
        expect(snapchatFinancialFieldReady(sar, "daily_budget_micro", "account-1")).toBe(false);
    });

    test("expires historical settings proof by timestamp and requires explicit mapping proof", () => {
        const stale = normalizeSnapchatEntitySettings({
            unified_entity_id: "unified-squad-1",
            provider_entity_id: "provider-squad-1",
            mapping_status: "verified",
            mapping_verified: true,
            ad_account_id: "account-1",
            account_currency: "USD",
            settings_synced_at: new Date(Date.now() - 3_600_000).toISOString(),
            daily_budget_micro: 20_000_000,
            quality: {
                settings_status: "settings_complete",
                freshness_seconds: 1,
                freshness_threshold_seconds: 1800,
                financial_field_controls: { daily_budget: { allowed: true } },
            },
        });
        expect(snapchatFinancialFieldReady(stale, "daily_budget_micro", "account-1")).toBe(false);

        const implicitMapping = normalizeSnapchatEntitySettings({
            ...stale,
            mapping_verified: false,
            settings_synced_at: new Date(Date.now() - 60_000).toISOString(),
        });
        expect(implicitMapping.mapping_verified).toBe(false);
        expect(snapchatFinancialFieldReady(
            implicitMapping,
            "daily_budget_micro",
            "account-1",
        )).toBe(false);
    });

    test("reads settings with unified identifiers without issuing a provider write", async () => {
        api.get.mockResolvedValue({
            data: {
                items: [{
                    entity_type: "ad_squad",
                    unified_entity_id: "7c0f5bfa-3f59-437b-bb89-1c70b11d0526",
                    provider_entity_id: "provider-ad-squad-original",
                    mapping_verified: true,
                    account_currency: "USD",
                    daily_budget_micro: 100_000_000,
                    quality: {
                        settings_status: "settings_complete",
                        financial_controls_allowed: true,
                    },
                }],
            },
        });

        const rows = await getSnapchatEntitySettings({
            entityType: "ad_squad",
            unifiedEntityId: "7c0f5bfa-3f59-437b-bb89-1c70b11d0526",
        });
        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/management/entity-settings",
            expect.objectContaining({
                params: expect.objectContaining({
                    entity_type: "ad_squad",
                    unified_entity_id: "7c0f5bfa-3f59-437b-bb89-1c70b11d0526",
                }),
            }),
        );
        expect(rows[0]).toMatchObject({
            unified_entity_id: "7c0f5bfa-3f59-437b-bb89-1c70b11d0526",
            provider_entity_id: "provider-ad-squad-original",
            daily_budget_micro: 100_000_000,
            daily_budget_usd: 100,
        });
        expect(api.post).not.toHaveBeenCalled();
    });

    test("reads only the exact visible settings batch and keeps it capped at 100", async () => {
        api.get.mockResolvedValue({ data: { items: [] } });
        const ids = Array.from({ length: 25 }, (_, index) => `campaign-${index}`);

        await getSnapchatEntitySettings({
            entityType: "campaign",
            unifiedEntityIds: [...ids, ids[0]],
            limit: 500,
        });

        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/management/entity-settings",
            {
                params: {
                    entity_type: "campaign",
                    unified_entity_id: undefined,
                    unified_entity_ids: ids.join(","),
                    parent_unified_id: undefined,
                    limit: 100,
                },
            },
        );
        expect(api.post).not.toHaveBeenCalled();
        await expect(getSnapchatEntitySettings({
            entityType: "campaign",
            unifiedEntityIds: Array.from({ length: 101 }, (_, index) => `campaign-${index}`),
        })).rejects.toThrow("snapchat_settings_visible_batch_too_large");
    });

    test("normalizes structured audit data and provider mapping independently", () => {
        const freshSettingsAt = new Date(Date.now() - 120_000).toISOString();
        const proposal = normalizeSnapchatManagementProposal({
            proposal_id: "proposal-1",
            action: "ad_squad.update",
            status: "completed",
            target_id: "unified-squad-1",
            provider_target_id: "provider-squad-9",
            provider_entity_id: "provider-squad-9",
            actor_id: "owner-1",
            settings_proof: {
                unified_entity_id: "unified-squad-1",
                provider_entity_id: "provider-squad-9",
                ad_account_id: "account-1",
                mapping_status: "verified",
                mapping_verified: true,
                settings_status: "settings_complete",
                financial_controls_allowed: true,
                financial_field_controls: {
                    daily_budget: { allowed: true },
                    bid: { allowed: true },
                },
                last_synced_at: freshSettingsAt,
                freshness_seconds: 120,
                freshness_threshold_seconds: 1800,
                currency: "USD",
            },
            field_changes: {
                daily_budget_micro: {
                    before_micro: 50_000_000,
                    after_micro: 60_000_000,
                    before_usd: 50,
                    after_usd: 60,
                },
                bid_micro: {
                    before_micro: 7_500_000,
                    after_micro: 8_000_000,
                    before_usd: 7.5,
                    after_usd: 8,
                },
                bid_strategy: {
                    before: "TARGET_COST",
                    after: "LOWEST_COST_WITH_MAX_BID",
                },
            },
            field_change_metadata: {
                actor_id: "owner-envelope",
                occurred_at: "2026-08-28T10:01:00Z",
                provider_entity_id: "provider-squad-9",
                provider_reread_verified: true,
            },
            verification: {
                verified: true,
                entity_id: "provider-squad-9",
                source: "snapchat_provider_reread",
                verified_at: "2026-08-28T10:02:00Z",
                provider_snapshot: {
                    id: "provider-squad-9",
                    daily_budget_micro: 60_000_000,
                    bid_micro: 8_000_000,
                    bid_strategy: "LOWEST_COST_WITH_MAX_BID",
                },
            },
            provider_write_reached: true,
            provider_write_state: "confirmed",
            provider_write_uncertain: false,
        });
        expect(proposal.target_id).toBe("unified-squad-1");
        expect(proposal.provider_target_id).toBe("provider-squad-9");
        expect(proposal.actor_id).toBe("owner-1");
        expect(proposal.account_currency).toBe("USD");
        expect(proposal.settings_proof).toMatchObject({
            account_currency: "USD",
            settings_synced_at: freshSettingsAt,
            quality: {
                settings_status: "settings_complete",
                freshness_seconds: 120,
                freshness_threshold_seconds: 1800,
                financial_field_controls: {
                    daily_budget: { allowed: true },
                    bid: { allowed: true },
                },
            },
        });
        expect(snapchatFinancialFieldReady(
            proposal.settings_proof,
            "bid_micro",
            "account-1",
        )).toBe(true);
        expect(proposal.field_changes_metadata).toMatchObject({
            actor_id: "owner-envelope",
            occurred_at: "2026-08-28T10:01:00Z",
            provider_entity_id: "provider-squad-9",
            provider_reread_verified: true,
        });
        expect(proposal.field_changes_known).toBe(true);
        expect(proposal.field_changes).toEqual(expect.arrayContaining([
            expect.objectContaining({
                field: "daily_budget_micro",
                before_micro: 50_000_000,
                after_micro: 60_000_000,
            }),
            expect.objectContaining({
                field: "bid_micro",
                before_micro: 7_500_000,
                after_micro: 8_000_000,
            }),
            expect.objectContaining({
                field: "bid_strategy",
                before: "TARGET_COST",
                after: "LOWEST_COST_WITH_MAX_BID",
            }),
        ]));
        expect(proposal.provider_readback).toEqual({
            id: "provider-squad-9",
            daily_budget_micro: 60_000_000,
            bid_micro: 8_000_000,
            bid_strategy: "LOWEST_COST_WITH_MAX_BID",
        });
        expect(proposal.verification).toMatchObject({
            source: "snapchat_provider_reread",
            verified_at: "2026-08-28T10:02:00Z",
        });

        const mismatch = normalizeSnapchatManagementProposal({
            proposal_id: "proposal-mismatch",
            action: "ad_squad.update",
            status: "failed",
            failure: { mismatched_fields: ["bid_micro"] },
            reconciliation_snapshot: { id: "provider-squad-9", bid_micro: 7_500_000 },
            verification: { verification_source: "post_write_reread" },
        });
        expect(mismatch.verification).toMatchObject({
            source: "post_write_reread",
            mismatched_fields: ["bid_micro"],
        });
        expect(mismatch.provider_readback).toEqual({
            id: "provider-squad-9",
            bid_micro: 7_500_000,
        });
        expect(mismatch.provider_reread.mismatched_fields).toEqual(["bid_micro"]);
    });

});
