import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import { AuthProvider, useAuth } from "../../context/AuthContext";
import GoogleAdsWorkspace from "./GoogleAdsWorkspace";
import planFixture from "../../services/__fixtures__/googleCommercialPlanPriorContext.json";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() } }));

const OWNER = "synthetic-owner", ACCOUNT = "5052346701", MERCHANT = "123";
// Actual d231 local route and699 observation outputs, synthetic providers only.
const CHOICES = { source: "mezan_google_local_membership", owner_id: OWNER,
    observation_id: "4516b385-8b53-4cf5-b132-0c3a21c06a3a", observed_at: "2026-10-29T22:44:25.647604+00:00",
    local_membership: "matching_complete_bracketing_snapshots", local_set_complete: true, local_account_count: 2,
    ads_accounts: [{ account_id: ACCOUNT, display_name: "إعلانات المتجر" }], merchant_accounts: [{ merchant_id: MERCHANT, display_name: "كتالوج المتجر" }],
    remote_account_set_complete: false, native_link_verified: false, merchant_access_verified: false,
    continuous_authorization_proved: false, store_product_mapping_verified: false, ads_eligibility_verified: false,
    execution_authorized: false, provider_calls: 0, provider_writes: 0 };
const OBSERVED = { scope: { owner_id: OWNER, ads_customer_id: ACCOUNT, merchant_id: MERCHANT }, products: [],
    started_at: "2026-10-29T22:46:30.415265+00:00", observed_at: "2026-10-29T22:46:30.417756+00:00",
    semantic_digest: "022f86a32757b5361f87048c0fcee499403bd1585a98c8bb5dab0e048a310742", pages_read: 1,
    source: "merchant_processed_products_v1", complete: true, atomic_snapshot: false, mapping_status: "unverified",
    store_product_mapping_verified: false, google_ads_channel_verified: false, destination_verified: false,
    category_membership_verified: false, attribution_verified: false, execution_authorized: false, provider_writes: 0,
    access_observation: { ads_customer_id: ACCOUNT, merchant_id: MERCHANT, credential_evidence_id: "google-bound-read-c7a39171be8a400b82cb56e1653a2020",
        native_link_resource: `customers/${ACCOUNT}/productLinks/17`, local_membership: "matching_bracketing_snapshots",
        native_link_set: "matching_complete_bracketing_reads", checked_at: "2026-10-29T22:46:30.418450+00:00", continuous_authorization_proved: false } };

const copy = (value) => JSON.parse(JSON.stringify(value));
const account = { account_id: ACCOUNT, account_name: "حساب مساحة الحملات", currency: "SAR", readable: true };
let root, container, actor, blockedChoices, blockedObservation, authFailure;
function Harness() {
    const { refreshUser } = useAuth();
    return <><button onClick={() => refreshUser().catch(() => {})}>تحديث جلسة المالك للاختبار</button>
        <GoogleAdsWorkspace dateFrom="2026-10-01" dateTo="2026-10-13" initialAccountId={ACCOUNT} selectedAccountId={ACCOUNT} /></>;
}
function pending() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    actor = { id: OWNER, role: "owner" }; blockedChoices = blockedObservation = authFailure = null;
    api.get.mockReset(); api.post.mockReset(); api.put.mockReset(); api.delete.mockReset();
    api.get.mockImplementation((url) => {
        if (url === "/auth/me") return authFailure ? Promise.reject(authFailure) : Promise.resolve({ data: { ...actor } });
        if (url.endsWith("/merchant-products/choices")) return blockedChoices?.promise || Promise.resolve({ data: copy(CHOICES) });
        if (url.endsWith("/merchant-products/observation")) return blockedObservation?.promise || Promise.resolve({ data: copy(OBSERVED) });
        if (url.endsWith("/commercial-plans/seasonal-focus")) return Promise.resolve({ data: copy(planFixture.initial_plan) });
        if (url.endsWith("/readiness")) return Promise.resolve({ data: { accounts: [account], permissions: { owner: actor.role === "owner", can_execute: false }, campaign_types: [] } });
        if (url.endsWith("/workspace")) return Promise.resolve({ data: { accounts: [account], items: [], permissions: { owner: actor.role === "owner" } } });
        return Promise.resolve({ data: { items: [] } });
    });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render() { await act(async () => root.render(<AuthProvider><Harness /></AuthProvider>)); }
function button(text) { return [...container.querySelectorAll("button")].find((node) => node.textContent === text); }
async function click(text) { await act(async () => button(text).click()); }
const merchantReads = () => api.get.mock.calls.filter(([url]) => url.includes("/merchant-products/"));
async function select() {
    for (const [label, id] of [["حساب إعلانات Google", ACCOUNT], ["حساب Merchant Center", MERCHANT]]) {
        const element = container.querySelector(`select[aria-label="${label}"]`);
        await act(async () => { element.value = id; element.dispatchEvent(new Event("change", { bubbles: true })); });
    }
}

test("real workspace and auth render the owner selector without inheriting the current campaign account or auto-reading Merchant", async () => {
    await render(); expect(container.querySelector('[aria-label="حساب إعلانات جوجل"]').value).toBe(ACCOUNT);
    expect(container.querySelector('[data-testid="google-merchant-observation-selector"]')).not.toBeNull();
    expect(merchantReads()).toHaveLength(0);
    await click("تحديث قائمة الحسابات");
    expect(container.querySelector('[aria-label="حساب إعلانات Google"]').value).toBe("");
    expect(container.querySelector('[aria-label="حساب Merchant Center"]').value).toBe("");
    await select(); expect(merchantReads()).toHaveLength(1);
    await click("قراءة منتجات Merchant Center"); expect(merchantReads()).toHaveLength(2);
    expect(merchantReads()[1][1].params).toEqual({ account_id: ACCOUNT, merchant_id: MERCHANT });
    expect(container.textContent).toContain("لم تُرجع القراءة منتجات");
    expect(container.textContent).toContain("لا يثبت ذلك خلو متجر سلة");
    expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled(); expect(api.delete).not.toHaveBeenCalled();
});

test.each(["list", "child"])("actual parent/auth same-owner refresh clears pending %s and ignores late success", async (kind) => {
    await render(); const old = pending();
    if (kind === "list") blockedChoices = old;
    await click("تحديث قائمة الحسابات");
    if (kind === "child") { await select(); blockedObservation = old; await click("قراءة منتجات Merchant Center"); }
    const signal = merchantReads().at(-1)[1].signal;
    await click("تحديث جلسة المالك للاختبار"); expect(signal.aborted).toBe(true);
    expect(container.querySelector('[aria-label="حساب Merchant Center"]')).toBeNull();
    await act(async () => old.resolve({ data: copy(kind === "list" ? CHOICES : OBSERVED) }));
    expect(container.querySelector('[aria-label="حساب Merchant Center"]')).toBeNull();
    expect(container.textContent).not.toContain("لم تُرجع القراءة منتجات");
    expect(merchantReads()).toHaveLength(kind === "list" ? 1 : 2);
});

test.each([401, 503])("actual parent/auth refresh failure%s removes the owner selector without reconnect writes", async (status) => {
    await render(); await click("تحديث قائمة الحسابات"); await select();
    authFailure = { response: { status }, message: "PRIVATE_AUTH_FAILURE" };
    await click("تحديث جلسة المالك للاختبار");
    expect(container.querySelector('[data-testid="google-merchant-observation-selector"]')).toBeNull();
    expect(container.textContent).not.toContain("PRIVATE_AUTH_FAILURE");
    expect(merchantReads()).toHaveLength(1); expect(api.post).not.toHaveBeenCalled();
});

test("staff cannot obtain Merchant choices by opening the actual Google workspace", async () => {
    actor = { id: "synthetic-staff", role: "viewer" }; await render();
    expect(container.querySelector('[data-testid="google-ads-workspace"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="google-merchant-observation-selector"]')).toBeNull();
    expect(merchantReads()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
});
