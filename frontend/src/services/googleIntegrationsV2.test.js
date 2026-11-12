import api from "../lib/api";
import { getGoogleAccountSelection, normalizeGoogleAccountSelection, saveGoogleAccountSelection,
    startGoogleConnection } from "./googleIntegrationsV2";

jest.mock("../lib/api", () => ({ get: jest.fn(), put: jest.fn(), post: jest.fn() }));

const path = "/integrations-v2/google_ads/accounts-selection";
const first = "1111111111", second = "2222222222";
const payload = () => ({
    provider: "google_ads", discovered_count: 2, selected_count: 1,
    selection_configured: true, selection_required: false,
    expected_account_ids: [first, second], source_only: true,
    provider_write_reached: false, campaign_write_reached: false,
    accounting_write_reached: false, qoyod_write_reached: false,
    accounts: [
        { account_id: first, display_name: "حساب فعّال", currency: "SAR", timezone: "Asia/Riyadh", selected: true },
        { account_id: second, display_name: "حساب سابق", currency: "USD", timezone: "Asia/Riyadh", selected: false },
    ],
});

beforeEach(() => jest.resetAllMocks());

test("reads the owner's saved reporting choice once without a native refresh", async () => {
    api.get.mockResolvedValue({ data: payload() });
    const result = await getGoogleAccountSelection();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith(path);
    expect(result.accounts.map((account) => account.selected)).toEqual([true, false]);
    expect(result.expected_account_ids).toEqual([first, second]);
    expect(result.selected_count).toBe(1);
    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
});

test("saves only the bounded selected and expected identifiers and preserves the account list", async () => {
    const saved = payload();
    saved.accounts[0].selected = false;
    saved.accounts[1].selected = true;
    api.put.mockResolvedValue({ data: saved });
    const result = await saveGoogleAccountSelection(["222-222-2222"], [first, second]);
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.put).toHaveBeenCalledWith(path, {
        selected_account_ids: [second], expected_account_ids: [first, second],
    });
    expect(result.accounts).toHaveLength(2);
    expect(result.accounts.map((account) => account.selected)).toEqual([false, true]);
    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
});

test.each([
    [[], [first, second]], [[first], []], [[first, first], [first, second]],
    [[first], [first, first]], [["3333333333"], [first, second]], [[false], [first]],
    [[first], ["bad-id"]], [[first], Array.from({ length: 51 }, (_, index) => String(1000000000 + index))],
])("rejects an invalid or unbounded selection before writing (%j)", async (selected, expected) => {
    await expect(saveGoogleAccountSelection(selected, expected)).rejects.toThrow("invalid_google_account_selection");
    expect(api.put).not.toHaveBeenCalled();
});

test.each([
    { source_only: false }, { provider: "meta_ads" }, { provider_write_reached: true },
    { campaign_write_reached: true }, { accounting_write_reached: true }, { qoyod_write_reached: true },
    { expected_account_ids: [first] }, { expected_account_ids: [first, first] },
    { accounts: [{ account_id: first, selected: "false" }, { account_id: second, selected: false }] },
])("rejects unsafe or inconsistent selection evidence (%j)", (change) => {
    expect(() => normalizeGoogleAccountSelection({ ...payload(), ...change })).toThrow();
});

test("keeps an explicit empty saved scope distinct from the legacy unconfigured state", () => {
    const value = payload();
    value.accounts.forEach((account) => { account.selected = false; });
    value.selection_required = true;
    const savedEmpty = normalizeGoogleAccountSelection(value);
    const legacy = normalizeGoogleAccountSelection({ ...value, selection_configured: false });
    expect(savedEmpty.selected_count).toBe(0);
    expect(savedEmpty.selection_configured).toBe(true);
    expect(legacy.selected_count).toBe(0);
    expect(legacy.selection_configured).toBe(false);
});

test("retains missing saved account evidence without adding hidden IDs to the selectable scope", () => {
    const value = { ...payload(), expected_account_ids: [second], selection_required: true,
        missing_selected_account_ids: [first], accounts: [{ account_id: second, selected: true }] };
    const normalized = normalizeGoogleAccountSelection(value);
    expect(normalized.expected_account_ids).toEqual([second]);
    expect(normalized.missing_selected_account_ids).toEqual([first]);
    expect(normalized.selection_required).toBe(true);
    expect(normalized.accounts.map((account) => account.account_id)).toEqual([second]);
});

test("returns stale-scope 409 to the caller without retrying or starting sync", async () => {
    const conflict = { response: { status: 409, data: { detail: { code: "google_selection_scope_changed" } } } };
    api.put.mockRejectedValue(conflict);
    await expect(saveGoogleAccountSelection([first], [first, second])).rejects.toBe(conflict);
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
});

test.each(["network", "invalid-response"])("requires verification after an uncertain %s save outcome", async (kind) => {
    if (kind === "network") api.put.mockRejectedValue(new Error("connection lost"));
    else api.put.mockResolvedValue({ data: { ...payload(), accounts: [] } });
    await expect(saveGoogleAccountSelection([first], [first, second]))
        .rejects.toMatchObject({ applyOutcomeUnknown: true });
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.get).not.toHaveBeenCalled();
});

test("preserves the existing trusted Google OAuth entry point", async () => {
    api.post.mockResolvedValue({ data: { authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?state=example" } });
    expect((await startGoogleConnection()).authorization_url).toContain("https://accounts.google.com/");
    expect(api.post).toHaveBeenCalledWith("/integrations-v2/google/connect/start");
});
