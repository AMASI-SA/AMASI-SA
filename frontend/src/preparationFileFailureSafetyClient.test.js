import {
  finalizeClientRequestId,
  safeDraftConfig,
} from "./preparationFileFailureSafetyClient";

beforeEach(() => {
  window.__mezanPreparationFileMetadata = undefined;
});

test("draft request is rewritten to validated safety endpoint with schedule", () => {
  window.__mezanPreparationFileMetadata = {
    scheduleMode: "required",
    requiredDueAt: "2026-08-05T09:00:00.000Z",
  };

  const config = safeDraftConfig({
    url: "/preparation-file-registry-v1/drafts",
    method: "post",
    data: {
      client_request_id: "request-123",
      file_title: "دفعة",
      responsible_employee_id: "employee-1",
      expected_quantity: 2,
      selected_product_count: 1,
    },
  });

  expect(config.url).toBe("/preparation-file-safety-v1/drafts");
  expect(config.data.schedule_mode).toBe("required");
  expect(config.data.required_due_at).toBe("2026-08-05T09:00:00.000Z");
  expect(config.data.client_request_id).toBe("request-123");
});

test("automatic schedule clears an obsolete required date", () => {
  window.__mezanPreparationFileMetadata = {
    scheduleMode: "automatic",
    requiredDueAt: "2026-08-05T09:00:00.000Z",
  };

  const config = safeDraftConfig({
    url: "/preparation-file-registry-v1/drafts",
    data: { client_request_id: "request-123" },
  });

  expect(config.data.schedule_mode).toBe("automatic");
  expect(config.data.required_due_at).toBeNull();
});

test("finalize request id is decoded for rollback", () => {
  expect(finalizeClientRequestId({
    url: "/preparation-file-registry-v1/finalize/request%20abc",
  })).toBe("request abc");
  expect(finalizeClientRequestId({ url: "/other" })).toBe("");
});
