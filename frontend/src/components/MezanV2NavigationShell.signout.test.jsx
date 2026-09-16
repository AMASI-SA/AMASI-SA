import { act } from "react";
import { createRoot } from "react-dom/client";
import MezanV2NavigationShell from "./MezanV2NavigationShellLegacy";

let mockAuth;
jest.mock("../context/AuthContext", () => ({
    useOptionalAuth: () => mockAuth,
}));
jest.mock("react-router-dom", () => ({
    Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}));

let container;
let root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});
afterEach(() => {
    act(() => root.unmount());
    container.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
});

function renderShell(onOpenAll = jest.fn()) {
    act(() => root.render(
        <MezanV2NavigationShell
            location={{ pathname: "/integrations-v2", search: "?provider=meta_ads" }}
            onOpenAll={onOpenAll}
        />,
    ));
}

test("reviewer can sign out without opening administrative navigation", async () => {
    const logout = jest.fn().mockResolvedValue(undefined);
    const onOpenAll = jest.fn();
    mockAuth = { user: { role: "meta_reviewer" }, logout };
    renderShell(onOpenAll);

    const signout = container.querySelector('button[aria-label="تسجيل الخروج"]');
    expect(signout).not.toBeNull();
    expect(signout.disabled).toBe(false);
    await act(async () => signout.click());
    expect(logout).toHaveBeenCalledTimes(1);
    expect(onOpenAll).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mezan-v2-open-all"]').disabled).toBe(true);
    expect(container.querySelector('a[href="/employees-v2"]')).toBeNull();
    expect(container.querySelector('a[href="/dashboard-advanced"]')).toBeNull();
});

test("owner keeps the established full menu and logout location", () => {
    mockAuth = { user: { role: "owner", is_owner: true }, logout: jest.fn() };
    const onOpenAll = jest.fn();
    renderShell(onOpenAll);
    const all = container.querySelector('[data-testid="mezan-v2-open-all"]');
    expect(all.disabled).toBe(false);
    act(() => all.click());
    expect(onOpenAll).toHaveBeenCalledTimes(1);
    expect(container.querySelector('button[aria-label="تسجيل الخروج"]')).toBeNull();
});
