import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
    MagnifyingGlass,
    Package,
    Receipt,
} from "@phosphor-icons/react";
import { useAuth } from "../context/AuthContext";
import { MEZAN_V2_NAV_SECTIONS } from "./MezanV2NavigationShell";

const TASHKEEL_RE = /[\u064B-\u0652\u0670\u0640]/g;

export function normalizeGlobalSearch(value) {
    return String(value || "")
        .toLowerCase()
        .replace(TASHKEEL_RE, "")
        .replace(/[أإآا]/g, "ا")
        .replace(/ة/g, "ه")
        .replace(/ى/g, "ي")
        .replace(/[^\u0600-\u06ffa-z0-9]+/g, " ")
        .trim();
}

const NAVIGATION_PAGES = MEZAN_V2_NAV_SECTIONS.flatMap((section) => (
    section.items.map((item) => ({
        type: "page",
        to: item.to,
        label: item.label,
        section: section.label,
        Icon: section.Icon,
        ownerOnly: true,
        keywords: [section.label, item.label].join(" "),
    }))
));

const QOYOD_PAGES = [
    {
        type: "page",
        to: "/integrations-v2/qoyod?tab=status",
        label: "قيود — الحالة",
        section: "التطبيقات والتكاملات",
        Icon: Receipt,
        ownerOnly: true,
        keywords: "قيود محاسبة فواتير ارسال تلقائي حالة الربط",
    },
    {
        type: "page",
        to: "/integrations-v2/qoyod?tab=exceptions",
        label: "قيود — الاستثناءات",
        section: "التطبيقات والتكاملات",
        Icon: Receipt,
        ownerOnly: true,
        keywords: "قيود طلبات لم ترسل اخطاء فشل استثناءات",
    },
    {
        type: "page",
        to: "/integrations-v2/qoyod?tab=reconciliation",
        label: "قيود — المطابقة",
        section: "التطبيقات والتكاملات",
        Icon: Receipt,
        ownerOnly: true,
        keywords: "قيود مطابقة ميزان فواتير فروقات مبالغ",
    },
    {
        type: "page",
        to: "/integrations-v2/qoyod?tab=settings",
        label: "قيود — الإعدادات",
        section: "التطبيقات والتكاملات",
        Icon: Receipt,
        ownerOnly: true,
        keywords: "قيود اعدادات مفتاح api حسابات ضريبة وسائل الدفع",
    },
];

export const MEZAN_V2_SEARCH_PAGES = [
    ...NAVIGATION_PAGES,
    ...QOYOD_PAGES,
];

function pageRank(page, normalizedQuery) {
    const label = normalizeGlobalSearch(page.label);
    const section = normalizeGlobalSearch(page.section);
    const haystack = normalizeGlobalSearch(
        [page.label, page.section, page.keywords].filter(Boolean).join(" "),
    );
    const words = normalizedQuery.split(/\s+/).filter(Boolean);
    if (!words.every((word) => haystack.includes(word))) return null;
    if (label === normalizedQuery) return 0;
    if (label.startsWith(normalizedQuery)) return 1;
    if (label.includes(normalizedQuery)) return 2;
    if (section.includes(normalizedQuery)) return 3;
    return 4;
}

export function buildGlobalSearchResults(query, { isOwner = false } = {}) {
    const rawQuery = String(query || "").trim();
    const normalizedQuery = normalizeGlobalSearch(rawQuery);
    if (!normalizedQuery) return [];

    const results = [];
    const orderNumber = rawQuery.replace(/^#/, "").trim();
    if (/^\d{5,}$/.test(orderNumber)) {
        results.push({
            type: "order",
            to: `/orders-v2/${encodeURIComponent(orderNumber)}`,
            label: `فتح الطلب #${orderNumber}`,
            section: "الطلبات",
            Icon: Package,
            orderNumber,
        });
    }

    const pages = MEZAN_V2_SEARCH_PAGES
        .filter((page) => !page.ownerOnly || isOwner)
        .map((page) => ({ page, rank: pageRank(page, normalizedQuery) }))
        .filter(({ rank }) => rank !== null)
        .sort((a, b) => a.rank - b.rank || a.page.label.localeCompare(b.page.label, "ar"))
        .slice(0, 12)
        .map(({ page }) => page);

    return [...results, ...pages];
}

function ResultRow({ result, active, onChoose, index }) {
    const Icon = result.Icon || MagnifyingGlass;
    return (
        <button
            type="button"
            role="option"
            aria-selected={active}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => onChoose(result)}
            className={[
                "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-right transition",
                active
                    ? "bg-emerald-50 text-emerald-950 ring-1 ring-emerald-200"
                    : "text-slate-800 hover:bg-slate-50",
            ].join(" ")}
            data-testid={`global-search-result-${index}`}
        >
            <span className={[
                "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
                result.type === "order"
                    ? "bg-violet-100 text-violet-700"
                    : "bg-emerald-100 text-emerald-700",
            ].join(" ")}>
                <Icon size={20} weight="duotone" />
            </span>
            <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-extrabold">
                    {result.label}
                </span>
                <span className="block truncate text-[11px] font-bold text-slate-500">
                    {result.type === "order" ? "طلب" : result.section}
                </span>
            </span>
            <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-bold text-slate-500">
                {result.type === "order" ? "طلب" : "صفحة"}
            </span>
        </button>
    );
}

export default function GlobalSearch({ compact = false }) {
    const navigate = useNavigate();
    const { user } = useAuth();
    const rootRef = useRef(null);
    const inputRef = useRef(null);
    const [query, setQuery] = useState("");
    const [focused, setFocused] = useState(false);
    const [activeIndex, setActiveIndex] = useState(0);

    const results = useMemo(
        () => buildGlobalSearchResults(query, { isOwner: Boolean(user?.is_owner) }),
        [query, user?.is_owner],
    );
    const resultsOpen = focused && query.trim().length > 0;

    useEffect(() => {
        setActiveIndex(0);
    }, [query]);

    useEffect(() => {
        const closeOnOutside = (event) => {
            if (rootRef.current && !rootRef.current.contains(event.target)) {
                setFocused(false);
            }
        };
        document.addEventListener("mousedown", closeOnOutside);
        return () => document.removeEventListener("mousedown", closeOnOutside);
    }, []);

    const choose = (result) => {
        if (!result?.to) return;
        navigate(result.to);
        setQuery("");
        setFocused(false);
    };

    const submit = (event) => {
        event.preventDefault();
        if (results.length > 0) choose(results[activeIndex] || results[0]);
    };

    const onKeyDown = (event) => {
        if (!resultsOpen) return;
        if (event.key === "ArrowDown") {
            event.preventDefault();
            setActiveIndex((index) => Math.min(index + 1, results.length - 1));
        } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActiveIndex((index) => Math.max(index - 1, 0));
        } else if (event.key === "Escape") {
            event.preventDefault();
            setFocused(false);
            inputRef.current?.blur();
        }
    };

    return (
        <div ref={rootRef} className="relative w-full" data-testid="global-search">
            <form
                onSubmit={submit}
                className={`flex items-center overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm ${compact ? "h-10 w-full" : "h-12 w-full max-w-2xl"}`}
                role="search"
                aria-label="البحث العام في الطلبات والصفحات"
                data-testid="global-order-search"
            >
                <div className="relative min-w-0 flex-1">
                    <MagnifyingGlass
                        size={compact ? 18 : 21}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400"
                    />
                    <input
                        ref={inputRef}
                        value={query}
                        onFocus={() => setFocused(true)}
                        onChange={(event) => {
                            setQuery(event.target.value);
                            setFocused(true);
                        }}
                        onKeyDown={onKeyDown}
                        placeholder="ابحث برقم الطلب أو اسم الصفحة…"
                        autoComplete="off"
                        className="h-full w-full bg-transparent pr-10 pl-3 text-sm text-slate-800 outline-none placeholder:text-slate-400"
                        data-testid="global-search-input"
                        aria-autocomplete="list"
                        aria-controls="global-search-results"
                        aria-expanded={resultsOpen}
                    />
                </div>
                <button
                    type="submit"
                    className={`inline-flex h-full shrink-0 items-center justify-center bg-violet-700 px-4 font-bold text-white transition hover:bg-violet-800 ${compact ? "text-xs" : "text-sm"}`}
                    data-testid="global-search-submit"
                >
                    بحث
                </button>
            </form>

            {resultsOpen && (
                <div
                    id="global-search-results"
                    role="listbox"
                    className="absolute inset-x-0 top-[calc(100%+0.5rem)] z-[100] max-h-[min(26rem,65vh)] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-2 shadow-2xl"
                    data-testid="global-search-results"
                >
                    {results.length > 0 ? (
                        <>
                            <div className="px-3 pb-1 pt-1 text-[11px] font-extrabold text-slate-500">
                                نتائج البحث
                            </div>
                            <div className="space-y-1">
                                {results.map((result, index) => (
                                    <ResultRow
                                        key={`${result.type}:${result.to}`}
                                        result={result}
                                        index={index}
                                        active={index === activeIndex}
                                        onChoose={choose}
                                    />
                                ))}
                            </div>
                            <div className="mt-2 border-t border-slate-100 px-3 pt-2 text-[10px] font-bold text-slate-400">
                                استخدم الأسهم للتنقل و Enter للفتح
                            </div>
                        </>
                    ) : (
                        <div className="px-4 py-6 text-center">
                            <MagnifyingGlass
                                size={26}
                                className="mx-auto mb-2 text-slate-300"
                            />
                            <p className="text-sm font-extrabold text-slate-700">
                                لا توجد نتائج
                            </p>
                            <p className="mt-1 text-xs font-bold text-slate-400">
                                جرّب رقم طلب أو اسم صفحة أخرى
                            </p>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
