import { useState, useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { List, MagnifyingGlass } from "@phosphor-icons/react";
import Sidebar from "./Sidebar";
import { Toaster } from "../components/ui/sonner";
import { LogoIcon } from "./MezanLogo";
import NotificationBell from "./NotificationBell";
import OrderUiEnhancements from "./OrderUiEnhancements";

function GlobalOrderSearch({ compact = false }) {
    const navigate = useNavigate();
    const [orderNumber, setOrderNumber] = useState("");

    function submit(event) {
        event.preventDefault();
        const normalized = String(orderNumber || "")
            .replace(/^#/, "")
            .trim();

        if (!normalized) return;

        navigate(`/orders-v2/${encodeURIComponent(normalized)}`);
        setOrderNumber("");
    }

    return (
        <form
            onSubmit={submit}
            className={`flex items-center overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm ${
                compact ? "h-10 w-full" : "h-12 w-full max-w-2xl"
            }`}
            role="search"
            aria-label="البحث العام عن طلب"
            data-testid="global-order-search"
        >
            <div className="relative min-w-0 flex-1">
                <MagnifyingGlass
                    size={compact ? 18 : 21}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400"
                />
                <input
                    value={orderNumber}
                    onChange={(event) => setOrderNumber(event.target.value)}
                    inputMode="numeric"
                    placeholder="ابحث برقم الطلب من أي صفحة…"
                    className="h-full w-full bg-transparent pr-10 pl-3 text-sm text-slate-800 outline-none placeholder:text-slate-400"
                />
            </div>
            <button
                type="submit"
                className={`inline-flex h-full shrink-0 items-center justify-center bg-violet-700 px-4 font-bold text-white transition hover:bg-violet-800 ${
                    compact ? "text-xs" : "text-sm"
                }`}
            >
                بحث
            </button>
        </form>
    );
}

export default function Layout({ children }) {
    const [mobileOpen, setMobileOpen] = useState(false);
    const location = useLocation();

    useEffect(() => {
        setMobileOpen(false);
    }, [location.pathname]);

    useEffect(() => {
        if (mobileOpen) {
            document.body.style.overflow = "hidden";
        } else {
            document.body.style.overflow = "";
        }
        return () => {
            document.body.style.overflow = "";
        };
    }, [mobileOpen]);

    return (
        <div className="min-h-screen bg-background grain">
            <Sidebar mobileOpen={mobileOpen} onMobileClose={() => setMobileOpen(false)} />
            <OrderUiEnhancements />

            <header
                className="lg:hidden sticky top-0 z-30 border-b border-border bg-white/95 backdrop-blur"
                data-testid="mobile-header"
            >
                <div className="flex h-14 items-center justify-between px-4">
                    <div className="flex items-center gap-2.5" data-testid="mobile-header-brand">
                        <LogoIcon size={32} />
                        <div>
                            <div className="text-brand text-base font-extrabold leading-tight tracking-wider" style={{ fontFamily: "Tajawal", letterSpacing: "0.08em" }}>
                                <span>MEZ</span><span className="text-accent-green">AN</span>
                            </div>
                            <div className="text-[10px] font-bold leading-tight text-muted-foreground">ميزان · تحليلات</div>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => setMobileOpen(true)}
                        className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-border text-foreground transition-colors hover:bg-accent"
                        data-testid="mobile-menu-btn"
                        aria-label="فتح القائمة"
                    >
                        <List size={22} weight="bold" />
                    </button>
                </div>
                <div className="px-4 pb-3">
                    <GlobalOrderSearch compact />
                </div>
            </header>

            <div
                className="fixed top-3 end-3 z-40 hidden lg:block"
                data-testid="desktop-notification-bell-wrap"
            >
                <NotificationBell />
            </div>
            <div
                className="lg:hidden fixed top-2 end-16 z-40"
                data-testid="mobile-notification-bell-wrap"
            >
                <NotificationBell />
            </div>

            <main className="min-h-screen lg:ps-64" data-testid="main-content">
                <div className="sticky top-0 z-20 hidden border-b border-slate-200 bg-white/95 px-6 py-3 backdrop-blur lg:flex lg:items-center lg:justify-center">
                    <GlobalOrderSearch />
                </div>
                <div className="mx-auto max-w-7xl px-4 py-4 sm:px-6 sm:py-6 lg:px-10 lg:py-8">
                    {children}
                </div>
            </main>
            <Toaster richColors position="top-center" />
        </div>
    );
}
