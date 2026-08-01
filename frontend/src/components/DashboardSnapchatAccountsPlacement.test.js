const fs = require("fs");
const path = require("path");

const componentsDir = __dirname;
const layoutSource = fs.readFileSync(path.join(componentsDir, "Layout.jsx"), "utf8");
const placementSource = fs.readFileSync(
    path.join(componentsDir, "DashboardSnapchatAccountsPlacement.jsx"),
    "utf8",
);
const cardsSource = fs.readFileSync(
    path.join(componentsDir, "SnapchatStandaloneAccountCards.jsx"),
    "utf8",
);

test("Layout mounts the Snapchat standalone placement on dashboard routes", () => {
    expect(layoutSource).toContain(
        'import DashboardSnapchatAccountsPlacement from "./DashboardSnapchatAccountsPlacement"',
    );
    expect(layoutSource).toContain(
        "<DashboardSnapchatAccountsPlacement active={showsDashboardAnalytics} />",
    );
});

test("merged Snapchat card is hidden only after isolated account data is ready", () => {
    expect(placementSource).toContain(
        "'[data-testid=\"snapchat-ads-section\"]'",
    );
    expect(placementSource).toContain("onReadyChange={setReady}");
    expect(placementSource).toContain(
        'mergedCard.style.setProperty("display", "none", "important")',
    );
    expect(placementSource).toContain(
        'candidate.insertAdjacentElement("afterend", currentHost)',
    );
});

test("standalone cards do not allocate orders or revenue by spend share", () => {
    expect(cardsSource).toContain("لا يوجد دمج بين الحسابات");
    expect(cardsSource).toContain("account.today");
    expect(cardsSource).toContain("account.month");
    expect(cardsSource).not.toContain("spend_share_pct");
    expect(cardsSource).not.toContain("prorated");
});
