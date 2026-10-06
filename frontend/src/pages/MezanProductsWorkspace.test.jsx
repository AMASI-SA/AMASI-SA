import React, { act } from "react";
import { createRoot } from "react-dom/client";

import {
    getProductV2,
    getProductV2Costs,
    listWorkspaceProducts,
    refreshProductV2Details,
    saveProductV2Costs,
} from "../services/mezanProductsV2";
import MezanProductsWorkspace from "./MezanProductsWorkspace";

jest.mock("../services/mezanProductsV2", () => ({
    applyMissingSkus: jest.fn(),
    getProductV2: jest.fn(),
    getProductV2Costs: jest.fn(),
    getProductsV2Summary: jest.fn(),
    listWorkspaceProducts: jest.fn(),
    previewMissingSkus: jest.fn(),
    refreshProductV2Details: jest.fn(),
    saveProductV2Costs: jest.fn(),
    syncProductsV2: jest.fn(),
}));
jest.mock("../components/products/ProductControlCenterPanel", () => ({ product }) => <div data-testid="control-center">{product?.name}</div>);
jest.mock("../components/products/ProductMediaDraftEditor", () => () => <div />);
jest.mock("../components/products/ProductOptionCostEditor", () => () => <div />);
jest.mock("../components/products/ProductOperationsEditor", () => () => <div />);

const service = jest.requireMock("../services/mezanProductsV2");
global.IS_REACT_ACT_ENVIRONMENT = true;

const PRODUCT = {
    id: "mpv2-1",
    mezan_product_id: "mpv2-1",
    salla_product_id: "358058139",
    name: "منتج محلي",
    variants: [],
    options: [],
};

function soldMissingResult(items = []) {
    return {
        items,
        pagination: { page: 1, total: items.length, total_pages: 1 },
        meta: {
            contract_version: "sold-missing-cost-v3",
            missing_mezan_cost: true,
            sold_only: true,
            cost_semantics: {
                missing_mezan_cost: "explicit_mezan_cost_only",
                calculation_cost: "mezan_then_salla_fallback",
            },
        },
    };
}

async function flush() {
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

describe("MezanProductsWorkspace local-first editor", () => {
    let container;
    let root;

    beforeEach(() => {
        jest.clearAllMocks();
        window.localStorage.clear();
        window.history.replaceState({}, "", "/products-v2?product=mpv2-1&focus=cost");
        window.HTMLElement.prototype.scrollIntoView = jest.fn();
        listWorkspaceProducts.mockResolvedValue({ items: [PRODUCT], pagination: { page: 1, total: 1, total_pages: 1 } });
        service.getProductsV2Summary.mockResolvedValue({ total: 1 });
        getProductV2.mockResolvedValue(PRODUCT);
        getProductV2Costs.mockResolvedValue({ base_cost: 12, variant_costs: {}, notes: "" });
        refreshProductV2Details.mockRejectedValue(new Error("Salla unavailable"));
        saveProductV2Costs.mockResolvedValue({ ok: true });
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
    });

    test("opening a selected product renders local data without a Salla refresh", async () => {
        act(() => root.render(<MezanProductsWorkspace />));
        await flush();

        expect(getProductV2).toHaveBeenCalledWith("mpv2-1");
        expect(refreshProductV2Details).not.toHaveBeenCalled();
        expect(container.textContent).toContain("منتج محلي");
        expect(container.textContent).toContain("التكلفة الأساسية — Mezan");
    });

    test("a direct missing-cost link targets one product and skips the full cohort", async () => {
        window.history.replaceState({}, "", "/products-v2?missing_mezan_cost=1&sold_only=1&product=mpv2-1&focus=cost");
        listWorkspaceProducts.mockResolvedValue(soldMissingResult([]));

        act(() => root.render(<MezanProductsWorkspace />));
        await flush();

        expect(listWorkspaceProducts).toHaveBeenCalledWith(expect.objectContaining({
            missingMezanCost: true,
            soldOnly: true,
            productIds: "mpv2-1",
        }));
    });

    test("selected product outside the filter is explained instead of contradicting the empty list", async () => {
        window.history.replaceState({}, "", "/products-v2?missing_mezan_cost=1&sold_only=1&product=mpv2-1&product_ids=mpv2-1&focus=cost");
        listWorkspaceProducts.mockResolvedValue(soldMissingResult([]));

        act(() => root.render(<MezanProductsWorkspace />));
        await flush();

        expect(container.textContent).toContain("المنتج المحدد مفتوح مباشرة، لكنه غير موجود حاليًا ضمن نتيجة الفلتر.");
    });

    test("saving Mezan cost stays local while Salla is unavailable", async () => {
        act(() => root.render(<MezanProductsWorkspace />));
        await flush();
        const costSection = container.querySelector("#mezan-product-cost-editor");
        const saveButton = [...costSection.querySelectorAll("button")].find((button) => button.textContent.includes("حفظ"));

        await act(async () => saveButton.click());
        await flush();

        expect(saveProductV2Costs).toHaveBeenCalledWith("mpv2-1", expect.objectContaining({ base_cost: 12 }));
        expect(refreshProductV2Details).not.toHaveBeenCalled();
    });

    test("manual Salla refresh failure keeps the local editor mounted", async () => {
        act(() => root.render(<MezanProductsWorkspace />));
        await flush();
        const refreshButton = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("تحديث بيانات سلة"));

        await act(async () => refreshButton.click());
        await flush();

        expect(refreshProductV2Details).toHaveBeenCalledTimes(1);
        expect(container.textContent).toContain("تعذر تحديث بيانات سلة — البيانات المحلية ما زالت متاحة");
        expect(container.textContent).toContain("منتج محلي");
    });
});
