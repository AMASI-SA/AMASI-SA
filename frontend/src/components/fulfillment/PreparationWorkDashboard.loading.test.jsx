import React, { act } from "react";
import { createRoot } from "react-dom/client";
import PreparationWorkDashboard from "./PreparationWorkDashboard";
import { getMyPreparationWork, getPreparationManagerSummary } from "../../services/preparationWorkService";

jest.mock("../../services/preparationWorkService", () => ({
    getMyPreparationWork: jest.fn(), getPreparationManagerSummary: jest.fn(),
}));
jest.mock("../../services/fulfillmentExperiment", () => ({ createFulfillmentHold: jest.fn() }));
jest.mock("./PreparationSupplierDispatchWorkspace", () => ({ onDataChanged }) => (
    <button onClick={onDataChanged}>refresh visible products</button>
));

let host, root;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    getMyPreparationWork.mockResolvedValue({ files: [], pieces: [], summary: {} });
    getPreparationManagerSummary.mockResolvedValue({ items: [] });
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});

test("standalone products does not read hidden work or scan the employee summary", async () => {
    await act(async () => root.render(<PreparationWorkDashboard initialView="my-products" standalone />));
    await act(async () => host.querySelector("button").click());
    expect(getMyPreparationWork).not.toHaveBeenCalled();
    expect(getPreparationManagerSummary).not.toHaveBeenCalled();
});

test("details loads work and reads the employee summary only when opened", async () => {
    await act(async () => root.render(<PreparationWorkDashboard />));
    expect(getMyPreparationWork).toHaveBeenCalledTimes(1);
    expect(getPreparationManagerSummary).not.toHaveBeenCalled();
    const employees = [...host.querySelectorAll("button")].find(node => node.textContent.includes("إدارة منتجات الموظفين"));
    await act(async () => employees.click());
    expect(getPreparationManagerSummary).toHaveBeenCalledTimes(1);
    expect(getMyPreparationWork).toHaveBeenCalledTimes(1);
});

test("a denied manager view returns to the working details view", async () => {
    getPreparationManagerSummary.mockRejectedValue(Object.assign(new Error("denied"), { forbidden: true }));
    await act(async () => root.render(<PreparationWorkDashboard initialView="employees" />));
    expect(getPreparationManagerSummary).toHaveBeenCalledTimes(1);
    expect(getMyPreparationWork).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[data-testid="preparation-my-work-view"]')).not.toBeNull();
});
