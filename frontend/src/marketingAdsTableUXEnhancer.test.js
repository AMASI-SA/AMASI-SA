import {
  enhanceMarketingAdsTables,
  getAdsReportLoadState,
  isSnapchatCampaignReportRequest,
  renderAdsReportLoadIndicator,
  setAdsReportLoadState,
} from "./marketingAdsTableUXEnhancer";

function campaignTable() {
  return `
    <section data-testid="campaign-manager-table">
      <table>
        <thead><tr>
          <th>تحديد</th><th>اسم الحملة</th><th>الحالة</th><th>مبيعات سلة</th><th>تكلفة المنتجات</th><th>المبلغ المصروف</th>
        </tr></thead>
        <tbody><tr>
          <td></td><td>حملة غلاف جوال</td><td>نشطة</td><td>259.49 ر.س</td><td>56.00 ر.س</td><td>165.69 ر.س</td>
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
          <td></td><td>مجموعة الرياض</td><td>حملة وطنية</td><td>متوقفة</td><td>44.18 ر.س</td><td>1.30×</td><td>131.00 ر.س</td>
        </tr></tbody>
      </table>
    </section>
  `;
}

function adTable() {
  return `
    <section data-testid="ad-manager-table">
      <table>
        <thead><tr>
          <th>الإعلان والإبداع</th><th>الحالة</th><th>ROAS</th><th>المبلغ المصروف</th><th>المبيعات</th>
        </tr></thead>
        <tbody><tr>
          <td>إعلان غلاف جوال</td><td>نشط</td><td>1.20×</td><td>20.00 ر.س</td><td>80.00 ر.س</td>
        </tr></tbody>
      </table>
    </section>
  `;
}

describe("Marketing Ads table UX enhancer", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    setAdsReportLoadState("idle");
  });

  test("recognizes only the Snapchat campaign report endpoint", () => {
    expect(isSnapchatCampaignReportRequest({
      url: "/integrations-v2/snapchat_ads/campaign-report",
    })).toBe(true);
    expect(isSnapchatCampaignReportRequest({
      url: "/integrations-v2/snapchat_ads/ad-squad-report",
    })).toBe(false);
  });

  test("shows a rotating load state then a completed check", () => {
    document.body.innerHTML = `
      <div data-testid="marketing-platform-workspace">
        <form><button type="submit">تطبيق التقرير</button></form>
      </div>
    `;

    setAdsReportLoadState("loading", { detail: "2026-08-01 — 2026-08-04" });
    expect(renderAdsReportLoadIndicator(document)).toBe(true);
    let indicator = document.querySelector('[data-mezan-ads-report-load-indicator]');
    expect(indicator.dataset.state).toBe("loading");
    expect(indicator.querySelector(".mezan-ads-report-spinner")).not.toBeNull();
    expect(indicator.textContent).toContain("جاري تحميل الفترة");

    setAdsReportLoadState("success", { detail: "2026-08-01 — 2026-08-04" });
    renderAdsReportLoadIndicator(document);
    indicator = document.querySelector('[data-mezan-ads-report-load-indicator]');
    expect(indicator.dataset.state).toBe("success");
    expect(indicator.textContent).toContain("✓");
    expect(indicator.textContent).toContain("تم تحميل التقرير");
    expect(getAdsReportLoadState().state).toBe("success");
  });

  test("keeps campaign name and status together and places spend beside Salla sales", () => {
    document.body.innerHTML = campaignTable();
    expect(enhanceMarketingAdsTables(document)).toBe(1);

    const headers = document.querySelectorAll('[data-testid="campaign-manager-table"] thead th');
    const identity = headers[1];
    const status = headers[2];
    const sales = headers[3];
    const spend = headers[5];
    const bodyIdentity = document.querySelectorAll('[data-testid="campaign-manager-table"] tbody td')[1];

    expect(identity.getAttribute("data-mezan-sticky-identity-status")).toBe("true");
    expect(identity.dataset.mezanStatusDisplay).toBe("الحالة");
    expect(identity.style.width).toBe("420px");
    expect(status.getAttribute("data-mezan-folded-status-cell")).toBe("true");
    expect(bodyIdentity.dataset.mezanStatusDisplay).toBe("✓ نشطة");
    expect(sales.getAttribute("data-mezan-sales-with-spend")).toBe("true");
    expect(sales.dataset.mezanSpendDisplay).toBe("المبلغ المصروف");
    expect(spend.getAttribute("data-mezan-folded-spend-cell")).toBe("true");
  });

  test("keeps Ad Squad status beside its name and brings spend next to sales", () => {
    document.body.innerHTML = adSquadTable();
    expect(enhanceMarketingAdsTables(document)).toBe(1);

    const headers = document.querySelectorAll('[data-testid="ad-squad-manager-table"] thead th');
    expect(headers[1].style.width).toBe("420px");
    expect(headers[3].getAttribute("data-mezan-folded-status-cell")).toBe("true");
    expect(headers[6].dataset.mezanSpendDisplay).toBe("المبلغ المصروف");
    expect(headers[4].getAttribute("data-mezan-folded-spend-cell")).toBe("true");
    const bodyIdentity = document.querySelectorAll('[data-testid="ad-squad-manager-table"] tbody td')[1];
    expect(bodyIdentity.dataset.mezanStatusDisplay).toBe("متوقفة");
  });

  test("keeps Ad status attached while preserving already-correct spend and sales order", () => {
    document.body.innerHTML = adTable();
    expect(enhanceMarketingAdsTables(document)).toBe(1);

    const headers = document.querySelectorAll('[data-testid="ad-manager-table"] thead th');
    expect(headers[0].style.width).toBe("450px");
    expect(headers[1].getAttribute("data-mezan-folded-status-cell")).toBe("true");
    expect(headers[3].getAttribute("data-mezan-folded-spend-cell")).toBeNull();
    const bodyIdentity = document.querySelectorAll('[data-testid="ad-manager-table"] tbody td')[0];
    expect(bodyIdentity.dataset.mezanStatusDisplay).toBe("✓ نشط");
  });
});
