import api from "../lib/api";
import {
    GOOGLE_ADS_ROOT, createGoogleDraft, createLatestGoogleResponseGuard, executeGoogleProposal,
    getGoogleAdsWorkspace, googleCampaignCapabilities, googleCapabilityCanCreate,
    googleProposalCanApprove, googleProposalNeedsReconcile, updateGoogleDraft, uploadGoogleImage,
} from "./googleAds";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() } }));
beforeEach(() => { api.get.mockReset(); api.post.mockReset(); api.put.mockReset(); });

test("workspace scopes every read to the selected range, account and hierarchy", async () => {
    api.get.mockResolvedValue({ data: { items: [] } });
    const controller = new AbortController();
    await getGoogleAdsWorkspace({ dateFrom: "2026-10-01", dateTo: "2026-10-13", accountId: "123", entityType: "ads", campaignId: "456", groupId: "789", page: 2, query: "Gift", signal: controller.signal });
    expect(api.get).toHaveBeenCalledWith(`${GOOGLE_ADS_ROOT}/workspace`, { signal: controller.signal, params: { from_date: "2026-10-01", to_date: "2026-10-13", account_id: "123", entity_type: "ads", campaign_id: "456", group_id: "789", page: 2, limit: 25, campaign_query: "Gift" } });
});

test("draft persistence retains decimal inputs and optimistic revisions", async () => {
    api.post.mockResolvedValue({ data: { draft_id: "draft_1", revision: 1 } }); api.put.mockResolvedValue({ data: { draft_id: "draft_1", revision: 2 } });
    const draft = { account_id: "123", budget_native: "139.123456", groups: [{ headlines: ["هدية بالاسم"] }] };
    await createGoogleDraft(draft, "stable_key"); await updateGoogleDraft("draft_1", draft, 1);
    expect(api.post.mock.calls[0][1]).toEqual({ draft, idempotency_key: "stable_key" });
    expect(api.put.mock.calls[0][1]).toEqual({ draft, expected_revision: 1 });
});

test("image preparation returns a proposal and never approves it implicitly", async () => {
    const proposal = { status: "previewed", proposal_id: "asset_1" }; api.post.mockResolvedValue({ data: proposal });
    const file = new File(["image"], "gift.png", { type: "image/png" });
    expect(await uploadGoogleImage({ accountId: "123", file, idempotencyKey: "asset_key" })).toBe(proposal);
    expect(api.post).toHaveBeenCalledTimes(1); expect(api.post.mock.calls[0][0]).toBe(`${GOOGLE_ADS_ROOT}/assets`);
    expect(api.post.mock.calls[0][1].get("account_id")).toBe("123"); expect(api.post.mock.calls[0][1].get("file")).toBe(file);
});

test("approval transmits only the immutable confirmation digest", async () => {
    api.post.mockResolvedValue({ data: { status: "completed" } });
    await executeGoogleProposal("proposal_1", "frozen_digest");
    expect(api.post.mock.calls[0][1]).toEqual({ confirmation_digest: "frozen_digest" }); expect(api.post.mock.calls[0][2].timeout).toBe(60000);
});

test("unknown outcomes and expired previews never permit another execution", () => {
    const preview = { proposal_id: "p1", confirmation_digest: "digest", status: "previewed" };
    expect(googleProposalCanApprove(preview)).toBe(true);
    expect(googleProposalCanApprove(preview, true)).toBe(false);
    expect(googleProposalCanApprove({ ...preview, expires_at: "2020-01-01T00:00:00Z" })).toBe(false);
    for (const status of ["outcome_unknown", "verification_pending", "executing"]) { expect(googleProposalCanApprove({ ...preview, status })).toBe(false); expect(googleProposalNeedsReconcile({ ...preview, status })).toBe(true); }
});

test("unsupported and ineligible account types stay disabled regardless of API enums", () => {
    const rows = googleCampaignCapabilities({ campaign_types: [{ type: "SEARCH", create: true, implemented: "builder_ready" }, { type: "VIDEO", create: false }, { type: "SHOPPING", create: true, account_eligible: false }] });
    expect(rows.map(googleCapabilityCanCreate)).toEqual([true, false, false]);
    expect(rows[0].label).toBe("البحث");
});

test("latest response guard rejects earlier account/range reads and unmounted responses", () => {
    const guard = createLatestGoogleResponseGuard(); const first = guard.begin(), second = guard.begin();
    expect(guard.current(first)).toBe(false); expect(guard.current(second)).toBe(true); guard.invalidate(); expect(guard.current(second)).toBe(false);
});
