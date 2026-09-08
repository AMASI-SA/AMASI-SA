import api from "./api";
import {
  __resetQoyodUnsentOrdersClientForTests,
  loadQoyodUnsentOrders,
} from "./qoyodUnsentOrdersClient";

jest.mock("./api", () => ({
  __esModule: true,
  default: { get: jest.fn() },
}));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  api.get.mockReset();
  __resetQoyodUnsentOrdersClientForTests();
});

test("twenty identical consumers share one HTTP request", async () => {
  const request = deferred();
  api.get.mockReturnValue(request.promise);
  const params = { from_date: "2026-07-01", limit: 5000 };

  const consumers = Array.from(
    { length: 20 },
    () => loadQoyodUnsentOrders(params),
  );
  expect(api.get).toHaveBeenCalledTimes(1);

  request.resolve({ data: { ok: true, orders: [] } });
  await expect(Promise.all(consumers)).resolves.toHaveLength(20);
});

test("tenant/query parameters never share a key", async () => {
  api.get
    .mockResolvedValueOnce({ data: { key: "a" } })
    .mockResolvedValueOnce({ data: { key: "b" } });

  const [first, second] = await Promise.all([
    loadQoyodUnsentOrders({ days: 30, search: "tenant-a" }),
    loadQoyodUnsentOrders({ days: 30, search: "tenant-b" }),
  ]);

  expect(first.key).toBe("a");
  expect(second.key).toBe("b");
  expect(api.get).toHaveBeenCalledTimes(2);
});

test("failed requests leave no in-flight or cache entry", async () => {
  api.get
    .mockRejectedValueOnce(new Error("temporary failure"))
    .mockResolvedValueOnce({ data: { ok: true } });

  await expect(loadQoyodUnsentOrders({ days: 30 }))
    .rejects.toThrow("temporary failure");
  await expect(loadQoyodUnsentOrders({ days: 30 }))
    .resolves.toEqual({ ok: true });
  expect(api.get).toHaveBeenCalledTimes(2);
});

test("aborting one consumer does not cancel the shared request", async () => {
  const request = deferred();
  api.get.mockReturnValue(request.promise);
  const controller = new AbortController();

  const survivor = loadQoyodUnsentOrders({ days: 90 });
  const cancelled = loadQoyodUnsentOrders(
    { days: 90 },
    { signal: controller.signal },
  );
  controller.abort();

  await expect(cancelled).rejects.toMatchObject({ name: "AbortError" });
  request.resolve({ data: { ok: true } });
  await expect(survivor).resolves.toEqual({ ok: true });
  expect(api.get).toHaveBeenCalledTimes(1);
});

test("force bypasses a settled cache but still joins an active request", async () => {
  api.get.mockResolvedValueOnce({ data: { version: 1 } });
  await expect(loadQoyodUnsentOrders({ days: 7 }))
    .resolves.toEqual({ version: 1 });

  const request = deferred();
  api.get.mockReturnValueOnce(request.promise);
  const first = loadQoyodUnsentOrders({ days: 7 }, { force: true });
  const second = loadQoyodUnsentOrders({ days: 7 }, { force: true });
  expect(api.get).toHaveBeenCalledTimes(2);
  request.resolve({ data: { version: 2 } });
  await expect(Promise.all([first, second]))
    .resolves.toEqual([{ version: 2 }, { version: 2 }]);
});
