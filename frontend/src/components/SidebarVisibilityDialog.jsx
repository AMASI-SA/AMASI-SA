/**
 * Iter-124 — Dialog allowing the merchant to choose which sidebar
 * pages to HIDE.  All items default to visible.  Toggling is instant
 * and persisted to localStorage.  The Sidebar listens for the
 * `mezan:sidebar-visibility-changed` event and re-renders immediately.
 *
 * Note: this component does NOT own the section list — it receives
 * it from the Sidebar (`sections` prop) so we have a single source of
 * truth for the nav structure.
 */
import { useState } from "react";
import {
    Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
    DialogFooter,
} from "./ui/dialog";
import { Button } from "./ui/button";
import { Eye, EyeSlash, ArrowsClockwise } from "@phosphor-icons/react";
import { loadHiddenPages, saveHiddenPages } from "../lib/sidebarVisibility";

export default function SidebarVisibilityDialog({ open, onClose, sections }) {
    // Re-key on open so the dialog re-reads localStorage fresh every
    // time it's shown (in case another tab toggled visibility).
    return open ? (
        <DialogInner
            key={`sv-${open}`}
            open={open}
            onClose={onClose}
            sections={sections}
        />
    ) : null;
}

function DialogInner({ open, onClose, sections }) {
    const [hidden, setHidden] = useState(() => loadHiddenPages());

    const toggle = (testid) => {
        const next = new Set(hidden);
        if (next.has(testid)) next.delete(testid);
        else next.add(testid);
        setHidden(next);
        saveHiddenPages(next);
    };

    const showAll = () => {
        setHidden(new Set());
        saveHiddenPages([]);
    };

    const hiddenCount = hidden.size;

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent
                className="max-w-2xl max-h-[85vh] overflow-y-auto"
                data-testid="sidebar-visibility-dialog"
            >
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Eye size={20} weight="duotone" />
                        إعدادات إظهار وإخفاء الصفحات
                    </DialogTitle>
                    <DialogDescription>
                        اختر الصفحات التي تريد إخفاءها من الشريط الجانبي.
                        التغييرات تُحفظ تلقائياً على هذا الجهاز فقط.
                    </DialogDescription>
                </DialogHeader>

                {/* Quick stats */}
                <div className="flex items-center justify-between bg-slate-50 rounded-lg p-3 text-sm">
                    <div>
                        <span className="text-slate-600">صفحات مخفية: </span>
                        <span className="font-bold text-rose-600 num" data-testid="sv-hidden-count">
                            {hiddenCount}
                        </span>
                    </div>
                    {hiddenCount > 0 && (
                        <Button
                            type="button" size="sm" variant="outline"
                            onClick={showAll}
                            data-testid="sv-show-all"
                        >
                            <ArrowsClockwise size={14} className="ml-1" />
                            إظهار الكل
                        </Button>
                    )}
                </div>

                {/* Sections grid */}
                <div className="space-y-4 mt-2">
                    {sections.map((sec) => (
                        <div
                            key={sec.id}
                            className="border-2 border-slate-200 rounded-xl p-3"
                            data-testid={`sv-section-${sec.id}`}
                        >
                            <h4 className="text-sm font-extrabold text-slate-800 mb-2 flex items-center gap-2">
                                {sec.label}
                                <span className="text-xs font-normal text-slate-500">
                                    ({sec.items.filter((i) => !hidden.has(i.testid)).length}
                                    {" / "}
                                    {sec.items.length} ظاهرة)
                                </span>
                            </h4>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                                {sec.items.map((item) => {
                                    const isHidden = hidden.has(item.testid);
                                    return (
                                        <label
                                            key={item.testid}
                                            className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs cursor-pointer transition border ${
                                                isHidden
                                                    ? "bg-rose-50 border-rose-200 text-slate-500"
                                                    : "bg-white border-slate-200 hover:bg-emerald-50 hover:border-emerald-300"
                                            }`}
                                            data-testid={`sv-toggle-${item.testid}`}
                                        >
                                            <input
                                                type="checkbox"
                                                checked={!isHidden}
                                                onChange={() => toggle(item.testid)}
                                                className="h-4 w-4 accent-emerald-600 shrink-0"
                                            />
                                            <span className={isHidden ? "line-through" : "font-medium"}>
                                                {item.label}
                                            </span>
                                            {isHidden && (
                                                <EyeSlash size={12} className="mr-auto text-rose-500 shrink-0" />
                                            )}
                                        </label>
                                    );
                                })}
                            </div>
                        </div>
                    ))}
                </div>

                <DialogFooter className="mt-3">
                    <Button
                        type="button"
                        onClick={onClose}
                        data-testid="sv-close"
                    >
                        إغلاق
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
