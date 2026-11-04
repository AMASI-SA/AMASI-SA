import React, { act, lazy } from "react";
import { createRoot } from "react-dom/client";
import PageContentBoundary from "./PageContentBoundary";
import ProtectedRoute from "./ProtectedRoute";
import { useAuth } from "../context/AuthContext";

jest.mock("../context/AuthContext", () => ({ useAuth: jest.fn() }));
jest.mock("../pages/AmasiDeliveryApp", () => () => <div>Courier</div>);
jest.mock("react-router-dom", () => ({
    useLocation: () => ({ pathname: "/orders-v2" }),
    Navigate: () => <div>Login</div>,
}));

describe("deferred protected page loading", () => {
    let container;
    let root;
    beforeEach(() => {
        globalThis.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });
    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
        jest.clearAllMocks();
        delete globalThis.IS_REACT_ACT_ENVIRONMENT;
    });

    test("does not request a protected page before authentication; preserves navigation while loading", async () => {
        let resolvePage;
        const load = jest.fn(() => new Promise(resolve => { resolvePage = resolve; }));
        const Page = lazy(load);
        const render = () => root.render(
            <ProtectedRoute>
                <nav>Navigation</nav>
                <PageContentBoundary><Page /></PageContentBoundary>
            </ProtectedRoute>,
        );
        useAuth.mockReturnValue({ user: false, loading: false, authStatus: "anonymous" });
        await act(async () => render());
        expect(load).not.toHaveBeenCalled();
        expect(container.textContent).toBe("Login");

        useAuth.mockReturnValue({ user: { role: "owner" }, loading: false, authStatus: "authenticated" });
        await act(async () => render());
        const navigation = container.querySelector("nav");
        expect(load).toHaveBeenCalledTimes(1);
        expect(container.querySelector('[role="status"]').textContent).toContain("جاري تحميل الصفحة");

        await act(async () => resolvePage({ default: () => <div>Orders loaded</div> }));
        expect(container.querySelector("nav")).toBe(navigation);
        expect(container.querySelector('[role="status"]')).toBeNull();
        expect(container.textContent).toContain("Orders loaded");
    });
});
