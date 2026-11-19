import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import EmployeeOperationsReadPage from "./EmployeeOperationsReadPage";

jest.mock("../lib/api", () => ({get: jest.fn()}));

test("reads only the chosen employee page and retrieves the next page and scoped events on demand", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const host = document.createElement("div");
    const root = createRoot(host);
    api.get.mockImplementation((url, { params }) => Promise.resolve({data: url.endsWith("/events")
        ? { items: [{id: "e", event_type: "employee_updated", actor_name: "عرفات"}], has_next: false }
        : { items: [{id: String(params.page), display_name: "اسم " + params.page}], has_next: params.page === 1 }}));
    try {
        await act(async () => root.render(<EmployeeOperationsReadPage pageId="employees.staff"/>));
        expect(api.get).toHaveBeenCalledTimes(1);
        expect(host.textContent).toContain("اسم 1");
        const click = async (text) => act(async () => Array.from(host.querySelectorAll("button")).find(button => button.textContent === text).click());
        await click("التالي");
        expect(host.textContent).toContain("اسم 2");
        await click("سجل الموظف");
        expect(api.get.mock.calls[2][0]).toBe("/web-page-access-v1/employee-tools/employees.staff/2/events");
        expect(host.textContent).toContain("تحديث الموظف");
        expect(host.querySelector("form, input")).toBeNull();
        await act(async () => root.render(<EmployeeOperationsReadPage pageId="finance.collections" />));
        expect(host.textContent).toContain("مراجعة تحصيلات الموصلين");
        expect(host.textContent).not.toContain("عرفات");
    } finally { await act(async () => root.unmount()); }
});
