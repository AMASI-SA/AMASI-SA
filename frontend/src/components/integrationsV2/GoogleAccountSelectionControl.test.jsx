import React, { act } from "react";
import { createRoot } from "react-dom/client";
import GoogleAccountSelectionControl from "./GoogleAccountSelectionControl";
import { getGoogleAccountSelection, saveGoogleAccountSelection, startGoogleConnection } from "../../services/googleIntegrationsV2";

jest.mock("../../services/googleIntegrationsV2", () => ({
    getGoogleAccountSelection: jest.fn(), saveGoogleAccountSelection: jest.fn(), startGoogleConnection: jest.fn(),
}));

const first = "1111111111", second = "2222222222", third = "3333333333";
function selection(selected = [first], ids = [first, second], configured = true) {
    return { provider: "google_ads", expected_account_ids: ids,
        selection_configured: configured, selection_required: selected.length === 0,
        accounts: ids.map((id, index) => ({ account_id: id, selected: selected.includes(id),
            display_name: ["الحساب الفعّال", "الحساب السابق", "حساب إضافي"][index], currency: "SAR", timezone: "Asia/Riyadh" })),
    };
}
let host, root;
const button = (id) => host.querySelector(`[data-testid="${id}"]`);
const click = async (id) => act(async () => button(id).click());
const open = () => click("google-account-selection-toggle");
const checkboxes = () => [...host.querySelectorAll('input[type="checkbox"]')];

beforeEach(async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.resetAllMocks();
    getGoogleAccountSelection.mockResolvedValue(selection());
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
    await act(async () => root.render(<GoogleAccountSelectionControl />));
});
afterEach(async () => {
    await act(async () => root.unmount()); host.remove(); delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("loads once on opening, shows owner choices, and does not fetch again on reopen", async () => {
    expect(getGoogleAccountSelection).not.toHaveBeenCalled();
    let resolve;
    getGoogleAccountSelection.mockImplementation(() => new Promise((done) => { resolve = done; }));
    await open();
    expect(host.textContent).toContain("جاري تحميل الحسابات");
    await act(async () => resolve(selection()));
    expect(checkboxes().map((input) => input.checked)).toEqual([true, false]);
    expect(host.textContent).toContain("1 من 2 حساب محدد");
    expect(host.textContent).toContain("تبقى الحسابات الأخرى وبياناتها محفوظة");
    await open(); await open();
    expect(getGoogleAccountSelection).toHaveBeenCalledTimes(1);
    expect(saveGoogleAccountSelection).not.toHaveBeenCalled();
    expect(startGoogleConnection).not.toHaveBeenCalled();
});

test("saves a changed subset against the loaded account set without removing other accounts", async () => {
    await open();
    expect(button("google-save-account-selection").disabled).toBe(true);
    await act(async () => { checkboxes()[0].click(); checkboxes()[1].click(); });
    saveGoogleAccountSelection.mockResolvedValue(selection([second]));
    await click("google-save-account-selection");
    expect(saveGoogleAccountSelection).toHaveBeenCalledTimes(1);
    expect(saveGoogleAccountSelection).toHaveBeenCalledWith([second], [first, second]);
    expect(checkboxes()).toHaveLength(2);
    expect(checkboxes().map((input) => input.checked)).toEqual([false, true]);
    expect(host.textContent).toContain("تم حفظ نطاق التقارير");
    expect(button("google-save-account-selection").disabled).toBe(true);
    expect(getGoogleAccountSelection).toHaveBeenCalledTimes(1);
    expect(startGoogleConnection).not.toHaveBeenCalled();
});

test.each([true, false])("makes a configured=%s empty choice explicit and cannot save it", async (configured) => {
    getGoogleAccountSelection.mockResolvedValue(selection([], [first, second], configured));
    await open();
    expect(checkboxes().map((input) => input.checked)).toEqual([false, false]);
    expect(host.textContent).toContain(configured ? "لا يوجد حساب متاح محدد للتقارير" : "لم يُحفظ اختيار حسابات التقارير بعد");
    expect(button("google-save-account-selection").disabled).toBe(true);
    await act(async () => checkboxes()[0].click());
    expect(button("google-save-account-selection").disabled).toBe(false);
});

test("shows no discovered accounts without inventing a selection or a save action", async () => {
    getGoogleAccountSelection.mockResolvedValue(selection([], [], false));
    await open();
    expect(host.textContent).toContain("لا توجد حسابات Google محفوظة لهذا المتجر");
    expect(checkboxes()).toHaveLength(0);
    expect(button("google-save-account-selection")).toBeNull();
});

test("lets the owner explicitly repair a saved scope containing a now-missing account", async () => {
    getGoogleAccountSelection.mockResolvedValue({ ...selection([second], [second]),
        selection_required: true, missing_selected_account_ids: [first] });
    await open();
    expect(checkboxes()).toHaveLength(1);
    expect(checkboxes()[0].checked).toBe(true);
    expect(host.textContent).toContain("بعض الحسابات المحددة سابقًا غير متاحة");
    expect(button("google-save-account-selection").disabled).toBe(false);
    saveGoogleAccountSelection.mockResolvedValue(selection([second], [second]));
    await click("google-save-account-selection");
    expect(saveGoogleAccountSelection).toHaveBeenCalledWith([second], [second]);
    expect(host.textContent).not.toContain("بعض الحسابات المحددة سابقًا غير متاحة");
    expect(button("google-save-account-selection").disabled).toBe(true);
});

test.each([403, 503])("shows a load error %s and only retries on explicit request", async (status) => {
    getGoogleAccountSelection.mockRejectedValue({ response: { status } });
    await open();
    expect(host.querySelector('[role="alert"]').textContent).toContain(status === 403 ? "متاح لمالك المتجر" : "تعذر تحميل");
    expect(getGoogleAccountSelection).toHaveBeenCalledTimes(1);
    getGoogleAccountSelection.mockResolvedValue(selection());
    await click("google-reload-account-selection");
    expect(getGoogleAccountSelection).toHaveBeenCalledTimes(2);
    expect(checkboxes()).toHaveLength(2);
});

test("stale scope 409 blocks another save until reload and review of the changed account set", async () => {
    await open();
    await act(async () => checkboxes()[1].click());
    saveGoogleAccountSelection.mockRejectedValue({ response: { status: 409, data: { detail: { code: "scope_changed" } } } });
    await click("google-save-account-selection");
    expect(host.textContent).toContain("تغيّرت قائمة حسابات Google");
    expect(button("google-save-account-selection").disabled).toBe(true);
    expect(checkboxes().every((input) => input.disabled)).toBe(true);
    expect(getGoogleAccountSelection).toHaveBeenCalledTimes(1);
    getGoogleAccountSelection.mockResolvedValue(selection([first], [first, second, third]));
    await click("google-reload-account-selection");
    expect(checkboxes()).toHaveLength(3);
    expect(checkboxes().map((input) => input.checked)).toEqual([true, false, false]);
    await act(async () => checkboxes()[2].click());
    saveGoogleAccountSelection.mockResolvedValue(selection([first, third], [first, second, third]));
    await click("google-save-account-selection");
    expect(saveGoogleAccountSelection).toHaveBeenLastCalledWith([first, third], [first, second, third]);
});

test.each([
    { response: { status: 503, data: { detail: { apply_outcome_unknown: true } } } },
    { applyOutcomeUnknown: true },
])("requires re-reading an uncertain save outcome without retrying the write", async (error) => {
    await open();
    await act(async () => checkboxes()[1].click());
    saveGoogleAccountSelection.mockRejectedValue(error);
    await click("google-save-account-selection");
    expect(host.textContent).toContain("لم نتأكد من نتيجة الحفظ");
    expect(button("google-save-account-selection").disabled).toBe(true);
    expect(saveGoogleAccountSelection).toHaveBeenCalledTimes(1);
    expect(getGoogleAccountSelection).toHaveBeenCalledTimes(1);
});

test("does not install a periodic read while the control remains open", async () => {
    const interval = jest.spyOn(window, "setInterval");
    await open();
    expect(interval).not.toHaveBeenCalled();
    expect(getGoogleAccountSelection).toHaveBeenCalledTimes(1);
    interval.mockRestore();
});
