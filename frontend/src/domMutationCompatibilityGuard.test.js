import {
  DOM_MUTATION_GUARD_POLICY,
  installDomMutationCompatibilityGuard,
  safeRemoveChild,
  uninstallDomMutationCompatibilityGuard,
} from "./domMutationCompatibilityGuard";
import { readSpaFailures } from "./spaRuntimeRecovery";

describe("DOM mutation compatibility guard", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    uninstallDomMutationCompatibilityGuard();
  });

  afterEach(() => {
    uninstallDomMutationCompatibilityGuard();
    document.body.innerHTML = "";
  });

  test("keeps valid removeChild behavior unchanged", () => {
    const parent = document.createElement("nav");
    const child = document.createElement("a");
    parent.appendChild(child);
    const native = jest.fn(function remove(target) {
      return Element.prototype.removeChild.call(this, target);
    });

    expect(safeRemoveChild(parent, child, native)).toBe(child);
    expect(native).toHaveBeenCalledTimes(1);
    expect(parent.contains(child)).toBe(false);
  });

  test("skips only an already detached or moved node", () => {
    const expectedParent = document.createElement("nav");
    expectedParent.dataset.testid = "mezan-v2-secondary-orders";
    const actualParent = document.createElement("div");
    const child = document.createElement("a");
    actualParent.appendChild(child);
    const native = jest.fn();

    expect(safeRemoveChild(expectedParent, child, native)).toBe(child);
    expect(native).not.toHaveBeenCalled();
    expect(child.parentNode).toBe(actualParent);
    expect(readSpaFailures(window.sessionStorage).at(-1)).toMatchObject({
      kind: "dom_remove_child_parent_mismatch",
      source: "dom_mutation_compatibility_guard",
      error: { name: "NotFoundError" },
    });
  });

  test("installs before React and restores the native method for tests", () => {
    const native = Node.prototype.removeChild;
    expect(installDomMutationCompatibilityGuard()).toBe(true);
    expect(Node.prototype.removeChild).not.toBe(native);
    expect(Node.prototype.removeChild._mezanDomMutationGuard).toBe(true);
    expect(installDomMutationCompatibilityGuard()).toBe(false);

    uninstallDomMutationCompatibilityGuard();
    expect(Node.prototype.removeChild).toBe(native);
  });

  test("declares the narrow guard policy", () => {
    expect(DOM_MUTATION_GUARD_POLICY).toEqual({
      operation: "removeChild",
      only_skips_parent_mismatch: true,
      preserves_valid_native_removals: true,
      records_diagnostics: true,
    });
  });
});
