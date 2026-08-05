jest.mock("./services/mezanProductsV2", () => ({
  listWorkspaceProducts: jest.fn(),
  syncRecentProductsV2: jest.fn(),
}));

jest.mock("./marketingCampaignResultSource", () => ({
  rememberSnapchatAdsManagerReturnRange: jest.fn((value) => value),
}));

import {
  CAMPAIGN_PRODUCT_NAVIGATION_POLICY,
  buildProfitabilityProductCostHref,
  currentSnapchatAdsManagerRange,
  resolveProductFromWorkspace,
} from "./campaignProfitabilityProductNavigation";

test("builds the Product V2 cost-focus deep link", () => {
  const href = buildProfitabilityProductCostHref({
    sku: "AMS13039",
    name: "مشط رجالي",
  });
  expect(href).toContain("/products-v2?");
  expect(href).toContain("focus=cost");
  expect(href).toContain("lookup_sku=AMS13039");
});

test("captures the selected Snapchat date range without DOM row enhancement", () => {
  document.body.innerHTML = `
    <main data-testid="marketing-platform-workspace" data-snapchat-selected-account="account-1">
      <form><input type="date" value="2026-08-01" /><input type="date" value="2026-08-05" /></form>
    </main>
  `;
  const locationLike = {
    pathname: "/ads-manager",
    search: "?provider=snapchat",
  };
  expect(currentSnapchatAdsManagerRange(document, locationLike)).toEqual({
    dateFrom: "2026-08-01",
    dateTo: "2026-08-05",
    accountId: "account-1",
  });
});

test("resolves by variant SKU before name", () => {
  const product = resolveProductFromWorkspace([
    { id: "p1", name: "منتج آخر", variants: [{ sku: "AMS13039" }] },
    { id: "p2", name: "مشط رجالي", sku: "OTHER" },
  ], { sku: "AMS13039", name: "مشط رجالي" });
  expect(product.id).toBe("p1");
});

test("declares the native navigation boundary", () => {
  expect(CAMPAIGN_PRODUCT_NAVIGATION_POLICY).toEqual({
    rendered_by_react: true,
    mutation_observer_used: false,
    preserves_snapchat_date_range: true,
    opens_cost_focus: true,
    provider_writes_allowed: false,
  });
});
