import React, { act } from "react";
import { createRoot } from "react-dom/client";
import SnapchatAutonomyPanel from "./SnapchatAutonomyPanel";
import * as service from "../../services/snapchatAutonomy";

jest.mock("../../services/snapchatAutonomy", () => ({
    ...jest.requireActual("../../services/snapchatAutonomy"),
    listSnapchatAutonomy: jest.fn(), saveSnapchatAutonomy: jest.fn(),
    revokeSnapchatAutonomy: jest.fn(), getSnapchatAutonomyExecutions: jest.fn(),
}));

const VERSION = "snapchat-autonomy-final-write-v1";
const policy = {
    account_id: "account-a", display_name: "حساب أماسي", currency: "SAR", timezone: "Asia/Riyadh",
    revision: 3, configured_mode: "enabled", effective_mode: "enabled", execution_enabled: true,
    allowed_actions: ["pause", "reduce"], max_entity_daily_budget_micro: 100000000,
    max_daily_budget_increase_micro: 0, max_change_percent: 15, min_interval_minutes: 300,
    max_actions_per_cycle: 1, expires_at: "2030-01-01T12:00:00Z", acknowledgement_version: VERSION,
};
let container, root;
beforeEach(() => {
    jest.clearAllMocks(); container = document.createElement("div"); document.body.appendChild(container);
    root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    service.listSnapchatAutonomy.mockResolvedValue({ executor_ready: true, items: [{ ...policy }] });
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<SnapchatAutonomyPanel {...props} />)); }
function button(label) { return [...container.querySelectorAll("button")].find((item) => item.textContent === label); }

test("enabled editing requires fresh acknowledgement and keeps exact account limits", async () => {
    service.saveSnapchatAutonomy.mockResolvedValue({ ...policy, revision: 4 });
    await render();
    expect(button("حفظ التفويض").disabled).toBe(true);
    await act(async () => container.querySelector('[aria-label="أوافق على التنفيذ التلقائي ضمن الحدود"]').click());
    expect(button("حفظ التفويض").disabled).toBe(false);
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    const [account, body] = service.saveSnapchatAutonomy.mock.calls[0];
    expect(account).toBe("account-a");
    expect(body).toMatchObject({ expected_revision: 3, mode: "enabled", acknowledgement_version: VERSION,
        accept_autonomous_execution: true, max_entity_daily_budget_micro: 100000000,
        max_daily_budget_increase_micro: 0, allowed_actions: ["pause", "reduce"] });
    expect(container.querySelector('[aria-label="أوافق على التنفيذ التلقائي ضمن الحدود"]').checked).toBe(false);
});

test("a new policy invents no spending defaults and cannot enable an unavailable executor", async () => {
    service.listSnapchatAutonomy.mockResolvedValue({ executor_ready: false, items: [{
        account_id: "account-a", currency: "SAR", revision: 0, configured_mode: "disabled", effective_mode: "disabled",
    }] });
    await render();
    expect(container.querySelector('[aria-label="أقصى ميزانية يومية للكيان"]').value).toBe("");
    expect(container.querySelector('[aria-label="سقف زيادات الميزانيات في اليوم"]').value).toBe("");
    expect(container.querySelector('option[value="enabled"]').disabled).toBe(true);
    expect(container.textContent).toContain("التنفيذ التلقائي غير متاح حاليًا");
    expect(service.saveSnapchatAutonomy).not.toHaveBeenCalled();
});

test("revoke is one request with current revision, and state changes after server confirmation", async () => {
    let finish;
    service.revokeSnapchatAutonomy.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render();
    await act(async () => { button("إيقاف التفويض الآن").click(); button("إيقاف التفويض الآن").click(); });
    expect(service.revokeSnapchatAutonomy).toHaveBeenCalledTimes(1);
    expect(service.revokeSnapchatAutonomy).toHaveBeenCalledWith("account-a", 3);
    expect(container.querySelector('[data-testid="autonomy-effective-mode"]').textContent).toBe("تفويض تلقائي");
    await act(async () => finish({ ...policy, revision: 4, configured_mode: "disabled", effective_mode: "disabled", execution_enabled: false }));
    expect(container.querySelector('[data-testid="autonomy-effective-mode"]').textContent).toBe("موقوف");
});

test("revision conflict is shown without retry or falsely changing the active grant", async () => {
    service.revokeSnapchatAutonomy.mockRejectedValue({ response: { data: { detail: { message: "تغيرت الإعدادات؛ حدّث الصفحة." } } } });
    await render();
    await act(async () => button("إيقاف التفويض الآن").click());
    expect(container.querySelector('[role="alert"]').textContent).toContain("تغيرت الإعدادات");
    expect(service.revokeSnapchatAutonomy).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="autonomy-effective-mode"]').textContent).toBe("تفويض تلقائي");
});
