import { useState, useEffect } from "react";
import { useLocation } from "react-router-dom";
import { List } from "@phosphor-icons/react";
import Sidebar from "./Sidebar";
import { Toaster } from "../components/ui/sonner";
import { LogoIcon } from "./MezanLogo";
import NotificationBell from "./NotificationBell";

export default function Layout({ children }) {
    const [mobileOpen, setMobileOpen] = useState(false);
    const location = useLocation();

    // Auto-close drawer on route change (mobile UX)
    useEffect(() => {
        setMobileOpen(false);
    }, [location.pathname]);

    // Lock body scroll when mobile drawer is open
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

            {/* Mobile top header — only visible on small screens */}
            <header
                className="lg:hidden sticky top-0 z-30 bg-white/95 backdrop-blur border-b border-border"
                data-testid="mobile-header"
            >
                <div className="flex items-center justify-between px-4 h-14">
                    <div className="flex items-center gap-2.5" data-testid="mobile-header-brand">
                        <LogoIcon size={32} />
                        <div>
                            <div className="text-brand text-base font-extrabold leading-tight tracking-wider" style={{ fontFamily: "Tajawal", letterSpacing: "0.08em" }}>
                                <span>MEZ</span><span className="text-accent-green">AN</span>
                            </div>
                            <div className="text-[10px] text-muted-foreground leading-tight font-bold">ميزان · تحليلات</div>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => setMobileOpen(true)}
                        className="inline-flex items-center justify-center w-10 h-10 rounded-lg border border-border text-foreground hover:bg-accent transition-colors"
                        data-testid="mobile-menu-btn"
                        aria-label="فتح القائمة"
                    >
                        <List size={22} weight="bold" />
                    </button>
                </div>
            </header>

            {/* Iter-159h — Floating Notification Bell.  Sits in the top-LEFT
                corner on desktop (the sidebar is on the right in RTL) and
                inside the mobile header bar.  Lives outside Sidebar so it
                follows the user across every page. */}
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

            <main className="lg:ps-64 min-h-screen" data-testid="main-content">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-10 py-4 sm:py-6 lg:py-8">
                    {children}
                </div>
            </main>
            <Toaster richColors position="top-center" />
        </div>
    );
}
