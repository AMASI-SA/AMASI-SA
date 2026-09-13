import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("axios", () => ({
  get: jest.fn(),
  post: jest.fn(),
  put: jest.fn(),
  delete: jest.fn(),
}));
jest.mock("sonner", () => ({
  toast: {
    success: jest.fn(),
    error: jest.fn(),
    warning: jest.fn(),
  },
}));
jest.mock("../components/ui/searchable-select", () => ({
  SearchableSelect: ({ value = "", onChange, disabled, "data-testid": testid }) => (
    <input
      data-testid={testid}
      disabled={disabled}
      onChange={(event) => onChange?.(event.target.value)}
      value={value || ""}
    />
  ),
}));
jest.mock("lucide-react", () => ({
  RefreshCw: () => null,
}));

import axios from "axios";
import { toast } from "sonner";

process.env.REACT_APP_BACKEND_URL = "";
const QoyodSettings = require("./QoyodSettings").default;

const SETTINGS_PATH = "/api/integrations/qoyod/settings";
const CREDENTIALS_PATH = "/api/integrations/qoyod/credentials";
const TEST_PATH = "/api/integrations/qoyod/test-connection";

const completeSettings = (fingerprint = "old1…old2") => ({
  user_id: "main",
  enabled: false,
  auto_send: false,
  auto_receipt: true,
  dry_run_mode: false,
  invoice_trigger_statuses: ["completed"],
  invoice_date_source: "send_date",
  trigger_once_only: true,
  default_branch_id: "1",
  default_tax_id: "1",
  default_customer_id: "2",
  default_product_type: "service",
  default_product_category_id: "1",
  default_product_tax_id: "1",
  default_product_unit_type_id: "6",
  default_sales_account_id: "17",
  default_inventory_id: "1",
  auto_adopt_existing_qoyod_products: true,
  payment_method_mapping: [],
  capabilities: {
    create_customers: true,
    create_products: true,
    create_invoices: true,
    create_receipts: true,
  },
  backfill_mode: "now_forward_only",
  credentials: { configured: true, fingerprint },
});

function configureReads(fingerprint = "old1…old2") {
  axios.get.mockImplementation((url) => {
    if (url === SETTINGS_PATH) {
      return Promise.resolve({ data: completeSettings(fingerprint) });
    }
    if (url.endsWith("/payment-methods/used")) {
      return Promise.resolve({ data: { catalogue: [], used: [] } });
    }
    if (url.endsWith("/salla-order-statuses")) {
      return Promise.resolve({ data: { statuses: [], source: "fixture" } });
    }
    if (url.endsWith("/admin/reference-lists")) {
      return Promise.resolve({
        data: {
          ok: true,
          lists: {
            categories: [], unit_types: [], inventories: [], accounts: [],
            taxes: [], branches: [], customers: [],
          },
        },
      });
    }
    if (/\/qoyod-(branches|accounts|taxes|inventories)$/.test(url)) {
      return Promise.resolve({ data: { data: [] } });
    }
    throw new Error(`unexpected GET ${url}`);
  });
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function renderPage(fingerprint = "old1…old2") {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  configureReads(fingerprint);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => root.render(<QoyodSettings />));
  await flush();
  return { container, root };
}

async function cleanup(container, root) {
  await act(async () => root.unmount());
  container.remove();
  globalThis.IS_REACT_ACT_ENVIRONMENT = false;
}

async function setInput(input, value) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  ).set;
  await act(async () => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  jest.clearAllMocks();
});

test("replaces an existing key through the credentials endpoint without deleting or saving settings", async () => {
  axios.post.mockImplementation((url) => {
    if (url === CREDENTIALS_PATH) {
      return Promise.resolve({ data: { ok: true, fingerprint: "new1…new2" } });
    }
    throw new Error(`unexpected POST ${url}`);
  });
  const { container, root } = await renderPage();
  try {
    expect(container.textContent).toContain("old1…old2");
    expect(container.querySelector('[data-testid="input-api-key"]')).toBeNull();

    const edit = container.querySelector('[data-testid="btn-edit-credentials"]');
    expect(edit).not.toBeNull();
    await act(async () => edit.click());

    const input = container.querySelector('[data-testid="input-api-key"]');
    expect(input).not.toBeNull();
    expect(input.type).toBe("password");
    expect(container.textContent).toContain("old1…old2");
    await setInput(input, "  new-secret-key  ");

    const save = container.querySelector('[data-testid="btn-save-credentials"]');
    await act(async () => save.click());
    await flush();

    expect(axios.post).toHaveBeenCalledWith(
      CREDENTIALS_PATH,
      { api_key: "new-secret-key" },
    );
    expect(axios.delete).not.toHaveBeenCalled();
    expect(axios.put).not.toHaveBeenCalled();
    expect(axios.get.mock.calls.filter(([url]) => url === SETTINGS_PATH)).toHaveLength(1);
    expect(container.textContent).toContain("new1…new2");
    expect(container.textContent).not.toContain("old1…old2");
    expect(container.querySelector('[data-testid="input-api-key"]')).toBeNull();
  } finally {
    await cleanup(container, root);
  }
});

test("keeps the old fingerprint and the replacement form when credential persistence fails", async () => {
  axios.post.mockRejectedValueOnce(new Error("synthetic storage failure"));
  const { container, root } = await renderPage();
  try {
    await act(async () => {
      container.querySelector('[data-testid="btn-edit-credentials"]').click();
    });
    const input = container.querySelector('[data-testid="input-api-key"]');
    await setInput(input, "replacement-key");
    await act(async () => {
      container.querySelector('[data-testid="btn-save-credentials"]').click();
    });
    await flush();

    expect(container.textContent).toContain("old1…old2");
    expect(container.querySelector('[data-testid="input-api-key"]')).not.toBeNull();
    expect(toast.error).toHaveBeenCalledWith("فشل حفظ المفتاح");
    expect(axios.delete).not.toHaveBeenCalled();
  } finally {
    await cleanup(container, root);
  }
});

test("does not report success when the save response lacks persistence evidence", async () => {
  axios.post.mockResolvedValueOnce({ data: { ok: true, fingerprint: null } });
  const { container, root } = await renderPage();
  try {
    await act(async () => {
      container.querySelector('[data-testid="btn-edit-credentials"]').click();
    });
    await setInput(
      container.querySelector('[data-testid="input-api-key"]'),
      "replacement-key",
    );
    await act(async () => {
      container.querySelector('[data-testid="btn-save-credentials"]').click();
    });
    await flush();

    expect(container.textContent).toContain("old1…old2");
    expect(container.querySelector('[data-testid="input-api-key"]')).not.toBeNull();
    expect(toast.success).not.toHaveBeenCalledWith("تم حفظ المفتاح بشكل آمن");
    expect(toast.error).toHaveBeenCalledWith("فشل حفظ المفتاح");
  } finally {
    await cleanup(container, root);
  }
});

test("a page reload reads and displays the persisted replacement fingerprint", async () => {
  axios.post.mockResolvedValueOnce({
    data: { ok: true, fingerprint: "new1…new2" },
  });
  const first = await renderPage();
  try {
    await act(async () => {
      first.container.querySelector('[data-testid="btn-edit-credentials"]').click();
    });
    await setInput(
      first.container.querySelector('[data-testid="input-api-key"]'),
      "new-secret-key",
    );
    await act(async () => {
      first.container.querySelector('[data-testid="btn-save-credentials"]').click();
    });
    await flush();
    expect(first.container.textContent).toContain("new1…new2");
  } finally {
    await cleanup(first.container, first.root);
  }

  const reloaded = await renderPage("new1…new2");
  try {
    expect(reloaded.container.textContent).toContain("new1…new2");
    expect(reloaded.container.textContent).not.toContain("old1…old2");
  } finally {
    await cleanup(reloaded.container, reloaded.root);
  }
});

test("ignores a connection response that started before a successful key replacement", async () => {
  let resolveOldTest;
  const oldTest = new Promise((resolve) => { resolveOldTest = resolve; });
  axios.post.mockImplementation((url) => {
    if (url === TEST_PATH) return oldTest;
    if (url === CREDENTIALS_PATH) {
      return Promise.resolve({ data: { ok: true, fingerprint: "new1…new2" } });
    }
    throw new Error(`unexpected POST ${url}`);
  });

  const { container, root } = await renderPage();
  try {
    await act(async () => {
      container.querySelector('[data-testid="btn-test-connection"]').click();
    });
    await act(async () => {
      container.querySelector('[data-testid="btn-edit-credentials"]').click();
    });
    const input = container.querySelector('[data-testid="input-api-key"]');
    await setInput(input, "new-secret-key");
    await act(async () => {
      container.querySelector('[data-testid="btn-save-credentials"]').click();
    });
    await flush();

    await act(async () => {
      resolveOldTest({
        data: {
          ok: false,
          fingerprint: "old1…old2",
          error: { code: "qoyod_unauthorized", message: "old key rejected" },
        },
      });
      await oldTest;
    });

    expect(container.textContent).toContain("new1…new2");
    expect(container.textContent).not.toContain("old key rejected");
    expect(toast.error).not.toHaveBeenCalledWith("old key rejected");
  } finally {
    await cleanup(container, root);
  }
});
