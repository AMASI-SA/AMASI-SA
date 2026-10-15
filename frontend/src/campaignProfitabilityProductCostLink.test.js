const mockRememberSnapchatAdsManagerReturnRange = jest.fn();

jest.mock("./marketingCampaignResultSource", () => ({
  rememberSnapchatAdsManagerReturnRange: (...args) => (
    mockRememberSnapchatAdsManagerReturnRange(...args)
  ),
}));

import {
  buildProfitabilityProductCostHref,
  enhanceProfitabilityProductRows,
  productHrefFromTarget,
  productWorkspaceId,
  rememberSnapchatRangeBeforeProductNavigation,
  resolveProductFromWorkspace,
  snapchatAdsManagerRangeFromPage,
} from "./campaignProfitabilityProductCostLink";

describe("Campaign profitability product cost links", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    mockRememberSnapchatAdsManagerReturnRange.mockReset();
    window.history.replaceState({}, "", "/ads-manager?provider=snapchat");
  });

  test("builds a cost-focused Products V2 lookup URL", () => {
    const href = buildProfitabilityProductCostHref({
      sku: "AMS10060",
      name: "تعليقة سيارة بالاسم شكل خيل",
    });
    const url = new URL(href, "https://mezansalla.com");
    expect(url.pathname).toBe("/products-v2");
    expect(url.searchParams.get("focus")).toBe("cost");
    expect(url.searchParams.get("lookup_sku")).toBe("AMS10060");
    expect(url.searchParams.get("lookup_name")).toBe("تعليقة سيارة بالاسم شكل خيل");
  });

  test("makes the missing-cost product cell and CTA open the exact product", () => {
    document.body.innerHTML = `
      <div data-testid="campaign-profitability-dialog">
        <table><tbody><tr>
          <td><div><div class="font-black text-slate-900">تعليقة سيارة بالاسم شكل خيل</div><div class="font-mono">AMS10060</div></div></td>
          <td>1</td><td>125.21 ر.س</td><td>—</td><td>46.60 ر.س</td><td>—</td><td>—</td>
        </tr></tbody></table>
      </div>
    `;

    expect(enhanceProfitabilityProductRows(document)).toBe(1);
    const link = document.querySelector('[data-mezan-profit-product-link="true"]');
    const cell = document.querySelector('[data-mezan-profit-product-cell="true"]');
    const productName = cell.querySelector(".font-black");
    expect(link).not.toBeNull();
    expect(link.textContent).toBe("فتح المنتج وإضافة التكلفة");
    expect(link.getAttribute("href")).toContain("lookup_sku=AMS10060");
    expect(cell.getAttribute("role")).toBe("link");
    expect(cell.getAttribute("tabindex")).toBe("0");
    expect(productHrefFromTarget(productName)).toBe(link.getAttribute("href"));
    expect(enhanceProfitabilityProductRows(document)).toBe(0);
  });

  test("remembers the selected Snapchat dates before opening a product", () => {
    document.body.innerHTML = `
      <div data-testid="marketing-platform-workspace" data-snapchat-selected-account="us-account">
        <form>
          <input type="date" value="2026-07-28" />
          <input type="date" value="2026-08-04" />
        </form>
      </div>
    `;
    mockRememberSnapchatAdsManagerReturnRange.mockReturnValue({
      date_from: "2026-07-28",
      date_to: "2026-08-04",
      account_id: "us-account",
    });

    expect(snapchatAdsManagerRangeFromPage(document, window.location)).toEqual({
      dateFrom: "2026-07-28",
      dateTo: "2026-08-04",
      accountId: "us-account",
    });
    expect(rememberSnapchatRangeBeforeProductNavigation(document, window.location)).toEqual({
      date_from: "2026-07-28",
      date_to: "2026-08-04",
      account_id: "us-account",
    });
    expect(mockRememberSnapchatAdsManagerReturnRange).toHaveBeenCalledWith({
      dateFrom: "2026-07-28",
      dateTo: "2026-08-04",
      accountId: "us-account",
    });
  });

  test("does not save a return range outside Snapchat Ads Manager", () => {
    window.history.replaceState({}, "", "/ads-manager?provider=meta");
    document.body.innerHTML = `
      <div data-testid="marketing-platform-workspace">
        <form><input type="date" value="2026-08-01" /><input type="date" value="2026-08-04" /></form>
      </div>
    `;

    expect(rememberSnapchatRangeBeforeProductNavigation(document, window.location)).toBeNull();
    expect(mockRememberSnapchatAdsManagerReturnRange).not.toHaveBeenCalled();
  });

  test("adds a normal product link when the cost exists", () => {
    document.body.innerHTML = `
      <div data-testid="campaign-profitability-dialog">
        <table><tbody><tr>
          <td><div><div class="font-black text-slate-900">كوب الدبدوب</div><div class="font-mono">AMS13027</div></div></td>
          <td>2</td><td>288.44 ر.س</td><td>30.00 ر.س</td><td>107.36 ر.س</td><td>151.08 ر.س</td><td>52%</td>
        </tr></tbody></table>
      </div>
    `;

    enhanceProfitabilityProductRows(document);
    expect(document.querySelector('[data-mezan-profit-product-link="true"]').textContent)
      .toBe("فتح المنتج");
  });

  test("resolves exact base or variant SKU and returns a Products V2 identity", () => {
    const products = [
      {
        mezan_product_id: "mpv2-1",
        name: "منتج آخر",
        sku: "AMS00001",
        variants: [],
      },
      {
        mezan_product_id: "mpv2-2",
        salla_product_id: "10060",
        name: "تعليقة سيارة بالاسم شكل خيل",
        sku: "",
        variants: [{ sku: "AMS10060" }],
      },
    ];
    const selected = resolveProductFromWorkspace(products, {
      sku: "ams10060",
      name: "تعليقة سيارة بالاسم شكل خيل",
    });
    expect(selected).toBe(products[1]);
    expect(productWorkspaceId(selected)).toBe("mpv2-2");
  });
});
