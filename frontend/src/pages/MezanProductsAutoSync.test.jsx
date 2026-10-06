import React, { act } from "react";
import { createRoot } from "react-dom/client";

import { syncRecentProductsV2 } from "../services/mezanProductsV2";
import MezanProductsAutoSync from "./MezanProductsAutoSync";

jest.mock("../services/mezanProductsV2", () => ({
    syncRecentProductsV2: jest.fn(),
}));
jest.mock("../components/products/ProductGoogleTaxonomyPilotPanel", () => () => <div>taxonomy</div>);
jest.mock("./MezanProductsWorkspace", () => () => <div>workspace</div>);

global.IS_REACT_ACT_ENVIRONMENT = true;

async function flush() {
    await act(async () => { await Promise.resolve(); });
}

describe("MezanProductsAutoSync pressure", () => {
    let container;
    let root;

    beforeEach(() => {
        jest.useFakeTimers();
        syncRecentProductsV2.mockReset().mockResolvedValue({ updated: 0, created: 0 });
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
        jest.useRealTimers();
        window.history.replaceState({}, "", "/");
    });

    test("an editor left open for five minutes performs no forced periodic sync", async () => {
        window.history.replaceState({}, "", "/products-v2?product=mpv2-1&focus=cost");
        act(() => root.render(<MezanProductsAutoSync />));

        act(() => jest.advanceTimersByTime(5 * 60_000));
        await flush();

        expect(syncRecentProductsV2).not.toHaveBeenCalled();
    });

    test("list mode uses one non-forced five-minute fallback", async () => {
        window.history.replaceState({}, "", "/products-v2?view=list");
        act(() => root.render(<MezanProductsAutoSync />));

        act(() => jest.advanceTimersByTime(5 * 60_000));
        await flush();

        expect(syncRecentProductsV2).toHaveBeenCalledTimes(1);
        expect(syncRecentProductsV2).toHaveBeenCalledWith({ force: false });
    });
});
