import { act } from "react";
import { createRoot } from "react-dom/client";
import PendingLabelPrints from "./PendingLabelPrints";
import { openCurrentCarrierLabel, pendingLabelPrint } from "../lib/openCurrentCarrierLabel";
import { refreshCompletedOrderCarrierLabel } from "../services/fulfillmentV2";
import { useAuth } from "../context/AuthContext";
jest.mock("../context/AuthContext", () => ({ useAuth: jest.fn() }));
jest.mock("../services/fulfillmentV2", () => ({ refreshCompletedOrderCarrierLabel: jest.fn() }));
jest.mock("../services/orderEngine", () => ({ verifyShippingLabel: jest.fn() }));
jest.mock("../lib/storeCourierLabelPrint", () => ({ printStoreCourierLabel: jest.fn() }));

let root, host, popup;
beforeEach(() => {
    window.localStorage.clear();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div"); root = createRoot(host);
    useAuth.mockReturnValue({ user: { id: "actor", is_owner: true }, loading: false, authStatus: "authenticated" });
    popup = { close: jest.fn(), location: { replace: jest.fn() } };
    jest.spyOn(window, "open").mockReturnValue(popup);
});
afterEach(() => { act(() => root.unmount()); pendingLabelPrint.setScope(null); jest.restoreAllMocks(); });

test("app-level status survives screen navigation and explicit cancellation clears intent", async () => {
    refreshCompletedOrderCarrierLabel.mockResolvedValue({ ready: false });
    await act(async () => root.render(<><PendingLabelPrints /><main>screen one</main></>));
    await act(async () => openCurrentCarrierLabel("42"));
    await act(async () => root.render(<><PendingLabelPrints /><main>screen two</main></>));
    expect(host.textContent).toContain("42");
    expect(host.textContent).toContain("إلغاء طلب الطباعة");
    act(() => host.querySelector("aside button").click());
    expect(pendingLabelPrint.snapshot()).toEqual([]);
    expect(popup.close).toHaveBeenCalled();
});

test.each(["logout", "permission"])("%s suppresses late opening at the app boundary", async reason => {
    let resolve;
    refreshCompletedOrderCarrierLabel.mockReturnValue(new Promise(done => { resolve = done; }));
    await act(async () => root.render(<PendingLabelPrints />));
    await act(async () => openCurrentCarrierLabel("42"));
    if (reason === "logout") act(() => window.dispatchEvent(new Event("mezan:auth-ending")));
    else {
        useAuth.mockReturnValue({ user: { id: "actor", permissions: [] }, loading: false });
        act(() => root.render(<PendingLabelPrints />));
    }
    await act(async () => resolve({ ready: true, label_url: "https://labels.test/current.pdf" }));
    expect(popup.location.replace).not.toHaveBeenCalled();
    expect(popup.close).toHaveBeenCalled();
});

test("blocked popup exposes a ready open action which rechecks the current label", async () => {
    window.open.mockReturnValueOnce(null);
    refreshCompletedOrderCarrierLabel.mockResolvedValue({ ready: true, label_url: "https://labels.test/current.pdf" });
    await act(async () => root.render(<PendingLabelPrints />));
    await act(async () => openCurrentCarrierLabel("42"));
    expect(host.textContent).toContain("فتح ملف الطباعة");
    await act(async () => host.querySelector("aside button").click());
    expect(refreshCompletedOrderCarrierLabel).toHaveBeenCalledTimes(2);
    expect(popup.location.replace).toHaveBeenCalledWith("https://labels.test/current.pdf");
});
