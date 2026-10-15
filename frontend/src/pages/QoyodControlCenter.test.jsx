import { renderToStaticMarkup } from "react-dom/server";

let mockQoyodQuery = "";
jest.mock("react-router-dom", () => ({
  useSearchParams: () => [new URLSearchParams(mockQoyodQuery), () => {}],
}));
jest.mock("./QoyodSettings", () => () => null);
jest.mock("./QoyodReconciliation", () => () => null);
jest.mock("./QoyodInvoiceReview", () => () => (
  <div data-testid="invoice-review-workspace">invoice-review-workspace</div>
));
jest.mock("./QoyodUnsentOrders", () => () => null);

import QoyodControlCenter, {
  QoyodOverview,
  QOYOD_V2_TABS,
} from "./QoyodControlCenter";

describe("QoyodControlCenter", () => {
  test("keeps the final V2 workspaces including read-only Qoyod invoices", () => {
    expect(QOYOD_V2_TABS.map((tab) => tab.id)).toEqual([
      "overview",
      "exceptions",
      "invoices",
      "reconciliation",
      "settings",
    ]);
  });

  test("renders the read-only invoice workspace for tab=invoices", () => {
    mockQoyodQuery = "tab=invoices";
    const html = renderToStaticMarkup(<QoyodControlCenter />);
    expect(html).toContain("invoice-review-workspace");
    mockQoyodQuery = "";
  });

  test("shows a healthy automatic-only status without a manual-send action", () => {
    const html = renderToStaticMarkup(
      <QoyodOverview
        settings={{
          credentials: { configured: true },
          plan_b_auto_send_status: {
            requested: true,
            armed: true,
            worker: { running: true, last_run_at: "2026-08-08T10:00:00Z" },
          },
        }}
        unsent={{
          counts: { "لم يُرسل": 0, "فشل": 0, "مكرر": 1, "أُرسل": 14 },
          queue_counts: {
            ready_to_send: 3,
            quarantined: 2,
            needs_payment_verification: 4,
            in_qoyod: 14,
            retryable_sync: 1,
          },
        }}
        loading={false}
        error=""
        onRefresh={() => {}}
        onOpenTab={() => {}}
      />,
    );

    expect(html).toContain("الإرسال التلقائي إلى قيود يعمل");
    expect(html).toContain("جاهز للإرسال");
    expect(html).toContain("محجور للمراجعة");
    expect(html).toContain("يحتاج تحقق دفع");
    expect(html).toContain("موجود في قيود");
    expect(html).toContain("إعادة مزامنة مجدولة لعدد 1");
    expect(html).toContain("الفاتورة الموجودة في قيود تُصالح ولا تُعاد");
    expect(html).not.toContain("تأكيد الإرسال إلى قيود");
  });
});
