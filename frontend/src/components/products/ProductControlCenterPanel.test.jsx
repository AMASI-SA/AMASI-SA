import React, { act } from "react";
import { createRoot } from "react-dom/client";

import {
    getProductControlCenter,
    publishProductControlDraft,
    setProductPublishActivity,
    verifyProductControlPublishAttempt,
} from "../../services/mezanProductsV2";
import ProductControlCenterPanel from "./ProductControlCenterPanel";

jest.mock("../../lib/api", () => ({
    __esModule: true,
    default: { get: jest.fn().mockResolvedValue({ data: { items: [] } }) },
}));
jest.mock("../../services/mezanProductsV2", () => ({
    approveProductControlDraft: jest.fn(),
    getProductControlCenter: jest.fn(),
    getSallaCategoryCatalog: jest.fn().mockResolvedValue({ items: [] }),
    publishProductControlDraft: jest.fn(),
    saveProductControlDraft: jest.fn(),
    setProductPublishActivity: jest.fn(),
    verifyProductControlPublishAttempt: jest.fn(),
}));
jest.mock("./VisualHtmlEditor", () => () => <div data-testid="visual-editor" />);

global.IS_REACT_ACT_ENVIRONMENT = true;

const PRODUCT = { id: "mpv2-1", mezan_product_id: "mpv2-1", salla_product_id: "salla-1", name: "قديم", price: 100 };
const DRAFT = { id: "draft-1", status: "approved", changes: { name: "جديد" }, before: { name: "قديم" } };

function response(attempt = null) {
    return {
        product: PRODUCT,
        draft: DRAFT,
        publish_attempt: attempt,
        protected_fields: ["base_cost"],
    };
}

async function flush() {
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

describe("Product Control Center durable publish UI", () => {
    let container;
    let root;

    beforeEach(() => {
        jest.clearAllMocks();
        const service = jest.requireMock("../../services/mezanProductsV2");
        service.getSallaCategoryCatalog.mockResolvedValue({ items: [] });
        jest.requireMock("../../lib/api").default.get.mockResolvedValue({ data: { items: [] } });
        getProductControlCenter.mockResolvedValue(response());
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
    });

    async function renderEditor() {
        act(() => root.render(<ProductControlCenterPanel productId="mpv2-1" product={PRODUCT} />));
        await flush();
        const edit = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("تحرير"));
        act(() => edit.click());
        await flush();
    }

    test("restores verification_pending after reload and offers GET-only verification", async () => {
        getProductControlCenter.mockResolvedValue(response({ id: "attempt-1", status: "verification_pending" }));
        verifyProductControlPublishAttempt.mockResolvedValue({ attempt: { id: "attempt-1", status: "verification_pending" } });

        await renderEditor();

        expect(container.textContent).toContain("verification_pending");
        const verify = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("تحقق آمن"));
        await act(async () => verify.click());
        expect(verifyProductControlPublishAttempt).toHaveBeenCalledWith("mpv2-1", "attempt-1");
        expect(publishProductControlDraft).not.toHaveBeenCalled();
    });

    test("outcome_unknown explicitly says PUT will not be resent", async () => {
        getProductControlCenter.mockResolvedValue(response({ id: "attempt-2", status: "outcome_unknown" }));

        await renderEditor();

        expect(container.textContent).toContain("لن يعيد ميزان إرسال PUT");
        expect(setProductPublishActivity).toHaveBeenCalledWith("mpv2-1", true);
    });

    test("active attempt disables a second publish", async () => {
        getProductControlCenter.mockResolvedValue(response({ id: "attempt-3", status: "publishing" }));

        await renderEditor();

        const publish = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("نشر إلى سلة"));
        expect(publish.disabled).toBe(true);
    });

    test("successful publish refreshes local state once", async () => {
        const onPublished = jest.fn();
        publishProductControlDraft.mockResolvedValue({
            attempt: { id: "attempt-4", status: "succeeded" },
            revision: { salla_response: {} },
        });
        act(() => root.render(<ProductControlCenterPanel productId="mpv2-1" product={PRODUCT} onPublished={onPublished} />));
        await flush();
        const edit = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("تحرير"));
        act(() => edit.click());
        await flush();
        const publish = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("نشر إلى سلة"));

        await act(async () => publish.click());
        await flush();

        expect(publishProductControlDraft).toHaveBeenCalledTimes(1);
        expect(onPublished).toHaveBeenCalledTimes(1);
    });
});
