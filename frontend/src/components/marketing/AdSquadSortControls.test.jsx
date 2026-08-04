import React, { act } from "react";
import { createRoot } from "react-dom/client";
import AdSquadSortControls, {
    AD_SQUAD_SORT_EVENT,
    AD_SQUAD_SORT_STORAGE_KEY,
} from "./AdSquadSortControls";

describe("AdSquadSortControls", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        window.localStorage.clear();
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
    });

    test("changes the active sort immediately without refreshing the page", async () => {
        const events = [];
        const listener = (event) => events.push(event.detail?.sort_by);
        window.addEventListener(AD_SQUAD_SORT_EVENT, listener);

        await act(async () => {
            root.render(<AdSquadSortControls />);
        });

        const newest = container.querySelector('[data-testid="ad-squad-sort-newest"]');
        const spend = container.querySelector('[data-testid="ad-squad-sort-spend"]');
        const active = container.querySelector('[data-testid="ad-squad-sort-active"]');
        expect(newest.getAttribute("aria-pressed")).toBe("true");

        await act(async () => spend.click());
        expect(spend.getAttribute("aria-pressed")).toBe("true");
        expect(newest.getAttribute("aria-pressed")).toBe("false");
        expect(window.localStorage.getItem(AD_SQUAD_SORT_STORAGE_KEY)).toBe("spend");

        await act(async () => active.click());
        expect(active.getAttribute("aria-pressed")).toBe("true");
        expect(window.localStorage.getItem(AD_SQUAD_SORT_STORAGE_KEY)).toBe("active");
        expect(events).toEqual(["spend", "active"]);

        window.removeEventListener(AD_SQUAD_SORT_EVENT, listener);
    });
});
