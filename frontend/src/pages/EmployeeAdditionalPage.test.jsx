import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import EmployeeAdditionalPage from "./EmployeeAdditionalPage";

jest.mock("react-router-dom", () => ({Link: ({to, children}) => <a href={to}>{children}</a>}));
jest.mock("../lib/api", () => ({get: jest.fn()}));

test("snap diagnostics are explicit and extra pages only open returned links or already granted rows", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const element = document.createElement("div");
    const root = createRoot(element);
    api.get.mockResolvedValueOnce({data:{rows:[], columns:[], description:"عملية متاحة", links:[{label:"السجل", to:"/financial-movements"}]}})
        .mockResolvedValueOnce({data:{rows:[{name:"مورد مأذون"}], columns:[{key:"name",label:"المورد"}], has_more:true}})
        .mockResolvedValueOnce({data:{rows:[], columns:[], has_more:false}});
    const render = pageId => act(async () => root.render(<EmployeeAdditionalPage key={pageId} page={{id:pageId, label:"صفحة"}}/>));
    try {
        await render("extra.snapchat_period");
        expect(element.textContent).toContain("فحص اتساق الفترة");
        expect(api.get).not.toHaveBeenCalled();
        await render("extra.financial_entry");
        expect(element.querySelector("a").getAttribute("href")).toBe("/financial-movements");
        expect(element.querySelector("form")).toBeNull();
        await render("extra.inventory_purchases");
        expect(element.textContent).toContain("مورد مأذون");
        await act(async () => [...element.querySelectorAll("button")].find(item => item.textContent === "التالي").click());
        expect(api.get.mock.calls.at(-1)[1].params.page).toBe(2);
    } finally {
        await act(async () => root.unmount());
    }
});
