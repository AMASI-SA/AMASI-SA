import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ProductOptionCostEditor from "./ProductOptionCostEditor";
import { getProductOptionCosts, refreshProductV2Details, saveProductOptionCost } from "../../services/mezanProductsV2";

jest.mock("../../services/mezanProductsV2", () => ({
    getProductOptionCosts: jest.fn(), refreshProductV2Details: jest.fn(),
    saveProductOptionCost: jest.fn(), deleteProductOptionCost: jest.fn(),
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("@phosphor-icons/react", () => ({ LinkSimple: () => null, Plus: () => null, SpinnerGap: () => null, Trash: () => null }));

const options = [{ id: "size", name: "المقاس", type: "select", values: [{ id: "child", name: "صغار" }] }];

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    refreshProductV2Details.mockResolvedValue({ product: { options } });
    saveProductOptionCost.mockResolvedValue({ ok: true });
});

async function openEditor(mode) {
    getProductOptionCosts.mockResolvedValue({
        resources: [{ id: "resource", name: "مكون", unit_cost: 60, status: "active" }],
        bindings: [{ option_id: "size", value_id: "child", mode, quantity: 60,
            direct_amount: 60, resource_id: "resource", resolved_amount: 60 }],
    });
    const host = document.createElement("div"); document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => { root.render(<ProductOptionCostEditor productId="product" options={options} />); });
    const click = async label => {
        const button = [...host.querySelectorAll("button")].find(node => node.textContent.trim() === label);
        expect(button).toBeDefined();
        await act(async () => button.click());
    };
    await click("تعديل التكلفة");
    return { host, root, click };
}

test.each(["direct", "resource"])("saving direct cost from %s does not send a hidden quantity multiplier", async mode => {
    const { host, root, click } = await openEditor(mode);
    try {
        if (mode === "resource") await click("مبلغ مباشر");
        await click("حفظ");
        expect(saveProductOptionCost).toHaveBeenCalledWith("product", "size", "child", {
            mode: "direct", direct_amount: 60, resource_id: null, quantity: 1,
        });
    } finally {
        await act(async () => root.unmount()); host.remove();
    }
});

test("resource costs retain their explicit quantity", async () => {
    const { host, root, click } = await openEditor("resource");
    try {
        await click("حفظ");
        expect(saveProductOptionCost).toHaveBeenCalledWith("product", "size", "child", {
            mode: "resource", direct_amount: null, resource_id: "resource", quantity: 60,
        });
    } finally {
        await act(async () => root.unmount()); host.remove();
    }
});
