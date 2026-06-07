/**
 * MEZAN Brand Logo (Iter-84)
 * --------------------------
 * Two reusable variants:
 *   • <LogoIcon size={...} /> — square mark (favicon, sidebar avatar, app icons)
 *   • <LogoFull size={...} /> — mark + "MEZAN" wordmark + "ميزان" Arabic + tagline
 *
 * Design: geometric M built from a balance-scale crossbar (gold)
 * suspending two pans, with ascending bar-charts forming the M's
 * vertical strokes. Deep green primary (#0F5D46) + gold accent (#D4A017)
 * + vibrant green growth (#16A34A).
 */

export function LogoIcon({
    size = 40,
    primary = "#0F5D46",
    accent = "#16A34A",
    gold = "#D4A017",
    rounded = true,
    className = "",
}) {
    return (
        <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 64 64"
            width={size}
            height={size}
            className={className}
            aria-label="MEZAN logo"
            role="img"
            data-testid="mezan-logo-icon"
        >
            {/* Tile background */}
            <rect
                x="0"
                y="0"
                width="64"
                height="64"
                rx={rounded ? 14 : 0}
                fill={primary}
            />

            {/* Subtle inner glow */}
            <rect
                x="2"
                y="2"
                width="60"
                height="60"
                rx={rounded ? 12 : 0}
                fill="none"
                stroke="rgba(255,255,255,0.06)"
                strokeWidth="1"
            />

            {/* Balance-scale crossbar (top of the M) */}
            <line x1="14" y1="18" x2="50" y2="18" stroke={gold} strokeWidth="2.5" strokeLinecap="round" />
            {/* Center fulcrum */}
            <circle cx="32" cy="18" r="2.6" fill={gold} />
            <line x1="32" y1="13.5" x2="32" y2="18" stroke={gold} strokeWidth="2" strokeLinecap="round" />

            {/* Left scale pan (small) */}
            <path
                d="M9.5 18 L18.5 18 A4.5 4.5 0 0 1 9.5 18 Z"
                fill={gold}
                opacity="0.95"
            />
            {/* Right scale pan (small) */}
            <path
                d="M45.5 18 L54.5 18 A4.5 4.5 0 0 1 45.5 18 Z"
                fill={gold}
                opacity="0.95"
            />

            {/* Ascending growth bars forming the M's body */}
            {/* Bar 1 — short (left leg) */}
            <rect x="13" y="38" width="6"  height="14" rx="1.5" fill="#FFFFFF" opacity="0.92" />
            {/* Bar 2 — medium */}
            <rect x="22" y="32" width="6"  height="20" rx="1.5" fill={accent} />
            {/* Bar 3 — taller (center) */}
            <rect x="31" y="26" width="6"  height="26" rx="1.5" fill={accent} />
            {/* Bar 4 — tallest */}
            <rect x="40" y="22" width="6"  height="30" rx="1.5" fill={accent} />
            {/* Bar 5 — tallest highlighted (right leg) */}
            <rect x="49" y="38" width="6" height="14" rx="1.5" fill="#FFFFFF" opacity="0.92" />

            {/* Baseline */}
            <line x1="10" y1="54" x2="54" y2="54" stroke={gold} strokeWidth="1.5" strokeLinecap="round" opacity="0.8" />
        </svg>
    );
}

export function LogoFull({
    height = 48,
    primary = "#0F5D46",
    accent = "#16A34A",
    gold = "#D4A017",
    text = "#111827",
    showTagline = true,
    className = "",
}) {
    return (
        <div
            className={`inline-flex items-center gap-3 ${className}`}
            dir="rtl"
            data-testid="mezan-logo-full"
        >
            <LogoIcon size={height} primary={primary} accent={accent} gold={gold} />
            <div className="flex flex-col items-start leading-tight">
                <div
                    className="font-extrabold tracking-wider"
                    style={{
                        fontFamily: "'Tajawal', 'Segoe UI', system-ui, sans-serif",
                        fontSize: `${height * 0.42}px`,
                        color: text,
                        letterSpacing: "0.08em",
                    }}
                >
                    <span style={{ color: primary }}>MEZ</span>
                    <span style={{ color: accent }}>AN</span>
                </div>
                <div
                    className="font-bold"
                    style={{
                        fontFamily: "'Tajawal', 'Segoe UI', system-ui, sans-serif",
                        fontSize: `${height * 0.32}px`,
                        color: text,
                        borderTop: `2px solid ${gold}`,
                        paddingTop: "2px",
                        marginTop: "2px",
                    }}
                >
                    ميزان
                </div>
                {showTagline && (
                    <div
                        className="text-muted-foreground"
                        style={{
                            fontFamily: "'Tajawal', 'Segoe UI', system-ui, sans-serif",
                            fontSize: `${Math.max(10, height * 0.2)}px`,
                            marginTop: "2px",
                        }}
                    >
                        منصة التحليلات والمحاسبة للتجارة الإلكترونية
                    </div>
                )}
            </div>
        </div>
    );
}

export default LogoFull;
