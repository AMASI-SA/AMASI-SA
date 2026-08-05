import {
  persistProfitabilityProductLinks,
  profitabilityProductIdentity,
} from "./campaignProfitabilityProductLinkPersistence";
import { buildProfitabilityProductCostHref } from "./campaignProfitabilityProductCostLink";

jest.mock("./campaignProfitabilityProductCostLink", () => ({
  buildProfitabilityProductCostHref: jest.fn(({ sku = "", name = "" }) => (
    `/products-v2?focus=cost&lookup_sku=${encodeURIComponent(sku)}&lookup_name=${encodeURIComponent(name)}`
  )),
  rememberSnapchatRangeBeforeProductNavigation: jest.fn(),
}));

function dialogRow({ cost = "81.00 ر.س", legacy = false } = {}) {
  return `
    <div data-testid="campaign-profitability-dialog">
      <table>
        <tbody>
          <tr>
            <td>
              <div class="flex items-center gap-3">
                <img src="/product.png" alt="" />
                <div>
                  <div class="font-black text-slate-900" title="غلاف جوال نساء">غلاف جوال نساء</div>
                  <div class="font-mono text-slate-400">AMS13037</div>
                  ${legacy ? '<a data-mezan-profit-product-link="true">فتح المنتج</a>' : ""}
                </div>
              </div>
            </td>
            <td>2</td>
            <td>393.41 ر.س</td>
            <td>${cost}</td>
            <td>165.40 ر.س</td>
            <td>147.01 ر.س</td>
            <td>37.37%</td>
          </tr>
        </tbody>
      </table>
    </div>
  `;
}

beforeEach(() => {
  document.body.innerHTML = "";
  jest.clearAllMocks();
});

test("reads the current React product name and SKU structure", () => {
  document.body.innerHTML = dialogRow();
  const cell = document.querySelector("tbody td");
  expect(profitabilityProductIdentity(cell)).toEqual({
    sku: "AMS13037",
    name: "غلاف جوال نساء",
  });
});

test("shows a persistent Open Product action when React removed the injected link", () => {
  document.body.innerHTML = dialogRow();
  expect(persistProfitabilityProductLinks(document)).toBe(1);

  const cell = document.querySelector("tbody td");
  expect(cell.dataset.mezanProfitProductPersistentLink).toBe("true");
  expect(cell.dataset.mezanProfitProductPersistentLabel).toBe("فتح المنتج");
  expect(cell.dataset.mezanProfitProductPersistentHref).toContain("lookup_sku=AMS13037");
  expect(cell.getAttribute("role")).toBe("link");
  expect(cell.getAttribute("tabindex")).toBe("0");
  expect(buildProfitabilityProductCostHref).toHaveBeenCalledWith({
    sku: "AMS13037",
    name: "غلاف جوال نساء",
  });
});

test("uses the add-cost label when the product cost is missing", () => {
  document.body.innerHTML = dialogRow({ cost: "—" });
  persistProfitabilityProductLinks(document);
  expect(document.querySelector("tbody td").dataset.mezanProfitProductPersistentLabel)
    .toBe("فتح المنتج وإضافة التكلفة");
});

test("does not duplicate the original real link when it is present", () => {
  document.body.innerHTML = dialogRow({ legacy: true });
  expect(persistProfitabilityProductLinks(document)).toBe(0);
  const cell = document.querySelector("tbody td");
  expect(cell.hasAttribute("data-mezan-profit-product-persistent-link")).toBe(false);
});
