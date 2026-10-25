import React, { act } from "react";
import { createRoot } from "react-dom/client";
import Panel from "./SnapchatCampaignPublicationPanel";
import { getSnapchatManagementReadiness } from "../../services/snapchatCampaignManagement";
import { listProductsV2 } from "../../services/mezanProductsV2";
import * as service from "../../services/snapchatCampaignPublication";

jest.mock("../../services/snapchatCampaignManagement", () => ({ getSnapchatManagementReadiness: jest.fn() }));
jest.mock("../../services/mezanProductsV2", () => ({ listProductsV2: jest.fn() }));
jest.mock("../../services/snapchatCampaignPublication", () => ({
    approveSnapchatPublication: jest.fn(), executeSnapchatPublication: jest.fn(),
    getSnapchatPublication: jest.fn(), getSnapchatPublicationByKey: jest.fn(),
    listSnapchatPublications: jest.fn(), previewSnapchatPublication: jest.fn(),
    reconcileSnapchatPublication: jest.fn(), reviewSnapchatPublication: jest.fn(),
    publicationAmountToMicro: (value) => Number(value) * 1000000,
    publicationError: (error) => error.message,
}));
jest.mock("./SnapchatMediaUploadPanel", () => function FakeUpload(props) {
    return require("react").createElement("button", { type: "button", "data-testid": "choose-real-upload",
        disabled: props.disabled, onClick: () => {
            props.onProfileChange("profile-1", { ownerId: props.ownerId, accountId: props.accountId });
            props.onReady({ upload_id: "a".repeat(64), provider_media_id: "media-1",
                account_id: props.accountId, ready_for_creative: true },
                { ownerId: props.ownerId, accountId: props.accountId });
        } }, "existing upload callback");
});

let root, container;
const job = { publication_id: "job-1", account_id: "account-1", status: "approved", revision: 2,
    plan: { campaign_name: "Campaign", daily_budget_micro: 25000000, currency: "SAR", country: "sa" },
    steps: [{ action: "campaign.create", status: "pending", provider_entity_id: null }] };

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    getSnapchatManagementReadiness.mockResolvedValue({ execution_enabled: true, activation_enabled: true, accounts: ["account-1", "account-2"].map((id) => ({
        account_id: id, display_name: id, currency: "SAR", management_allowed: true, creative_allowed: true,
        pixels: [{ pixel_id: "pixel-1", display_name: "Pixel" }],
    })) });
    service.listSnapchatPublications.mockResolvedValue([]);
    listProductsV2.mockResolvedValue({ items: [{ salla_product_id: "product-1", name: "Product",
        variants: [{ id: "variant-1", name: "Option" }] }] });
    service.previewSnapchatPublication.mockResolvedValue({ ...job, status: "previewed", confirm_token: "token", revision: 1 });
    service.approveSnapchatPublication.mockResolvedValue(job);
    service.executeSnapchatPublication.mockResolvedValue(job);
    service.getSnapchatPublication.mockResolvedValue(job);
    Object.defineProperty(window.crypto, "randomUUID", { configurable: true, value: () => "stable-browser-key" });
    container = document.createElement("div"); document.body.appendChild(container);
    root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(accountId = "account-1", ownerId = "owner") {
    await act(async () => root.render(<Panel ownerId={ownerId} accountId={accountId} />));
}
async function click(selector) { await act(async () => container.querySelector(selector).click()); }
async function input(node, value) {
    const setter = Object.getOwnPropertyDescriptor(
        node.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype, "value").set;
    await act(async () => { setter.call(node, value); node.dispatchEvent(new Event("change", { bubbles: true }));
        node.dispatchEvent(new Event("input", { bubbles: true })); });
}

test("opening the form performs reads only and supplies no spending/start/country defaults", async () => {
    await render();
    expect(container.querySelector('[data-testid="publication-budget"]').value).toBe("");
    expect(container.querySelector('[data-testid="publication-start"]').value).toBe("");
    expect(container.querySelector('[data-testid="publication-country"]').value).toBe("");
    expect(service.previewSnapchatPublication).not.toHaveBeenCalled();
    expect(service.approveSnapchatPublication).not.toHaveBeenCalled();
    expect(service.executeSnapchatPublication).not.toHaveBeenCalled();
    expect(container.textContent).toContain("نشطة مباشرةً");
    expect(container.textContent).toContain("قد يبدأ الصرف");
    expect(container.textContent).not.toContain("بحالة متوقفة");
});

test("a saved approved plan requires an explicit execution click", async () => {
    service.listSnapchatPublications.mockResolvedValue([job]);
    await render();
    const saved = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("Campaign"));
    await act(async () => saved.click());
    expect(service.executeSnapchatPublication).not.toHaveBeenCalled();
    await click('[data-testid="publication-execute"]');
    expect(service.executeSnapchatPublication).toHaveBeenCalledWith("job-1");
    expect(service.previewSnapchatPublication).not.toHaveBeenCalled();
});

test("account change clears uploaded media and the previous plan", async () => {
    await render();
    await click('[data-testid="choose-real-upload"]');
    expect(container.querySelector('[data-testid="publication-preview"]').disabled).toBe(false);
    await render("account-2");
    expect(container.querySelector('[data-testid="publication-preview"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="publication-progress"]')).toBeNull();
    expect(service.previewSnapchatPublication).not.toHaveBeenCalled();
});

test("expired plan review uses the same saved publication and does not execute", async () => {
    service.listSnapchatPublications.mockResolvedValue([{ ...job, status: "interrupted" }]);
    service.reviewSnapchatPublication.mockResolvedValue({ ...job, status: "previewed", revision: 3, confirm_token: "fresh" });
    await render();
    const saved = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("Campaign"));
    await act(async () => saved.click());
    await click('[data-testid="publication-review"]');
    expect(service.reviewSnapchatPublication).toHaveBeenCalledWith("job-1");
    expect(service.executeSnapchatPublication).not.toHaveBeenCalled();
    await click('[data-testid="publication-approve"]');
    expect(service.approveSnapchatPublication).toHaveBeenCalledWith(expect.objectContaining({
        publication_id: "job-1", revision: 3, confirm_token: "fresh",
    }));
});

test("reconciliation never starts another publication or executes a child", async () => {
    service.listSnapchatPublications.mockResolvedValue([{ ...job, status: "interrupted" }]);
    service.reconcileSnapchatPublication.mockResolvedValue({ ...job, status: "interrupted", current_step: 2 });
    await render();
    const saved = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("Campaign"));
    await act(async () => saved.click());
    await click('[data-testid="publication-reconcile"]');
    expect(service.reconcileSnapchatPublication).toHaveBeenCalledWith("job-1");
    expect(service.previewSnapchatPublication).not.toHaveBeenCalled();
    expect(service.executeSnapchatPublication).not.toHaveBeenCalled();
});

test("unknown active ad outcome blocks execution and shows created active levels", async () => {
    const unknown = { ...job, status: "interrupted", delivery_outcome_unknown: true, current_step: 3,
        steps: [{ action: "campaign.create", status: "completed", configured_status: "ACTIVE", provider_entity_id: "campaign-live" },
            { action: "ad_squad.create", status: "completed", configured_status: "ACTIVE", provider_entity_id: "group-live" }] };
    service.listSnapchatPublications.mockResolvedValue([unknown]);
    await render();
    const saved = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("Campaign"));
    await act(async () => saved.click());
    expect(container.querySelector('[data-testid="publication-execute"]').disabled).toBe(true);
    expect(container.textContent).toContain("قد يكون الإعلان نشطًا وبدأ الصرف");
    expect(container.textContent).toContain("campaign-live");
    expect(service.executeSnapchatPublication).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="publication-reconcile"]').disabled).toBe(false);
});

test("completed configuration is ACTIVE while provider review and delivery remain pending", async () => {
    const complete = { ...job, status: "completed", configured_delivery_status: "ACTIVE",
        provider_delivery_status: ["PENDING_REVIEW"],
        review: { creative_review_status: "PENDING_REVIEW", creative_packaging_status: "SUCCESS",
            ad_review_status: "PENDING", ad_delivery_status: ["PENDING_REVIEW"] } };
    service.listSnapchatPublications.mockResolvedValue([complete]);
    await render();
    const saved = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("Campaign"));
    await act(async () => saved.click());
    expect(container.textContent).toContain("أُنشئت الحملة والمجموعة والإعلان نشطة مباشرةً");
    expect(container.textContent).toContain("حالة التسليم لدى Snapchat");
    expect(container.textContent).toContain("PENDING_REVIEW");
    expect(container.querySelector('[data-testid="publication-execute"]')).toBeNull();
    expect(service.executeSnapchatPublication).not.toHaveBeenCalled();
});

test("an unavailable activation flag disables direct ACTIVE creation", async () => {
    getSnapchatManagementReadiness.mockResolvedValue({ execution_enabled: true, activation_enabled: false,
        accounts: [{ account_id: "account-1", display_name: "Account", currency: "SAR",
            management_allowed: true, creative_allowed: true, pixels: [] }] });
    service.listSnapchatPublications.mockResolvedValue([job]);
    await render();
    const saved = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("Campaign"));
    await act(async () => saved.click());
    expect(container.querySelector('[data-testid="publication-execute"]').disabled).toBe(true);
    expect(container.textContent).toContain("التشغيل المباشر غير متاح");
    expect(service.executeSnapchatPublication).not.toHaveBeenCalled();
});

test("owner change clears the previous owner's saved operations before new reads finish", async () => {
    service.listSnapchatPublications.mockResolvedValueOnce([job]).mockReturnValueOnce(new Promise(() => {}));
    await render();
    expect(container.textContent).toContain("Campaign");
    await render("account-1", "different-owner");
    expect(container.textContent).not.toContain("Campaign");
    expect(service.executeSnapchatPublication).not.toHaveBeenCalled();
});

