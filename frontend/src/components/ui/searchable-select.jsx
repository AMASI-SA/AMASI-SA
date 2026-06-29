/* eslint-disable react/prop-types */
import React, { useState, useMemo } from "react";
import { Check, ChevronsUpDown, AlertTriangle } from "lucide-react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "./command";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";

/**
 * Iter-290i — Searchable, name-first picker for قيود reference lists.
 *
 * Behaviour
 * ─────────
 *   • Renders the option NAME as the primary label.
 *   • Surfaces the قيود id as a small monospace tag next to the name.
 *   • Filters by name OR id (so an operator who remembers the id
 *     can still find the option fast).
 *   • If `value` doesn't match any option in `options`, the trigger
 *     shows a warning state explaining the saved id is no longer
 *     present in قيود — but it never silently drops the value.
 *
 * Props
 * ─────
 *   options        Array<{id: string, name: string, ...extra}>
 *   value          string | null   (the قيود id we're persisting)
 *   onChange       (id: string | null) => void
 *   placeholder    string          (label inside the trigger when no value)
 *   testid         string          (data-testid for the trigger)
 *   secondaryKey   string          (optional extra field to show, e.g. 'phone')
 *   disabled       boolean
 *
 * Visual contract: `value` is null/empty → placeholder grey. `value`
 * present but not in options → warning amber. `value` matches an
 * option → standard slate.
 */
export function SearchableSelect({
  options = [],
  value,
  onChange,
  placeholder = "اختر...",
  testid,
  secondaryKey,
  disabled = false,
  // Iter-290i.1 — When the list itself failed to load from قيود, we
  // MUST NOT label saved ids as "missing in قيود" because we can't
  // tell — the list was never fetched. `listUnavailable=true` puts
  // the picker in a read-only neutral state that shows the saved id
  // without judgement, plus a banner asking the operator to retry.
  listUnavailable = false,
  unavailableReason = null,
  // Iter-290i.2 — caller-supplied trigger label for when
  // `listUnavailable && value` — defaults to a generic message but
  // can be overridden so the user sees a domain-specific hint
  // (e.g. "تعذر تحميل قائمة حسابات قيود" for the payment-method
  // mapping picker).
  unavailableLabel = null,
}) {
  const [open, setOpen] = useState(false);
  const stringValue = value == null ? "" : String(value);
  const selected = useMemo(
    () => options.find((o) => String(o.id) === stringValue) || null,
    [options, stringValue]
  );
  // Only call it "orphan" when the list DID load and the saved id
  // truly isn't in it. If the list never loaded, we cannot judge.
  const orphan = stringValue && !selected && !listUnavailable;

  // Optional secondary field (e.g. phone for customers, percent for taxes).
  const secondary = selected && secondaryKey && selected[secondaryKey];

  const triggerLabel = selected
    ? selected.name
    : orphan
      ? `ID ${stringValue} غير موجود في قيود`
      : listUnavailable && stringValue
        ? (unavailableLabel
            ? `${unavailableLabel} (ID ${stringValue})`
            : `ID ${stringValue} (لم تُحمّل القائمة)`)
        : placeholder;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled || (listUnavailable && !stringValue)}
          data-testid={testid}
          aria-expanded={open}
          className={[
            "w-full flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm bg-white",
            "transition focus:outline-none focus:ring-2",
            orphan
              ? "border-amber-400 text-amber-900 focus:ring-amber-300"
              : listUnavailable
                ? "border-slate-300 text-slate-500 focus:ring-slate-300"
                : selected
                  ? "border-slate-300 text-slate-800 focus:ring-sky-300"
                  : "border-slate-200 text-slate-400 focus:ring-slate-300",
            disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
          ].join(" ")}
        >
          <span className="truncate text-right" dir="auto">
            {orphan && (
              <AlertTriangle className="inline-block w-3.5 h-3.5 mr-1 text-amber-600" />
            )}
            {listUnavailable && (
              <AlertTriangle className="inline-block w-3.5 h-3.5 mr-1 text-slate-400" />
            )}
            {triggerLabel}
            {selected && (
              <span className="ml-2 text-[10px] font-mono text-slate-400">
                ID {selected.id}
                {secondary ? ` · ${secondary}` : ""}
              </span>
            )}
          </span>
          <ChevronsUpDown className="w-4 h-4 text-slate-400 shrink-0" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="end">
        <Command
          filter={(rowValue, search) => {
            const q = search.toLowerCase().trim();
            return rowValue.toLowerCase().includes(q) ? 1 : 0;
          }}
        >
          <CommandInput placeholder="بحث..." />
          <CommandList>
            {listUnavailable ? (
              <div className="px-3 py-4 text-xs text-slate-600 text-center"
                   data-testid={testid ? `${testid}-unavailable` : undefined}>
                <AlertTriangle className="w-5 h-5 mx-auto mb-1 text-amber-500" />
                <div className="font-bold mb-1">تعذّر تحميل القائمة من قيود</div>
                {unavailableReason && (
                  <div className="text-[10px] text-slate-500" dir="ltr">
                    {unavailableReason}
                  </div>
                )}
                <div className="text-[10px] text-slate-500 mt-1">
                  اضغط "تحديث القوائم من قيود" لإعادة المحاولة.
                </div>
              </div>
            ) : (
              <>
                <CommandEmpty>لا توجد نتائج</CommandEmpty>
                {selected && (
                  <CommandGroup>
                    <CommandItem
                      value="__clear__"
                      onSelect={() => {
                        onChange(null);
                        setOpen(false);
                      }}
                      data-testid={testid ? `${testid}-clear` : undefined}
                    >
                      <span className="text-slate-500">— إلغاء الاختيار —</span>
                    </CommandItem>
                  </CommandGroup>
                )}
                <CommandGroup>
                  {options.map((opt) => {
                    const idStr = String(opt.id);
                    const optSecondary = secondaryKey ? opt[secondaryKey] : null;
                    const searchable = [opt.name, idStr, optSecondary]
                      .filter(Boolean)
                      .join(" ");
                    return (
                      <CommandItem
                        key={idStr}
                        value={searchable}
                        onSelect={() => {
                          onChange(idStr);
                          setOpen(false);
                        }}
                        data-testid={testid ? `${testid}-option-${idStr}` : undefined}
                      >
                        <Check
                          className={[
                            "mr-2 h-4 w-4",
                            idStr === stringValue ? "opacity-100" : "opacity-0",
                          ].join(" ")}
                        />
                        <span className="flex-1 text-right" dir="auto">
                          {opt.name}
                        </span>
                        <span className="ml-2 text-[10px] font-mono text-slate-400">
                          ID {idStr}
                          {optSecondary ? ` · ${optSecondary}` : ""}
                        </span>
                      </CommandItem>
                    );
                  })}
                </CommandGroup>
              </>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

export default SearchableSelect;
