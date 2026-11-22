import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

let mockCurrentUser;
let mockCurrentLoading = false;

// Use the real router with its CommonJS DOM entry so App-route test ordering
// cannot change this installed module into a different virtual mock identity.
jest.mock("react-router/dom", () => {
    const { TextEncoder, TextDecoder } = require("util");
    if (!global.TextEncoder) global.TextEncoder = TextEncoder;
    if (!global.TextDecoder) global.TextDecoder = TextDecoder;
    const path = require("path");
    const packagePath = require.resolve("react-router/package.json");
    let target = require(packagePath).exports["./dom"];
    while (target && typeof target === "object") {
        target = target.require || target.node || target.default;
    }
    if (typeof target !== "string") throw new Error("react-router/dom CommonJS export missing");
    return require(path.resolve(path.dirname(packagePath), target));
}, { virtual: true });

jest.mock("../context/AuthContext", () => ({
    useAuth: () => ({ user: mockCurrentUser, loading: mockCurrentLoading }),
}));

import PermissionRoute, { userHasPermission } from "./PermissionRoute";

const PERMISSION = "customer_intelligence.inbox.read";

test("owner and explicitly permitted employee can open customer intelligence", () => {
    expect(userHasPermission({ is_owner: true, permissions: [] }, PERMISSION)).toBe(true);
    expect(userHasPermission({
        is_owner: false,
        permissions: [PERMISSION],
    }, PERMISSION)).toBe(true);
});

test("an unrelated employee permission cannot open the inbox", () => {
    mockCurrentUser = { is_owner: false, permissions: ["orders.view"] };
    const markup = renderToStaticMarkup(
        <MemoryRouter initialEntries={["/customer-intelligence"]}>
            <PermissionRoute permission={PERMISSION}>
                <div data-testid="protected-inbox">inbox</div>
            </PermissionRoute>
        </MemoryRouter>,
    );

    expect(markup).toContain('data-testid="permission-route-denied"');
    expect(markup).not.toContain('data-testid="protected-inbox"');
});

test("permitted employee receives the protected content", () => {
    mockCurrentUser = { is_owner: false, permissions: [PERMISSION] };
    const markup = renderToStaticMarkup(
        <MemoryRouter initialEntries={["/customer-intelligence"]}>
            <PermissionRoute permission={PERMISSION}>
                <div data-testid="protected-inbox">inbox</div>
            </PermissionRoute>
        </MemoryRouter>,
    );

    expect(markup).toContain('data-testid="protected-inbox"');
    expect(markup).not.toContain('data-testid="permission-route-denied"');
});
