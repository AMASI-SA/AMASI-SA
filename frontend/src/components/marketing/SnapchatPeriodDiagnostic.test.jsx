import React, { act } from "react";
import { createRoot } from "react-dom/client";
import SnapchatPeriodDiagnostic from "./SnapchatPeriodDiagnostic";
import api from "../../lib/api";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));

describe("SnapchatPeriodDiagnostic", () => {
    let container;
    let root;
    const props = { accountId: "a1", dateFrom: "2026-08-01", dateTo: "2026-09-17" };
    const payload = {
        ad_account_id: "a1", timezone: "America/Los_Angeles", periods: {
            whole: ["2026-08-01", "2026-09-17"], left: ["2026-08-01", "2026-08-24"], right: ["2026-08-25", "2026-09-17"],
        }, all_orders: { whole: 7, left: 2, right: 4, gap: 1 },
        explicit_snapchat_source: { whole: 5, left: 1, right: 3, gap: 1 },
    };
    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        jest.clearAllMocks();
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });
    afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

    test("runs only on explicit request and labels date scope without claiming campaign coverage", async () => {
        api.get.mockResolvedValue({ data: payload });
        await act(async () => root.render(<SnapchatPeriodDiagnostic {...props} />));
        expect(api.get).not.toHaveBeenCalled();
        expect(container.querySelector("input").value).toBe("2026-08-25");
        await act(async () => container.querySelector("button").click());
        expect(api.get).toHaveBeenCalledWith("/integrations-v2/snapchat-v2/period-diagnostics", expect.objectContaining({
            params: { ad_account_id: "a1", date_from: "2026-08-01", date_to: "2026-09-17", split_on: "2026-08-25" },
        }));
        expect(container.querySelectorAll("tbody tr")).toHaveLength(4);
        expect(container.textContent).toContain("2026-08-24");
        expect(container.textContent).toContain("لا يقيس نسبة الربط بالحملات");
        expect(api.post).not.toHaveBeenCalled();
    });

    test("does not reuse the prior account's pending result", async () => {
        let resolve;
        api.get.mockImplementation(() => new Promise(done => { resolve = done; }));
        await act(async () => root.render(<SnapchatPeriodDiagnostic key="a1" {...props} />));
        await act(async () => container.querySelector("button").click());
        await act(async () => root.render(<SnapchatPeriodDiagnostic key="a2" {...props} accountId="a2" />));
        await act(async () => resolve({ data: payload }));
        expect(container.querySelector("table")).toBeNull();
    });

    test("clears a prior result on failure and hides backend error details", async () => {
        api.get.mockResolvedValueOnce({ data: payload }).mockRejectedValueOnce(new Error("private detail"));
        await act(async () => root.render(<SnapchatPeriodDiagnostic {...props} />));
        await act(async () => container.querySelector("button").click());
        await act(async () => container.querySelector("button").click());
        expect(container.querySelector("table")).toBeNull();
        expect(container.querySelector('[role="alert"]')).not.toBeNull();
        expect(container.textContent).not.toContain("private detail");
    });

    test("does not request an indivisible one-day range", async () => {
        await act(async () => root.render(<SnapchatPeriodDiagnostic {...props} dateTo="2026-08-01" />));
        expect(container.querySelector("button").disabled).toBe(true);
        expect(api.get).not.toHaveBeenCalled();
    });
});
