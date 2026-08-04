import { enhanceSnapchatDeliveryStates } from "./marketingSnapchatDeliveryStateEnhancer";

function tableHtml(deliveryText, detail = "قد تكون في مرحلة التعلم") {
  return `
    <section data-testid="campaign-manager-table">
      <table>
        <thead>
          <tr>
            <th></th>
            <th>اسم الحملة</th>
            <th>الحالة</th>
            <th>حالة التسليم</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td></td>
            <td>حملة اختبار</td>
            <td><span>نشطة</span></td>
            <td>
              <div>
                <span class="mt-1 h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-emerald-100"></span>
                <div>
                  <div>${deliveryText}</div>
                  <div class="mt-0.5 text-[10px] font-semibold">${detail}</div>
                </div>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  `;
}

describe("Snapchat delivery state enhancer", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/ads-manager?provider=snapchat");
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  test("keeps the active status while showing a blocked delivery dot", () => {
    document.body.innerHTML = tableHtml("لا تسليم — خارج الميزانية اليومية");

    expect(enhanceSnapchatDeliveryStates(document)).toBe(true);

    const row = document.querySelector("tbody tr");
    const cells = row.querySelectorAll(":scope > td");
    expect(cells[2].textContent).toContain("نشطة");
    expect(cells[3].dataset.mezanSnapchatDeliveryState).toBe("blocked");
    expect(cells[3].querySelector("span.rounded-full").classList)
      .toContain("bg-amber-500");
    expect(cells[3].querySelector("span.rounded-full").classList)
      .not.toContain("bg-emerald-500");
    expect(cells[3].textContent).not.toContain("مرحلة التعلم");
  });

  test("shows no delivery when every ad squad is paused", () => {
    document.body.innerHTML = tableHtml(
      "لا تسليم — لا توجد مجموعة إعلانية نشطة",
    );

    enhanceSnapchatDeliveryStates(document);

    const deliveryCell = document.querySelectorAll("tbody td")[3];
    expect(deliveryCell.dataset.mezanSnapchatDeliveryState).toBe("blocked");
    expect(deliveryCell.textContent).toContain("لا توجد مجموعة إعلانية نشطة");
  });

  test("leaves a delivering campaign green", () => {
    document.body.innerHTML = tableHtml("يتم التسليم");

    enhanceSnapchatDeliveryStates(document);

    const deliveryCell = document.querySelectorAll("tbody td")[3];
    expect(deliveryCell.dataset.mezanSnapchatDeliveryState).toBe("delivering");
    expect(deliveryCell.querySelector("span.rounded-full").classList)
      .toContain("bg-emerald-500");
  });

  test("does not run outside the explicit Snapchat Ads Manager route", () => {
    window.history.pushState({}, "", "/products-v2");
    document.body.innerHTML = tableHtml("لا تسليم — خارج الميزانية اليومية");

    expect(enhanceSnapchatDeliveryStates(document)).toBe(false);
    expect(document.querySelector("tbody td:last-child").dataset.mezanSnapchatDeliveryState)
      .toBeUndefined();
  });
});
