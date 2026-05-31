import { useState } from "react";
import { Eye, EyeSlash, Copy, Trash, Check } from "@phosphor-icons/react";
import { toast } from "sonner";

/**
 * SecretField — uniform UI for long sensitive tokens (Meta/Snap access tokens,
 * client secrets, refresh tokens). Solves the "long string breaks the layout"
 * problem with:
 *   • Masked preview by default (first 10 + ... + last 6 characters).
 *   • 👁 Show/Hide toggle → expands into a wrappable textarea.
 *   • 📋 Copy + 🗑 Clear shortcut buttons.
 *   • Fully responsive (buttons stack under field on mobile).
 *   • `overflow-wrap: anywhere; word-break: break-all` to prevent any
 *     horizontal scroll on tiny phones.
 *
 * NOTE: this component is intentionally fully-controlled. Parent owns the
 * value + onChange. When `existingMask` is passed (e.g. "EAA****ABC") we
 * show that mask as a hint when the input is empty — typical "stored on
 * the server, leave blank to keep" UX.
 */
export default function SecretField({
    label,
    value,
    onChange,
    existingMask = null,     // mask string returned by backend, e.g. "•••KAB78ZD"
    placeholder = "",
    helper = null,           // optional helper text under the field
    testidPrefix = "secret",
    statusBadge = null,      // <StatusBadge/> node (e.g. expired/valid pill)
    rows = 4,                // textarea rows when expanded
    direction = "ltr",       // tokens are usually LTR even on Arabic pages
    disabled = false,
}) {
    const [shown, setShown] = useState(false);

    const masked = (() => {
        const v = value || "";
        if (v.length === 0) return existingMask || "";
        if (v.length <= 20) return "•".repeat(v.length);
        return `${v.slice(0, 10)}${"•".repeat(6)}${v.slice(-6)}`;
    })();

    const copyToClipboard = async () => {
        const text = value || "";
        if (!text) {
            toast.error("لا يوجد توكن للنسخ");
            return;
        }
        try {
            await navigator.clipboard.writeText(text);
            toast.success("تم النسخ ✓");
        } catch {
            toast.error("تعذّر النسخ");
        }
    };

    const clearField = () => {
        if (!value) return;
        onChange("");
        toast("تم المسح", { icon: "🗑" });
    };

    return (
        <div className="w-full min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-1.5">
                <label className="text-xs font-bold text-muted-foreground">
                    {label}
                </label>
                {statusBadge}
            </div>

            {shown ? (
                /* EXPANDED — textarea allows native word-wrapping of long tokens
                   without any horizontal scroll. break-all keeps it tidy on phones. */
                <textarea
                    value={value || ""}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder={placeholder}
                    rows={rows}
                    dir={direction}
                    disabled={disabled}
                    className="w-full max-w-full px-3 py-2.5 text-xs sm:text-sm border border-border rounded-lg font-mono leading-relaxed resize-y"
                    style={{
                        overflowWrap: "anywhere",
                        wordBreak: "break-all",
                    }}
                    data-testid={`${testidPrefix}-textarea`}
                />
            ) : (
                /* MASKED PREVIEW — single-line, never overflows because the
                   masked string is at most ~22 chars. The placeholder shows
                   when the field is empty AND there is no existingMask. */
                <input
                    type="text"
                    value={value ? masked : ""}
                    onChange={() => { /* read-only mask, edit via textarea */ }}
                    onFocus={() => setShown(true)}
                    placeholder={existingMask
                        ? `محفوظ: ${existingMask} — اضغط 👁 لإدخال جديد`
                        : placeholder}
                    dir={direction}
                    disabled={disabled}
                    readOnly={!!value}
                    className="w-full max-w-full px-3 py-2.5 text-xs sm:text-sm border border-border rounded-lg font-mono bg-accent/30"
                    style={{
                        overflowWrap: "anywhere",
                        wordBreak: "break-all",
                    }}
                    data-testid={`${testidPrefix}-input-masked`}
                />
            )}

            <div className="mt-2 flex flex-wrap gap-1.5">
                <button
                    type="button"
                    onClick={() => setShown((s) => !s)}
                    disabled={disabled}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded border border-border hover:bg-accent transition-colors"
                    data-testid={`${testidPrefix}-toggle`}
                    title={shown ? "إخفاء وتقليص" : "عرض كامل للتعديل"}
                >
                    {shown
                        ? <><EyeSlash size={14} weight="bold" /> إخفاء</>
                        : <><Eye size={14} weight="bold" /> عرض</>
                    }
                </button>
                <button
                    type="button"
                    onClick={copyToClipboard}
                    disabled={disabled || !value}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded border border-border hover:bg-accent transition-colors disabled:opacity-50"
                    data-testid={`${testidPrefix}-copy`}
                    title="نسخ القيمة للحافظة"
                >
                    <Copy size={14} weight="bold" /> نسخ
                </button>
                <button
                    type="button"
                    onClick={clearField}
                    disabled={disabled || !value}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded border border-border hover:bg-red-50 hover:border-red-300 hover:text-red-700 transition-colors disabled:opacity-50"
                    data-testid={`${testidPrefix}-clear`}
                    title="مسح الحقل (لا يحذف القيمة المحفوظة على السيرفر)"
                >
                    <Trash size={14} weight="bold" /> مسح
                </button>
            </div>

            {helper && (
                <p className="text-xs text-muted-foreground mt-1.5 leading-relaxed">
                    {helper}
                </p>
            )}
        </div>
    );
}

/**
 * StatusBadge — green/amber/red pill used next to long-lived tokens.
 * Driven by the `connection_status` field returned by the backend.
 */
export function StatusBadge({ status, label }) {
    const map = {
        ok: { bg: "bg-emerald-100", text: "text-emerald-800", border: "border-emerald-300", emoji: "🟢", default: "صالح" },
        expiring_soon: { bg: "bg-amber-100", text: "text-amber-800", border: "border-amber-300", emoji: "🟡", default: "يحتاج تجديد قريباً" },
        expired: { bg: "bg-red-100", text: "text-red-800", border: "border-red-300", emoji: "🔴", default: "منتهي الصلاحية" },
        permission_denied: { bg: "bg-red-100", text: "text-red-800", border: "border-red-300", emoji: "🔴", default: "صلاحيات ناقصة" },
        invalid_account: { bg: "bg-red-100", text: "text-red-800", border: "border-red-300", emoji: "🔴", default: "حساب غير صالح" },
        rate_limited: { bg: "bg-amber-100", text: "text-amber-800", border: "border-amber-300", emoji: "🟡", default: "تم تجاوز الحد" },
        network_error: { bg: "bg-amber-100", text: "text-amber-800", border: "border-amber-300", emoji: "🟡", default: "خطأ شبكة" },
        error: { bg: "bg-red-100", text: "text-red-800", border: "border-red-300", emoji: "🔴", default: "خطأ" },
        unknown: { bg: "bg-gray-100", text: "text-gray-700", border: "border-gray-300", emoji: "⚪", default: "غير معروف" },
    };
    const s = map[status] || map.unknown;
    return (
        <span
            className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs font-bold rounded-full border ${s.bg} ${s.text} ${s.border}`}
            data-testid={`status-badge-${status || "unknown"}`}
        >
            {s.emoji} {label || s.default}
        </span>
    );
}
