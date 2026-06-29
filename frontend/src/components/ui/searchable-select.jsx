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
}) {
  const [open, setOpen] = useState(false);
  const stringValue = value == null ? "" : String(value);
  const selected = useMemo(
    () => options.find((o) => String(o.id) === stringValue) || null,
    [options, stringValue]
  );
  const orphan = stringValue && !selected;

  // Optional secondary field (e.g. phone for customers, percent for taxes).
  const secondary = selected && secondaryKey && selected[secondaryKey];

  const triggerLabel = selected
    ? selected.name
    : orphan
      ? `ID ${stringValue} غير موجود في قيود`
      : placeholder;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          data-testid={testid}
          aria-expanded={open}
          className={[
            "w-full flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm bg-white",
            "transition focus:outline-none focus:ring-2",
            orphan
              ? "border-amber-400 text-amber-900 focus:ring-amber-300"
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
          // Iter-290i — Filter on BOTH the name and the id so an
          // operator who only remembers the قيود id can still find
          // the row instantly.
          filter={(rowValue, search) => {
            const q = search.toLowerCase().trim();
            return rowValue.toLowerCase().includes(q) ? 1 : 0;
          }}
        >
          <CommandInput placeholder="بحث..." />
          <CommandList>
            <CommandEmpty>لا توجد نتائج</CommandEmpty>
            {/* Clear selection */}
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
                // Build a searchable string combining name + id +
                // any secondary so the user can search by phone /
                // tax percent / account code.
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
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

export default SearchableSelect;
