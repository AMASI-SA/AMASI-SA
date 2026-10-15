import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../../services/fulfillmentExperiment", () => ({
    createFulfillmentHold: jest.fn(),
    getFulfillmentExperimentState: jest.fn(),
    releaseFulfillmentHold: jest.fn(),
    resetFulfillmentExperiment: jest.fn(),
}));

import FulfillmentExperimentPanel from "./FulfillmentExperimentPanel";
import {
    getFulfillmentExperimentState,
    resetFulfillmentExperiment,
} from "../../services/fulfillmentExperiment";

describe("FulfillmentExperimentPanel", () => {
    let container;
    let root;

    beforeEach(() => {
        globalThis.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        getFulfillmentExperimentState.mockReset();
        resetFulfillmentExperiment.mockReset();
        getFulfillmentExperimentState.mockResolvedValue({
            workflow_stage: "in_progress",
            pieces: [{ piece_id: "piece-1" }, { piece_id: "piece-2" }],
            holds: [],
            capabilities: {
                can_reset_experiment: true,
                can_manage_stops: false,
            },
        });
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    });

    test.each(["salla", "store_courier"])("confirms selected %s experiment flow in-app before reset", async (flow) => {
        const nativeConfirm = jest.spyOn(window, "confirm");
        resetFulfillmentExperiment.mockResolvedValue({
            run: { generation: 1 },
            archived_piece_count: 2,
        });

        try {
            await act(async () => {
                root.render(<FulfillmentExperimentPanel orderNumber="276628330" />);
                await Promise.resolve();
            });

            const selector = container.querySelector('[data-testid="fulfillment-experiment-delivery-flow"]');
            await act(async () => {
                selector.value = flow;
                selector.dispatchEvent(new Event("change", { bubbles: true }));
            });
            const resetButton = container.querySelector('[data-testid="fulfillment-experiment-reset"]');
            await act(async () => resetButton.click());

            expect(nativeConfirm).not.toHaveBeenCalled();
            expect(resetFulfillmentExperiment).not.toHaveBeenCalled();
            expect(container.querySelector('[data-testid="fulfillment-experiment-reset-dialog"]')).not.toBeNull();
            expect(container.textContent).toContain("الفاتورة والمحاسبة وشحنة سلة الحقيقية لن تتغير");

            await act(async () => container.querySelector('[data-testid="fulfillment-experiment-reset-cancel"]').click());
            expect(resetFulfillmentExperiment).not.toHaveBeenCalled();
            expect(container.querySelector('[data-testid="fulfillment-experiment-reset-dialog"]')).toBeNull();
            await act(async () => resetButton.click());
            const confirmButton = container.querySelector('[data-testid="fulfillment-experiment-reset-confirm"]');
            await act(async () => {
                confirmButton.click();
                await Promise.resolve();
                await Promise.resolve();
            });

            expect(resetFulfillmentExperiment).toHaveBeenCalledWith(
                "276628330",
                "إعادة الطلب لاختبار دورة التجهيز والصلاحيات",
                flow,
            );
            expect(container.textContent).toContain("بدأت التجربة رقم 1");
            expect(container.querySelector('[data-testid="fulfillment-experiment-reset-dialog"]')).toBeNull();
        } finally {
            nativeConfirm.mockRestore();
        }
    });
});
