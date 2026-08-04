import { recordSpaFailure } from "./spaRuntimeRecovery";

let installed = false;
let originalRemoveChild = null;

function describeNode(node) {
  if (!node || typeof node !== "object") return "unknown";
  const tag = String(node.nodeName || node.tagName || "node").toLowerCase();
  const id = String(node.id || "").trim();
  const testid = String(node.getAttribute?.("data-testid") || "").trim();
  return [tag, id ? `#${id}` : "", testid ? `[data-testid=${testid}]` : ""]
    .filter(Boolean)
    .join("")
    .slice(0, 240);
}

export function safeRemoveChild(parent, child, nativeRemoveChild) {
  if (!parent || !child || typeof nativeRemoveChild !== "function") {
    return nativeRemoveChild?.call(parent, child);
  }
  if (child.parentNode !== parent) {
    recordSpaFailure(
      "dom_remove_child_parent_mismatch",
      new DOMException(
        "Skipped removeChild because the target node was already detached or moved.",
        "NotFoundError",
      ),
      {
        source: "dom_mutation_compatibility_guard",
        parent: describeNode(parent),
        child: describeNode(child),
      },
    );
    return child;
  }
  return nativeRemoveChild.call(parent, child);
}

export function installDomMutationCompatibilityGuard() {
  if (installed || typeof Node === "undefined") return false;
  const current = Node.prototype.removeChild;
  if (typeof current !== "function") return false;

  installed = true;
  originalRemoveChild = current;
  Node.prototype.removeChild = function guardedRemoveChild(child) {
    return safeRemoveChild(this, child, originalRemoveChild);
  };
  Node.prototype.removeChild._mezanDomMutationGuard = true;
  Node.prototype.removeChild._mezanOriginalRemoveChild = originalRemoveChild;
  return true;
}

export function uninstallDomMutationCompatibilityGuard() {
  if (!installed || typeof Node === "undefined") return;
  if (originalRemoveChild && Node.prototype.removeChild?._mezanDomMutationGuard) {
    Node.prototype.removeChild = originalRemoveChild;
  }
  installed = false;
  originalRemoveChild = null;
}

export const DOM_MUTATION_GUARD_POLICY = Object.freeze({
  operation: "removeChild",
  only_skips_parent_mismatch: true,
  preserves_valid_native_removals: true,
  records_diagnostics: true,
});
