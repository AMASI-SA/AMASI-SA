import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { getAccountingAccess } from "./services/accountingModule";
import MezanV2NavigationShell from "./components/MezanV2NavigationShell";
jest.mock("react-router-dom", () => ({ Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
jest.mock("./context/AuthContext", () => ({ useOptionalAuth: () => ({ user: { id: "owner", role: "owner" } }) }));
jest.mock("./services/accountingModule", () => ({ getAccountingAccess: jest.fn().mockResolvedValue({ is_owner: true, user_id: "owner", permissions: [] }) }));
const fs = require("fs");
const path = require("path");

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

test("Mezan 2 navigation and order search share one responsive header", () => {
    const layout = read("src/components/Layout.jsx");
    const navigation = read("src/components/MezanV2NavigationShellLegacy.jsx");

    expect(layout).toContain('data-testid="mezan-v2-unified-header"');
    expect(layout).toContain('searchForm={<GlobalSearch compact />}');
    expect(layout).toContain('notificationControl={<NotificationBell />}');
    expect(layout).toMatch(/\{!isMezanV2 && \(\s*<header/);
    expect(read("src/components/MezanV2NavigationShell.jsx")).toContain("return <LegacyMezanV2NavigationShell {...props} />;");
    expect(layout).toContain('DashboardAnalyticsPlacement active={showsDashboardAnalytics}');
    expect(layout).toContain('DashboardSnapchatAccountsPlacement active={showsDashboardAnalytics}');

    expect(navigation).toContain('data-testid="mezan-v2-unified-primary-row"');
    expect(navigation).toContain('data-testid="mezan-v2-primary-scroll"');
    expect(navigation).toContain('data-testid="mezan-v2-search-trigger"');
    expect(navigation).toContain('data-testid="mezan-v2-search-dropdown"');
    expect(navigation).toContain('flex-nowrap');
    expect(navigation).toContain('whitespace-nowrap');
    expect(navigation).toContain('label: "الذكاء الاصطناعي"');
    expect(navigation).not.toContain('flex min-h-16 flex-wrap items-center');
});

test("the unified Mezan 2 header stays visible while the page scrolls", () => {
    const layout = read("src/components/Layout.jsx");
    const headerStart = layout.indexOf('data-testid="mezan-v2-unified-header"');
    expect(headerStart).toBeGreaterThan(-1);

    const headerContext = layout.slice(Math.max(0, headerStart - 280), headerStart + 100);
    expect(headerContext).toContain('className="sticky top-0 z-40');
    expect(headerContext).toContain('backdrop-blur');
    expect(headerContext).not.toContain('fixed top-0');
});

test("the unified header uses the established Mezan green identity", () => {
    const navigation = read("src/components/MezanV2NavigationShellLegacy.jsx");

    expect(navigation).toContain('border-emerald-950 bg-brand shadow-xl');
    expect(navigation).toContain('border-t border-white/10 bg-[#0B4938]');
    expect(navigation).toContain('data-navigation-source={openSection ? "opened" : "active"}');
    expect(navigation).not.toContain('top-[calc(100%+0.6rem)]');
    expect(navigation).not.toContain('border-y border-slate-800 bg-slate-950 shadow-xl');
});

test("the search field stays collapsed until its header icon is activated", () => {
    const navigation = read("src/components/MezanV2NavigationShellLegacy.jsx");

    expect(navigation).toContain('const [searchOpen, setSearchOpen] = useState(false);');
    expect(navigation).toContain('aria-expanded={searchOpen}');
    expect(navigation).toContain('{searchOpen && safeSearchForm && <div');
    expect(navigation).toContain('setSearchOpen((value) => !value);');
});

test("mobile and narrow desktop widths keep primary labels on one line", () => {
    const navigation = read("src/components/MezanV2NavigationShellLegacy.jsx");

    expect(navigation).toContain('overflow-x-auto overscroll-x-contain');
    expect(navigation).toContain('text-[11px]');
    expect(navigation).toContain('2xl:text-sm');
    expect(navigation).toContain('<span className="whitespace-nowrap">{section.label}</span>');
});

 test("actual delegated header toggles search, retains controls and closes on outside click", async () => {
    getAccountingAccess.mockResolvedValue({ is_owner: true, user_id: "owner", permissions: [] });
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    try {
        await act(async () => root.render(<MezanV2NavigationShell location={{ pathname: "/dashboard-advanced", search: "" }}
            searchForm={<form data-testid="actual-search" />} notificationControl={<button>Notifications</button>} />));
        const trigger = container.querySelector('[data-testid="mezan-v2-search-trigger"]');
        expect(trigger.getAttribute("aria-expanded")).toBe("false");
        expect(container.querySelector('[data-testid="actual-search"]')).toBeNull();
        expect(container.querySelector('[data-testid="mezan-v2-notification-control"]').textContent).toBe("Notifications");
        expect(container.querySelector('[data-testid="mezan-v2-primary-scroll"]').classList.contains("overflow-x-auto")).toBe(true);
        await act(async () => trigger.click());
        expect(trigger.getAttribute("aria-expanded")).toBe("true");
        expect(container.querySelector('[data-testid="actual-search"]')).not.toBeNull();
        await act(async () => document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
        expect(trigger.getAttribute("aria-expanded")).toBe("false");
        expect(container.querySelector('[data-testid="actual-search"]')).toBeNull();
    } finally {
        await act(async () => root.unmount()); container.remove(); globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});
