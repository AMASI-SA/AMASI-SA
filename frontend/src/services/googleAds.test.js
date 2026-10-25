import api from "../lib/api";
import {
    GOOGLE_ADS_ROOT, createGoogleDraft, createLatestGoogleResponseGuard, executeGoogleProposal,
    getGoogleAdsWorkspace, getGoogleProducts, googleCampaignCapabilities, googleCapabilityCanCreate, googleIdempotencyKey,
    googleProposalCanApprove, googleProposalNeedsReconcile, updateGoogleDraft, uploadGoogleImage,
    googleErrorMessage, googleConfidenceLabel, googleRecommendationAction, previewGoogleRecommendation, analyzeGoogleCampaign,
} from "./googleAds";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() } }));
const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
beforeAll(() => { Object.defineProperty(globalThis, "crypto", { configurable: true, value: require("crypto").webcrypto }); });
afterAll(() => { if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto); else delete globalThis.crypto; });
beforeEach(() => { api.get.mockReset(); api.post.mockReset(); api.put.mockReset(); });

test("saved AI review sends only server analysis and recommendation identities", async () => {
    api.post.mockResolvedValue({ data: { proposal_id: "p1", status: "previewed" } });
    const controller = new AbortController();
    await previewGoogleRecommendation("saved-analysis", "google_ads:campaign:123:456:PAUSE_CAMPAIGN", { signal: controller.signal });
    expect(api.post).toHaveBeenCalledWith(`${GOOGLE_ADS_ROOT}/ai/recommendations/preview`, {
        analysis_id: "saved-analysis", recommendation_id: "google_ads:campaign:123:456:PAUSE_CAMPAIGN",
    }, { timeout: 30000, signal: controller.signal });
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("native uppercase actions and confidence use the backend contract", () => {
    expect(googleRecommendationAction({ action: "PAUSE_CAMPAIGN" })).toBe("pause");
    expect(googleRecommendationAction({ action: "RESUME_CAMPAIGN" })).toBe("enable");
    expect(googleRecommendationAction({ action: "CHANGE_BUDGET" })).toBe("set_budget");
    for (const action of ["MONITOR", "REVIEW_TRACKING", "CHANGE_BIDDING", "CREATE_TEST_DRAFT"]) expect(googleRecommendationAction({ action })).toBeNull();
    expect(googleConfidenceLabel("high")).toBe("مرتفعة");
    expect(googleConfidenceLabel("medium")).toBe("متوسطة");
    expect(googleConfidenceLabel("low")).toBe("منخفضة");
    expect(googleConfidenceLabel(0.8)).toBe("80%");
    expect(googleConfidenceLabel("unknown")).toBe("غير متاحة");
});

test("AI timeout has an actionable safe explanation without echoing private error data", () => {
    const message = googleErrorMessage({ response: { status: 504, data: { detail: { code: "google_ai_timeout", provider_writes: 0, private_data: "secret-provider-body" } } } });
    expect(message).toContain("مهلة");
    expect(message).not.toContain("secret-provider-body");
});

test("analysis transport timeout explains the failed analysis and never claims a draft or write", () => {
    for (const error of [
        { code: "ECONNABORTED", config: { url: `${GOOGLE_ADS_ROOT}/ai/analyze` } },
        { response: { status: 504, data: { detail: { code: "google_ai_timeout" } } }, config: { url: `${GOOGLE_ADS_ROOT}/ai/analyze` } },
    ]) {
        const message = googleErrorMessage(error);
        expect(message).toContain("مهلة تحليل");
        expect(message).not.toContain("مسودة");
    }
});

test("analysis waits for native reads and reasoning and retains caller cancellation", async () => {
    api.post.mockResolvedValue({ data: { analysis_id: "saved", recommendations: [] } });
    const controller = new AbortController();
    const payload = { account_id: "123", from_date: "2026-10-25", to_date: "2026-10-25" };
    expect(await analyzeGoogleCampaign(payload, { signal: controller.signal })).toEqual({ analysis_id: "saved", recommendations: [] });
    expect(api.post).toHaveBeenCalledWith(`${GOOGLE_ADS_ROOT}/ai/analyze`, payload, { timeout: 90000, signal: controller.signal });
});

test("workspace scopes every read to the selected range, account and hierarchy", async () => {
    api.get.mockResolvedValue({ data: { items: [] } });
    const controller = new AbortController();
    await getGoogleAdsWorkspace({ dateFrom: "2026-10-01", dateTo: "2026-10-13", accountId: "123", entityType: "ads", campaignId: "456", groupId: "789", page: 2, query: "Gift", signal: controller.signal });
    expect(api.get).toHaveBeenCalledWith(`${GOOGLE_ADS_ROOT}/workspace`, { timeout: 30000, signal: controller.signal, params: { from_date: "2026-10-01", to_date: "2026-10-13", account_id: "123", entity_type: "ads", campaign_id: "456", group_id: "789", page: 2, limit: 25, campaign_query: "Gift" } });
});

test("Mezan product search uses its bounded catalog endpoint and abort signal", async () => {
    const catalog = { items: [{ product_id: "owned_gift", name: "هدية باسمها", source: "mezan_products_v2" }], complete: true };
    api.get.mockResolvedValue({ data: catalog }); const controller = new AbortController();
    expect(await getGoogleProducts({ query: "  هدية  ", limit: 300, signal: controller.signal })).toBe(catalog);
    expect(api.get).toHaveBeenCalledWith(`${GOOGLE_ADS_ROOT}/products`, { timeout: 30000, signal: controller.signal, params: { query: "هدية", limit: 50 } });
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

test("HTML5 upload preserves an opaque ZIP and requests its separate frozen proposal", async () => {
    const proposal = { status: "previewed", action: "upload_html5", proposal_id: "html5_1" }; api.post.mockResolvedValue({ data: proposal });
    const file = new File(["opaque archive"], "display-ad.zip", { type: "application/zip" });
    expect(await uploadGoogleImage({ accountId: "123", file, assetType: "HTML5", idempotencyKey: "html5_key" })).toBe(proposal);
    expect(api.post).toHaveBeenCalledTimes(1); expect(api.post.mock.calls[0][1].get("asset_type")).toBe("HTML5");
    expect(api.post.mock.calls[0][1].get("file").name).toBe("display-ad.zip"); expect(api.post.mock.calls[0][1].get("idempotency_key")).toBe("html5_key");
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

test("operation identities use Web Crypto when randomUUID is unavailable", () => {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
    const getRandomValues = jest.fn((bytes) => { bytes.forEach((_, index) => { bytes[index] = index; }); return bytes; });
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: { getRandomValues } });
    try {
        expect(googleIdempotencyKey()).toBe("google_000102030405060708090a0b0c0d0e0f");
        expect(getRandomValues).toHaveBeenCalledTimes(1);
        expect(getRandomValues.mock.calls[0][0]).toBeInstanceOf(Uint8Array);
        expect(getRandomValues.mock.calls[0][0]).toHaveLength(16);
    } finally { Object.defineProperty(globalThis, "crypto", descriptor); }
});

test("operation identities refuse a browser without a secure random source", () => {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: undefined });
    try { expect(() => googleIdempotencyKey()).toThrow("اتصالًا آمنًا"); expect(api.post).not.toHaveBeenCalled(); }
    finally { Object.defineProperty(globalThis, "crypto", descriptor); }
});
