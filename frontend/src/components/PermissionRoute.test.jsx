import { renderToStaticMarkup } from "react-dom/server";

let mockCurrentUser;
let mockCurrentLoading = false;

jest.mock("react-router-dom", () => ({
    Navigate: ({ to }) => <div data-testid="navigate">{to}</div>,
    useLocation: () => ({ pathname: "/customer-intelligence" }),
}));

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
        <PermissionRoute permission={PERMISSION}>
            <div data-testid="protected-inbox">inbox</div>
        </PermissionRoute>,
    );

    expect(markup).toContain('data-testid="permission-route-denied"');
    expect(markup).not.toContain('data-testid="protected-inbox"');
});

test("permitted employee receives the protected content", () => {
    mockCurrentUser = { is_owner: false, permissions: [PERMISSION] };
    const markup = renderToStaticMarkup(
        <PermissionRoute permission={PERMISSION}>
            <div data-testid="protected-inbox">inbox</div>
        </PermissionRoute>,
    );

    expect(markup).toContain('data-testid="protected-inbox"');
    expect(markup).not.toContain('data-testid="permission-route-denied"');
});
