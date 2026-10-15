jest.mock("react-router-dom", () => ({
    Navigate: () => null,
    useLocation: () => ({ pathname: "/", search: "" }),
}));

jest.mock("../context/AuthContext", () => ({
    useAuth: () => ({ user: { is_owner: true }, loading: false }),
}));

import { isAccountingWorkspaceLocation } from "./OwnerOnlyRoute";

test("only the financial integrations workspace bypasses the outer owner route", () => {
    expect(isAccountingWorkspaceLocation({
        pathname: "/integrations-v2",
        search: "?workspace=financial&page=home",
    })).toBe(true);
    expect(isAccountingWorkspaceLocation({
        pathname: "/integrations-v2",
        search: "?workspace=accounts",
    })).toBe(false);
    expect(isAccountingWorkspaceLocation({
        pathname: "/integrations-v2",
        search: "",
    })).toBe(false);
    expect(isAccountingWorkspaceLocation({
        pathname: "/settings",
        search: "?workspace=financial",
    })).toBe(false);
});
