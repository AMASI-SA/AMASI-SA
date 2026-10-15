import React, { act } from "react";
import { createRoot } from "react-dom/client";
import AppCrashBoundary from "./AppCrashBoundary";

function BrokenPage() {
  throw new Error("route render failed");
}

let transientPageReady = false;
function TransientPage() {
  if (!transientPageReady) throw new Error("temporary navigation state");
  return <div data-testid="recovered-page">تمت الاستعادة</div>;
}

let delayedPageReady = false;
function DelayedOriginPage() {
  if (!delayedPageReady) throw new Error("origin is still restarting");
  return <div data-testid="origin-recovered-page">عاد الخادم</div>;
}

describe("AppCrashBoundary", () => {
  let container;
  let root;
  let consoleError;
  let originalFetch;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    transientPageReady = false;
    delayedPageReady = false;
    originalFetch = window.fetch;
    window.fetch = jest.fn();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
    window.sessionStorage.clear();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    consoleError.mockRestore();
    container.remove();
    if (originalFetch === undefined) delete window.fetch;
    else window.fetch = originalFetch;
    jest.useRealTimers();
  });

  test("retries one transient render crash without showing the manual reload card", async () => {
    await act(async () => {
      root.render(
        <AppCrashBoundary>
          <TransientPage />
        </AppCrashBoundary>,
      );
    });

    expect(
      container.querySelector('[data-testid="app-crash-recovery-pending"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-testid="app-crash-recovery"]'),
    ).toBeNull();

    transientPageReady = true;
    await act(async () => {
      jest.advanceTimersByTime(300);
    });

    expect(container.querySelector('[data-testid="recovered-page"]')).not.toBeNull();
    expect(
      container.querySelector('[data-testid="app-crash-recovery-pending"]'),
    ).toBeNull();
    expect(
      container.querySelector('[data-testid="app-crash-recovery"]'),
    ).toBeNull();
  });

  test("retries the route when the public origin becomes healthy after the fallback appeared", async () => {
    window.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true }),
    });

    await act(async () => {
      root.render(
        <AppCrashBoundary>
          <DelayedOriginPage />
        </AppCrashBoundary>,
      );
    });

    await act(async () => {
      jest.advanceTimersByTime(300);
    });
    expect(
      container.querySelector('[data-testid="app-crash-recovery"]'),
    ).not.toBeNull();

    delayedPageReady = true;
    await act(async () => {
      jest.advanceTimersByTime(5_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(window.fetch).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/health\?spa_recovery=\d+$/),
      expect.objectContaining({
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
      }),
    );
    expect(
      container.querySelector('[data-testid="origin-recovered-page"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-testid="app-crash-recovery"]'),
    ).toBeNull();
  });

  test("shows manual recovery only after the bounded retry also fails", async () => {
    await act(async () => {
      root.render(
        <AppCrashBoundary>
          <BrokenPage />
        </AppCrashBoundary>,
      );
    });

    expect(
      container.querySelector('[data-testid="app-crash-recovery-pending"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-testid="app-crash-recovery"]'),
    ).toBeNull();

    await act(async () => {
      jest.advanceTimersByTime(300);
    });

    const fallback = container.querySelector('[data-testid="app-crash-recovery"]');
    expect(fallback).not.toBeNull();
    expect(fallback.textContent).toContain("تعذر عرض الصفحة مؤقتًا");
    expect(fallback.textContent).toContain("إعادة تحميل الصفحة");
    expect(container.textContent.trim()).not.toBe("");
  });

  test("renders healthy children without adding recovery UI", async () => {
    await act(async () => {
      root.render(
        <AppCrashBoundary>
          <div data-testid="healthy-page">صفحة سليمة</div>
        </AppCrashBoundary>,
      );
    });

    expect(container.querySelector('[data-testid="healthy-page"]')).not.toBeNull();
    expect(
      container.querySelector('[data-testid="app-crash-recovery-pending"]'),
    ).toBeNull();
    expect(
      container.querySelector('[data-testid="app-crash-recovery"]'),
    ).toBeNull();
  });
});
