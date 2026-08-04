import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ArabicDateRangePicker, {
    adsDatePreset,
    formatRangeLabel,
    monthGrid,
} from "./ArabicDateRangePicker";

describe("ArabicDateRangePicker", () => {
    const anchor = new Date(Date.UTC(2026, 7, 4));

    test("builds the expected Arabic reporting presets", () => {
        expect(adsDatePreset("today", anchor)).toEqual({
            dateFrom: "2026-08-04",
            dateTo: "2026-08-04",
        });
        expect(adsDatePreset("last7", anchor)).toEqual({
            dateFrom: "2026-07-29",
            dateTo: "2026-08-04",
        });
        expect(adsDatePreset("previousMonth", anchor)).toEqual({
            dateFrom: "2026-07-01",
            dateTo: "2026-07-31",
        });
        expect(adsDatePreset("monthToDate", anchor)).toEqual({
            dateFrom: "2026-08-01",
            dateTo: "2026-08-04",
        });
    });

    test("formats one day and a range in Arabic", () => {
        expect(formatRangeLabel("2026-08-04", "2026-08-04")).toBe("4 أغسطس 2026");
        expect(formatRangeLabel("2026-07-29", "2026-08-04")).toBe("29 يوليو — 4 أغسطس 2026");
    });

    test("renders a stable six-week calendar grid", () => {
        const grid = monthGrid(new Date(Date.UTC(2026, 7, 1)));
        expect(grid).toHaveLength(42);
        expect(grid[0].toISOString().slice(0, 10)).toBe("2026-07-26");
        expect(grid.at(-1).toISOString().slice(0, 10)).toBe("2026-09-05");
    });

    test("keeps two native date inputs for Snapchat account timezone sync", async () => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        const container = document.createElement("div");
        document.body.appendChild(container);
        const root = createRoot(container);
        const onApply = jest.fn();

        await act(async () => {
            root.render(
                <ArabicDateRangePicker
                    valueFrom="2026-08-04"
                    valueTo="2026-08-04"
                    onApply={onApply}
                />,
            );
        });

        const inputs = [...container.querySelectorAll('input[type="date"]')];
        expect(inputs).toHaveLength(2);
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype,
            "value",
        ).set;

        await act(async () => {
            setter.call(inputs[0], "2026-08-03");
            inputs[0].dispatchEvent(new Event("change", { bubbles: true }));
            setter.call(inputs[1], "2026-08-03");
            inputs[1].dispatchEvent(new Event("change", { bubbles: true }));
            await Promise.resolve();
        });

        expect(onApply).toHaveBeenCalledWith({
            dateFrom: "2026-08-03",
            dateTo: "2026-08-03",
        });

        await act(async () => root.unmount());
        container.remove();
    });
});
