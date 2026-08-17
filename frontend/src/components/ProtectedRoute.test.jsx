import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { useAuth } from "../context/AuthContext";
import ProtectedRoute from "./ProtectedRoute";

jest.mock("react-router-dom", () => {
    const ReactModule = require("react");
    return {
        Navigate: ({ to }) => ReactModule.createElement(
            "div",
            { "data-testid": "navigate", "data-to": to },
        ),
        useLocation: () => ({ pathname: "/ads-manager/recommendations" }),
    };
});

jest.mock("../context/AuthContext", () => ({
    useAuth: jest.fn(),
}));

describe("ProtectedRoute auth bootstrap states", () => {
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

    async function renderRoute() {
        await act(async () => {
            root.render(
                <ProtectedRoute>
                    <div data-testid="protected-content">secret</div>
                </ProtectedRoute>,
            );
            await Promise.resolve();
        });
    }

    test("fails closed with an in-place retry instead of an endless spinner", async () => {
        const retryAuth = jest.fn();
        useAuth.mockReturnValue({
            user: null,
            loading: false,
            authStatus: "unavailable",
            retryAuth,
        });

        await renderRoute();

        expect(container.querySelector('[data-testid="auth-unavailable"]'))
            .not.toBeNull();
        expect(container.querySelector('[data-testid="protected-content"]'))
            .toBeNull();
        await act(async () => {
            container.querySelector('[data-testid="auth-retry"]').click();
        });
        expect(retryAuth).toHaveBeenCalledTimes(1);
    });

    test("keeps protected content hidden while the bounded probe is running", async () => {
        useAuth.mockReturnValue({
            user: null,
            loading: true,
            authStatus: "checking",
            retryAuth: jest.fn(),
        });

        await renderRoute();

        expect(container.querySelector('[data-testid="auth-loading"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="protected-content"]')).toBeNull();
    });

    test("renders the requested deep link after authentication", async () => {
        useAuth.mockReturnValue({
            user: { id: "owner-1", role: "owner" },
            loading: false,
            authStatus: "authenticated",
            retryAuth: jest.fn(),
        });

        await renderRoute();

        expect(container.querySelector('[data-testid="protected-content"]'))
            .not.toBeNull();
    });

    test("redirects only a confirmed anonymous session to login", async () => {
        useAuth.mockReturnValue({
            user: false,
            loading: false,
            authStatus: "anonymous",
            retryAuth: jest.fn(),
        });

        await renderRoute();

        expect(container.querySelector('[data-testid="navigate"]')?.dataset.to)
            .toBe("/login");
        expect(container.querySelector('[data-testid="protected-content"]'))
            .toBeNull();
    });
});
