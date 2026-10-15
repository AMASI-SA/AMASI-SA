const fs = require("fs");
const path = require("path");

const componentsDir = __dirname;
const placementSource = fs.readFileSync(
    path.join(componentsDir, "DashboardSnapchatAccountsPlacement.jsx"),
    "utf8",
);
const cardsSource = fs.readFileSync(
    path.join(componentsDir, "SnapchatStandaloneAccountCards.jsx"),
    "utf8",
);

test("retired dashboard placement mounts no Snapchat portal or polling side effect", () => {
    expect(placementSource).toContain("return null;");
    expect(placementSource).not.toContain("createPortal");
    expect(placementSource).not.toContain("MutationObserver");
    expect(placementSource).not.toContain("setInterval(");
    expect(placementSource).not.toContain("SnapchatStandaloneAccountCards");
});

test("standalone cards remain isolated for their dedicated supported surfaces", () => {
    expect(cardsSource).toContain("لا يوجد دمج بين الحسابات");
    expect(cardsSource).toContain('periodKey="today"');
    expect(cardsSource).toContain('periodKey="month"');
    expect(cardsSource).toContain("account?.[periodKey]");
    expect(cardsSource).not.toContain("spend_share_pct");
    expect(cardsSource).not.toContain("prorated");
});
