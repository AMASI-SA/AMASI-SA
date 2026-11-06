import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import api from "../lib/api";

jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));

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

  test("shows healthy automatic status and a manual cycle without individual-send action", () => {
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
    expect(html).toContain("تشغيل دورة الإرسال الآن");
    expect(html).not.toContain("تأكيد الإرسال إلى قيود");
  });

  test("shows the two-hour policy and the saved next cycle in Riyadh time", () => {
    const format = jest.spyOn(Date.prototype, "toLocaleString");
    try {
      const html = renderToStaticMarkup(<QoyodOverview
        settings={{ plan_b_auto_send_status: {
          armed: true, worker: { running: true },
          cycle: { next_due_at: "2026-11-06T18:00:00+00:00", status: "idle" },
        } }} unsent={{}} onRefresh={() => {}} onOpenTab={() => {}} />);
      expect(html).toContain("كل ساعتين");
      expect(html).toContain("الدورة القادمة");
      expect(html).toContain("جميع الفواتير المؤهلة مرة واحدة");
      expect(format).toHaveBeenCalledWith("ar-SA-u-ca-gregory", expect.objectContaining({ timeZone: "Asia/Riyadh" }));
    } finally {
      format.mockRestore();
    }
  });
});

describe("manual cycle", () => {
  let host, root, cycle;
  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers(); jest.clearAllMocks(); mockQoyodQuery = "";
    cycle = { status: "idle" };
    api.get.mockImplementation(async path => ({ data: path.endsWith("/settings")
      ? { plan_b_auto_send_status: { armed: true, worker: { running: true }, cycle } }
      : path.endsWith("/cycle") ? { cycle } : { queue_counts: {} } }));
    api.post.mockResolvedValue({ data: { accepted: true, status: "queued" } });
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
  });
  afterEach(async () => {
    await act(async () => root.unmount()); host.remove(); jest.clearAllTimers(); jest.useRealTimers();
  });
  test("one click queues all eligible and observes only lightweight status until completion", async () => {
    await act(async () => root.render(<QoyodControlCenter />));
    const button = () => host.querySelector('[data-testid="qoyod-cycle-run-now"]');
    await act(async () => { button().click(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith("/integrations/qoyod/auto-send/cycle");
    expect(button().disabled).toBe(true);
    await act(async () => { button().click(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    cycle = { status: "running", phase: "sending", processed_count: 500, eligible_count: 606, sent_count: 498, review_count: 2 };
    await act(async () => { jest.advanceTimersByTime(2000); });
    expect(host.textContent).toContain("عولج 500 من 606");
    expect(api.get.mock.calls.filter(([path]) => path.endsWith("/unsent-orders"))).toHaveLength(1);
    cycle = { status: "idle", phase: "complete", processed_count: 606, eligible_count: 606 };
    await act(async () => { jest.advanceTimersByTime(2000); });
    expect(button().disabled).toBe(false);
    expect(api.get.mock.calls.filter(([path]) => path.endsWith("/unsent-orders"))).toHaveLength(2);
    const calls = api.get.mock.calls.length;
    await act(async () => { jest.advanceTimersByTime(10000); });
    expect(api.get).toHaveBeenCalledTimes(calls);
  });
  test("owner permission failure stays visible and never begins polling", async () => {
    api.post.mockRejectedValue({ response: { data: { detail: "qoyod_security_owner_required" } } });
    await act(async () => root.render(<QoyodControlCenter />));
    await act(async () => host.querySelector('[data-testid="qoyod-cycle-run-now"]').click());
    expect(host.textContent).toContain("qoyod_security_owner_required");
    await act(async () => { jest.advanceTimersByTime(10000); });
    expect(api.get.mock.calls.some(([path]) => path.endsWith("/cycle"))).toBe(false);
  });
});
