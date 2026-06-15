// Iter-210b — Reusable inline help/info toggle.
//
// Renders a small "؟" sky-blue circle next to any label/control.
// Clicking it expands a slate panel with the supplied explanation.
// Use inside any flex/inline container — it controls its own visibility.
//
// Usage:
//   <HelpToggle testid="cod-bank-help">
//     <p>...explanation...</p>
//   </HelpToggle>

import { useState } from "react";

export default function HelpToggle({ children, testid, side = "below" }) {
    const [open, setOpen] = useState(false);
    return (
        <>
            <button type="button"
                onClick={() => setOpen(v => !v)}
                className={`inline-flex items-center justify-center w-5 h-5 rounded-full bg-sky-100 hover:bg-sky-200 text-sky-700 font-bold text-[11px] transition-colors flex-shrink-0 ${
                    open ? "ring-2 ring-sky-300" : ""
                }`}
                title="ما عمل هذا الحقل؟"
                data-testid={testid}>
                ؟
            </button>
            {open && (
                <div className={`${side === "below" ? "mt-2 col-span-full" : "ms-2"} bg-sky-50 border border-sky-200 rounded-lg p-3 text-xs text-slate-700 leading-relaxed space-y-2`}
                    data-testid={`${testid}-panel`}>
                    {children}
                </div>
            )}
        </>
    );
}
