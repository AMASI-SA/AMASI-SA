import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../lib/api", () => ({
  get: jest.fn(),
  post: jest.fn(),
}));

import api from "../lib/api";
import QoyodUnsentOrders from "./QoyodUnsentOrders";

async function renderRecoveryPage(path = "/") {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  window.history.pushState({}, "", path);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<QoyodUnsentOrders />); });
  await act(async () => { await Promise.resolve(); });
  return { container, root };
}

async function cleanup(container, root) {
  await act(async () => root.unmount());
  container.remove();
  window.history.pushState({}, "", "/");
  globalThis.IS_REACT_ACT_ENVIRONMENT = false;
}

beforeEach(() => {
  jest.clearAllMocks();
  api.get.mockResolvedValue({
    data: {
      counts: {},
      orders: [],
      salla_status_counts: {},
    },
  });
});

test("recovery panel documents the closed three-status live gate", async () => {
  const { container, root } = await renderRecoveryPage("/?recovery=1");
  try {
    const panel = container.querySelector('[data-testid="qoyod-recovery-panel"]');
    expect(panel).not.toBeNull();
    expect(panel.textContent).toContain("تم التنفيذ");
    expect(panel.textContent).toContain("جاري التوصيل");
    expect(panel.textContent).toContain("تم التوصيل");
    expect(panel.textContent).toContain("تم التجهيز");
    expect(panel.textContent).toContain("completed");
    expect(panel.textContent).not.toContain("ولا يقبل إلا «تم التنفيذ»،");
  } finally {
    await cleanup(container, root);
  }
});

test("visible recovery button opens the restricted resend panel", async () => {
  const { container, root } = await renderRecoveryPage();
  try {
    const toggle = container.querySelector('[data-testid="qoyod-recovery-toggle"]');
    expect(toggle).not.toBeNull();
    expect(toggle.textContent).toContain("إعادة إرسال الطلبات المقيدة");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector('[data-testid="qoyod-recovery-panel"]')).toBeNull();

    await act(async () => { toggle.click(); });

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector('[data-testid="qoyod-recovery-panel"]')).not.toBeNull();
    expect(toggle.textContent).toContain("إغلاق إعادة الإرسال");
  } finally {
    await cleanup(container, root);
  }
});
