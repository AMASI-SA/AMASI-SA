/**
 * Iter-290h.2 — <Term /> + <TermPill />
 *
 * Reusable display widgets that render an Arabic label with an
 * accessible tooltip carrying the explanation. Used wherever a
 * technical Qoyod/pipeline identifier surfaces in the UI.
 *
 * Examples:
 *   <Term code="INVOICE_PAYMENT_CREATED" kind="stage" />
 *   <TermPill code="duplicate_idempotency_key" kind="reason" tone="yellow" />
 *
 * The tooltip uses native `title` attribute so it works without a
 * heavyweight popover dependency. Visual underline indicates the
 * term is hoverable for more info.
 */
import React from "react";
import { termFor } from "../lib/qoyodTerminology";

/** Plain inline label with hover tooltip. */
export const Term = ({ code, kind = "stage", showRaw = false, className = "" }) => {
  const { label, description } = termFor(code, kind);
  return (
    <span
      title={description ? `${description}${showRaw ? ` (${code})` : ""}` : (showRaw ? code : undefined)}
      className={`underline decoration-dotted decoration-zinc-400 cursor-help ${className}`}
      data-testid={`term-${kind}-${code}`}
    >
      {label}
    </span>
  );
};

/** Pill variant — coloured background. */
export const TermPill = ({ code, kind = "stage", tone = "default", showRaw = false }) => {
  const { label, description } = termFor(code, kind);
  const cls = {
    default: "bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-200",
    green:   "bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-200",
    yellow:  "bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-200",
    red:     "bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-200",
    indigo:  "bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-200",
  }[tone] || "";
  return (
    <span
      title={description ? `${description}${showRaw ? ` (${code})` : ""}` : undefined}
      className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium cursor-help ${cls}`}
      data-testid={`termpill-${kind}-${code}`}
    >
      {label}
    </span>
  );
};

/** Card-style explanation block — used in screen headers. */
export const TermHelpCard = ({ code, kind = "general", className = "" }) => {
  const { label, description } = termFor(code, kind);
  if (!description) return null;
  return (
    <div
      className={`rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900 px-3 py-2 text-xs text-zinc-700 dark:text-zinc-300 ${className}`}
      data-testid={`termhelp-${kind}-${code}`}
    >
      <span className="font-medium text-zinc-900 dark:text-zinc-100">{label}:</span>{" "}
      <span>{description}</span>
    </div>
  );
};
