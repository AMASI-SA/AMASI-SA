import { Cube, Package, Receipt, SignOut, SquaresFour, House, Queue, UsersThree, Buildings, Wallet, Megaphone, Plug, Robot, Truck } from "@phosphor-icons/react";
import { Link, useLocation } from "react-router-dom";
import { useOptionalAuth } from "../context/AuthContext";
import { webPageSections } from "../lib/webPageAccess";
import NavigationShell from "./MezanV2NavigationShellLegacy";

const groupIcons = {
    home: House, products: Cube, orders: Package, fulfillment: Queue, employees: UsersThree,
    suppliers: Buildings, finance: Wallet, accounting: Receipt, balances: Wallet, marketing: Megaphone,
    apps: Plug, intelligence: Robot, shipping: Truck,
};

export default function EmployeeWebLayout({ pages, children }) {
    const location = useLocation();
    const { logout } = useOptionalAuth() || {};
    const sections = webPageSections(pages).map(({ id, label, pages: items }) => ({
        id, label, Icon: groupIcons[id] || SquaresFour,
        items: items.map(({ to, label: text }) => ({ to, label: text })),
    }));

    return (
        <div className="min-h-screen bg-background" dir="rtl" data-testid="employee-web-layout">
            <header className="sticky top-0 z-40 border-b bg-background sm:px-4 sm:py-2" data-testid="mezan-v2-unified-header">
                <NavigationShell
                    location={location}
                    restrictedSections={sections}
                    notificationControl={logout && (
                        <button type="button" onClick={logout} className="rounded-xl px-3 py-2 text-sm font-bold text-white">
                            <SignOut className="ml-1 inline" /> تسجيل الخروج
                        </button>
                    )}
                />
            </header>
            <main className="mx-auto max-w-[1600px] px-4 py-4 sm:px-6" data-testid="main-content">
                {children}
            </main>
        </div>
    );
}

export function EmployeePageUnavailable({ pages }) {
    return (
        <EmployeeWebLayout pages={pages}>
            <section className="mx-auto max-w-2xl rounded-2xl border bg-white p-6 text-center">
                <h1 className="text-xl font-extrabold">
                    {pages.length ? "هذه الصفحة غير متاحة لحسابك" : "لم تُمنح صفحات لهذا الحساب بعد"}
                </h1>
                <p className="mt-2 text-sm text-slate-600">يمكن لمالك المتجر تحديد صفحات ميزان المسموحة لك.</p>
                {pages.length > 0 && (
                    <Link to={pages[0].to} className="mt-4 inline-block rounded-xl bg-emerald-700 px-4 py-2 font-bold text-white">
                        {pages[0].label}
                    </Link>
                )}
            </section>
        </EmployeeWebLayout>
    );
}
