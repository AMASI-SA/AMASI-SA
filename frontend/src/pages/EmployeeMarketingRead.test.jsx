import { act } from "react";
import { createRoot } from "react-dom/client";
import EmployeeMarketingRead from "./EmployeeMarketingRead";
import api from "../lib/api";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));

test("the grants workspace reads only the selected page and clears records on a new selection", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const host = document.createElement("div");
    const root = createRoot(host);
    api.get.mockResolvedValueOnce({ data: { columns: ["الحساب"], rows: [["الإنماء"]], has_more: false } });
    await act(async () => root.render(<EmployeeMarketingRead page={{ id: "apps.advertising", label: "الحسابات الإعلانية" }} />));
    expect(api.get.mock.calls[0][0]).toBe("/web-page-access-v1/marketing-read/apps.advertising");
    expect(host.textContent).toContain("الإنماء");
    api.get.mockRejectedValueOnce(new Error("not allowed"));
    await act(async () => root.render(<EmployeeMarketingRead page={{ id: "marketing.costs", label: "العمولات" }} />));
    expect(host.textContent).not.toContain("الإنماء");
    expect(host.querySelector("[role=alert]").textContent).toContain("تعذّر تحميل");
    await act(async () => root.unmount());
});
