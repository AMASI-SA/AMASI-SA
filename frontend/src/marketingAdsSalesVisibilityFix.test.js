import { enhanceMarketingAdsTables } from "./marketingAdsTableUXEnhancer";
import { repairAdsSalesVisibility } from "./marketingAdsSalesVisibilityFix";

function campaignTable() {
  return `
    <section data-testid="campaign-manager-table">
      <table>
        <thead><tr>
          <th>تحديد</th><th>اسم الحملة</th><th>الحالة</th><th>مبيعات سلة</th><th>تكلفة المنتجات</th><th>المبلغ المصروف</th>
        </tr></thead>
        <tbody><tr>
          <td></td><td>حملة غلاف جوال</td><td>نشطة</td><td><div>259.49 ر.س</div></td><td>56.00 ر.س</td><td><div>165.69 ر.س</div></td>
        </tr></tbody>
        <tfoot><tr>
          <td></td><td>إجمالي الفترة</td><td></td><td>259.49 ر.س</td><td>56.00 ر.س</td><td>165.69 ر.س</td>
        </tr></tfoot>
      </table>
    </section>
  `;
}

function adSquadTable() {
  return `
    <section data-testid="ad-squad-manager-table">
      <table>
        <thead><tr>
          <th>تحديد</th><th>اسم المجموعة الإعلانية</th><th>الحملة</th><th>الحالة</th><th>المبلغ المصروف</th><th>ROAS</th><th>المبيعات</th>
        </tr></thead>
        <tbody><tr>
          <td></td><td>مجموعة الرياض</td><td>حملة وطنية</td><td>نشطة</td><td>44.18 ر.س</td><td>1.30×</td><td><div>131.00 ر.س</div></td>
        </tr></tbody>
      </table>
    </section>
  `;
}

describe("Ads sales visibility repair", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  test("keeps both spend and Salla sales labels and values visible", () => {
    document.body.innerHTML = campaignTable();
    enhanceMarketingAdsTables(document);

    expect(repairAdsSalesVisibility(document)).toBe(3);

    const cells = document.querySelectorAll('[data-mezan-sales-with-spend="true"]');
    expect(cells).toHaveLength(3);
    expect(cells[0].dataset.mezanSpendDisplay).toBe("المبلغ المصروف");
    expect(cells[0].dataset.mezanSalesDisplay).toBe("مبيعات سلة");
    expect(cells[1].dataset.mezanSpendDisplay).toContain("165.69");
    expect(cells[1].dataset.mezanSalesDisplay).toContain("259.49");
    expect(cells[2].dataset.mezanSpendDisplay).toContain("165.69");
    expect(cells[2].dataset.mezanSalesDisplay).toContain("259.49");
    cells.forEach((cell) => {
      expect(cell.getAttribute("data-mezan-sales-visibility-fixed")).toBe("true");
    });
  });

  test("repairs Ad Squad spend and sales without changing their amounts", () => {
    document.body.innerHTML = adSquadTable();
    enhanceMarketingAdsTables(document);
    expect(repairAdsSalesVisibility(document)).toBe(2);

    const cells = document.querySelectorAll('[data-mezan-sales-with-spend="true"]');
    expect(cells[0].dataset.mezanSalesDisplay).toBe("المبيعات");
    expect(cells[1].dataset.mezanSpendDisplay).toContain("44.18");
    expect(cells[1].dataset.mezanSalesDisplay).toContain("131.00");
  });

  test("does nothing when spend and sales were never folded", () => {
    document.body.innerHTML = `
      <table><thead><tr><th>المبلغ المصروف</th><th>المبيعات</th></tr></thead></table>
    `;
    expect(repairAdsSalesVisibility(document)).toBe(0);
  });
});
