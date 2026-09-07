import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { AuthProvider, useAuth } from "./AuthContext";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
    },
    formatApiErrorDetail: jest.fn(),
}));

function AuthStateProbe() {
    const {
        user,
        loading,
        authStatus,
        authError,
        retryAuth,
        refreshUser,
    } = useAuth();
    return (
        <div>
            <span data-testid="status">{authStatus}</span>
            <span data-testid="loading">{String(loading)}</span>
            <span data-testid="user">{user ? user.id : String(user)}</span>
            <span data-testid="error">{authError ? "present" : "none"}</span>
            <button type="button" data-testid="retry" onClick={retryAuth}>
                retry
            </button>
            <button
                type="button"
                data-testid="refresh-user"
                onClick={() => refreshUser().catch(() => {})}
            >
                refresh user
            </button>
        </div>
    );
}

async function flushPromises() {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
}

describe("AuthProvider bootstrap", () => {
    let container;
    let root;

    beforeEach(() => {
        globalThis.IS_REACT_ACT_ENVIRONMENT = true;
        jest.useFakeTimers();
        jest.setSystemTime(new Date("2026-08-17T16:00:00Z"));
        api.get.mockReset();
        api.post.mockReset();
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
        jest.useRealTimers();
        delete globalThis.IS_REACT_ACT_ENVIRONMENT;
    });

    async function renderProvider() {
        await act(async () => {
            root.render(
                <AuthProvider>
                    <AuthStateProbe />
                </AuthProvider>,
            );
            await flushPromises();
        });
    }

    test("hydrates an authenticated cookie session", async () => {
        api.get.mockResolvedValue({ data: { id: "owner-1", is_owner: true } });

        await renderProvider();

        expect(container.querySelector('[data-testid="status"]').textContent)
            .toBe("authenticated");
        expect(container.querySelector('[data-testid="loading"]').textContent)
            .toBe("false");
        expect(container.querySelector('[data-testid="user"]').textContent)
            .toBe("owner-1");
        expect(api.get).toHaveBeenCalledWith("/auth/me", expect.objectContaining({
            signal: expect.any(AbortSignal),
            timeout: 6_000,
        }));
    });

    test("treats only a definitive 401 as anonymous", async () => {
        api.get.mockRejectedValue(Object.assign(new Error("unauthorized"), {
            response: { status: 401, headers: {} },
        }));

        await renderProvider();

        expect(container.querySelector('[data-testid="status"]').textContent)
            .toBe("anonymous");
        expect(container.querySelector('[data-testid="user"]').textContent)
            .toBe("false");
        expect(api.get).toHaveBeenCalledTimes(1);
    });

    test("preserves the cookie session on 520 and recovers in place", async () => {
        const unavailable = Object.assign(new Error("origin unavailable"), {
            response: {
                status: 520,
                headers: { "retry-after": "60" },
            },
        });
        api.get.mockRejectedValueOnce(unavailable);

        await renderProvider();

        expect(container.querySelector('[data-testid="status"]').textContent)
            .toBe("unavailable");
        expect(container.querySelector('[data-testid="user"]').textContent)
            .toBe("null");
        expect(container.querySelector('[data-testid="error"]').textContent)
            .toBe("present");

        api.get.mockResolvedValueOnce({ data: { id: "owner-1", is_owner: true } });
        await act(async () => {
            container.querySelector('[data-testid="retry"]').click();
            await flushPromises();
        });

        expect(container.querySelector('[data-testid="status"]').textContent)
            .toBe("authenticated");
        expect(container.querySelector('[data-testid="user"]').textContent)
            .toBe("owner-1");
        expect(api.get).toHaveBeenCalledTimes(2);
    });

    test("coalesces rapid retry clicks into one fresh auth probe", async () => {
        const unavailable = Object.assign(new Error("temporarily unavailable"), {
            response: {
                status: 503,
                headers: { "retry-after": "60" },
            },
        });
        api.get.mockRejectedValueOnce(unavailable);
        await renderProvider();

        let resolveRetry;
        api.get.mockImplementationOnce(() => new Promise((resolve) => {
            resolveRetry = resolve;
        }));
        await act(async () => {
            const retry = container.querySelector('[data-testid="retry"]');
            retry.click();
            retry.click();
            await flushPromises();
        });

        expect(api.get).toHaveBeenCalledTimes(2);
        expect(container.querySelector('[data-testid="status"]').textContent)
            .toBe("checking");

        await act(async () => {
            resolveRetry({ data: { id: "owner-1", is_owner: true } });
            await flushPromises();
        });
        expect(container.querySelector('[data-testid="status"]').textContent)
            .toBe("authenticated");
    });

    test("fails closed when a later user refresh is forbidden", async () => {
        api.get.mockResolvedValueOnce({
            data: { id: "owner-1", is_owner: true },
        });
        await renderProvider();

        api.get.mockRejectedValueOnce(Object.assign(new Error("forbidden"), {
            response: { status: 403, headers: {} },
        }));
        await act(async () => {
            container.querySelector('[data-testid="refresh-user"]').click();
            await flushPromises();
        });

        expect(container.querySelector('[data-testid="status"]').textContent)
            .toBe("unavailable");
        expect(container.querySelector('[data-testid="user"]').textContent)
            .toBe("owner-1");
        expect(container.querySelector('[data-testid="error"]').textContent)
            .toBe("present");
    });
});
