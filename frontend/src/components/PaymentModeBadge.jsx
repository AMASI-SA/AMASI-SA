// Iter-189 — Reusable Payment Mode Badge
//
// Renders a clear, color-coded badge for a shipping company's payment
// mode: 🟢 "دفع مقدم" (Prepaid) or 🟠 "دفع آجل" (Deferred). Used by:
//   • CODDiagnostic page (next to each courier row)
//   • Shipping Ledger (per-order column, in iter-192-ext)
//   • Couriers Ledger summary
//
// Accepts either the new `payment_mode` string OR the legacy
// `is_deferred` boolean — whichever the parent page has in scope.

import React from "react";

export function PaymentModeBadge({ payment_mode, is_deferred, size = "sm" }) {
    const isDeferred = payment_mode != null
        ? payment_mode === "deferred"
        : !!is_deferred;
    const cls = isDeferred
        ? "bg-amber-100 text-amber-900 border-amber-300"
        : "bg-emerald-100 text-emerald-900 border-emerald-300";
    const icon = isDeferred ? "🟠" : "🟢";
    const label = isDeferred ? "دفع آجل" : "دفع مقدم";
    const pad = size === "xs" ? "px-1.5 py-0.5 text-[10px]"
        : size === "lg" ? "px-3 py-1.5 text-sm"
        : "px-2 py-1 text-[11px]";
    return (
        <span
            className={`inline-flex items-center gap-1 rounded-full border font-extrabold ${cls} ${pad}`}
            data-testid={`payment-mode-badge-${isDeferred ? "deferred" : "prepaid"}`}
        >
            <span>{icon}</span>
            <span>{label}</span>
        </span>
    );
}

export default PaymentModeBadge;
