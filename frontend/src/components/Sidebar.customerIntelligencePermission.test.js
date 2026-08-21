jest.mock("react-router-dom", () => ({
    NavLink: () => null,
    useLocation: () => ({ pathname: "/" }),
}), { virtual: true });
jest.mock("./SidebarVisibilityDialog", () => () => null);
jest.mock("../lib/sidebarVisibility", () => ({
    loadHiddenPages: () => new Set(),
    SIDEBAR_VISIBILITY_EVENT: "test-sidebar-visibility",
}));

import { sidebarSectionsForUser } from "./Sidebar";

const PERMISSION = "customer_intelligence.inbox.read";

function customerIntelligenceLinks(user) {
    return sidebarSectionsForUser(user)
        .flatMap((section) => section.items || [])
        .filter((item) => item.to.startsWith("/customer-intelligence"));
}

function orderTrackingLinks(user) {
    return sidebarSectionsForUser(user)
        .flatMap((section) => section.items || [])
        .filter((item) => item.to === "/order-tracking-notes");
}

test("customer intelligence navigation is visible once for owner or permitted employee", () => {
    expect(customerIntelligenceLinks({ is_owner: true, permissions: [] })).toHaveLength(1);
    expect(customerIntelligenceLinks({
        is_owner: false,
        permissions: [PERMISSION],
    })).toHaveLength(1);
});

test("customer-service navigation opens the conversations tab directly", () => {
    const [link] = customerIntelligenceLinks({
        is_owner: false,
        permissions: [PERMISSION],
    });

    expect(link.to).toBe("/customer-intelligence?tab=conversations");
});

test("customer-service section exposes one unified order tracking and notes page", () => {
    const links = orderTrackingLinks({
        is_owner: false,
        permissions: [PERMISSION],
    });

    expect(links).toHaveLength(1);
    expect(links[0].label).toBe("تتبع الطلب وملاحظاته");
});

test("customer intelligence navigation stays hidden from unrelated employees", () => {
    expect(customerIntelligenceLinks({
        is_owner: false,
        permissions: ["orders.view"],
    })).toEqual([]);
});

test("permission exposes only the customer-service section, not owner Mezan OS", () => {
    const sections = sidebarSectionsForUser({
        is_owner: false,
        permissions: [PERMISSION],
    });

    expect(sections.find((section) => section.id === "customer_service")).toBeDefined();
    expect(sections.find((section) => section.id === "mezan_os")).toBeUndefined();
});
