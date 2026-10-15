import fs from "fs";
import path from "path";

describe("dashboard eligibility enhancer mutation safety", () => {
  const source = fs.readFileSync(path.join(__dirname, "dashboardEligibilityEnhancer.js"), "utf8");

  test("does not rewrite DOM text when the rendered value is unchanged", () => {
    expect(source).toContain("function setTextIfChanged");
    expect(source).toContain("if (node.textContent !== next) node.textContent = next");
  });

  test("does not rebuild the toggle on every MutationObserver callback", () => {
    expect(source).toContain("wrapper.dataset.renderedState !== enabledKey");
    expect(source).toContain("new MutationObserver(() => scheduleRender())");
    expect(source).not.toContain("new MutationObserver(() => renderEnhancements())");
  });

  test("uses one delegated click handler instead of rebinding on every render", () => {
    expect(source).toContain('document.addEventListener("click", handleToggleClick)');
  });
});
