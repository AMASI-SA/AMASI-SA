import React, { act } from "react";
import { createRoot } from "react-dom/client";
import AppCrashBoundary from "./AppCrashBoundary";

function BrokenPage() {
  throw new Error("route render failed");
}

describe("AppCrashBoundary", () => {
  let container;
  let root;
  let consoleError;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
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
  });

  test("replaces an uncaught render crash with a visible recovery screen", async () => {
    await act(async () => {
      root.render(
        <AppCrashBoundary>
          <BrokenPage />
        </AppCrashBoundary>,
      );
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
    expect(container.querySelector('[data-testid="app-crash-recovery"]')).toBeNull();
  });
});
