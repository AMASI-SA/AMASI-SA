import {
  buildProfitabilityProductCostHref,
  enhanceProfitabilityProductRows,
  productWorkspaceId,
  resolveProductFromWorkspace,
} from "./campaignProfitabilityProductCostLink";

describe("Campaign profitability product cost links", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
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

  test("adds an Add Cost link to a missing-cost product row", () => {
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
    expect(link).not.toBeNull();
    expect(link.textContent).toBe("فتح المنتج وإضافة التكلفة");
    expect(link.getAttribute("href")).toContain("lookup_sku=AMS10060");
    expect(enhanceProfitabilityProductRows(document)).toBe(0);
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
