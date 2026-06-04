import { NavLink } from "react-router-dom";
import {
    House,
    UploadSimple,
    Gear,
    ClockCounterClockwise,
    Receipt,
    SignOut,
    ChartPieSlice,
    Truck,
    Plug,
    Wallet,
    Ghost,
    Package,
    Image,
    UserCircle,
    UsersThree,
    X,
} from "@phosphor-icons/react";
import { useAuth } from "../context/AuthContext";

const baseLinks = [
    { to: "/", label: "لوحة التحكم", icon: House, testid: "nav-dashboard" },
    { to: "/upload", label: "رفع ملف Excel", icon: UploadSimple, testid: "nav-upload" },
    { to: "/make-webhook", label: "ربط Make.com", icon: Plug, testid: "nav-make-webhook" },
    { to: "/history", label: "سجل التحليلات", icon: ClockCounterClockwise, testid: "nav-history" },
    { to: "/daily-costs", label: "التكاليف اليومية", icon: Receipt, testid: "nav-daily-costs" },
    { to: "/operating-expenses", label: "المصروفات التشغيلية", icon: Wallet, testid: "nav-operating-expenses" },
    { to: "/reports", label: "التقارير", icon: ChartPieSlice, testid: "nav-reports" },
    { to: "/snapchat-accounts", label: "حسابات Snapchat", icon: Ghost, testid: "nav-snapchat-accounts" },
    { to: "/product-costs", label: "تكاليف المنتجات", icon: Package, testid: "nav-product-costs" },
    { to: "/product-preparation", label: "تجهيز المنتجات", icon: Package, testid: "nav-product-preparation" },
    { to: "/image-catalog", label: "إدارة صور المنتجات", icon: Image, testid: "nav-image-catalog" },
    { to: "/shipping-accounts", label: "حسابات الشحن الآجلة", icon: Truck, testid: "nav-shipping-accounts" },
    { to: "/settlements", label: "تسويات المدفوعات", icon: Receipt, testid: "nav-settlements" },
    { to: "/profile", label: "حسابي", icon: UserCircle, testid: "nav-profile" },
];

const ownerLink = { to: "/team", label: "إدارة الفريق", icon: UsersThree, testid: "nav-team" };
const settingsLink = { to: "/settings", label: "الإعدادات", icon: Gear, testid: "nav-settings" };

export default function Sidebar({ mobileOpen = false, onMobileClose = () => {} }) {
    const { user, logout } = useAuth();

    const onLogout = async () => {
        await logout();
    };

    const links = [
        ...baseLinks,
        ...(user?.is_owner ? [ownerLink] : []),
        settingsLink,
    ];

    return (
        <>
            {/* Mobile backdrop overlay */}
            {mobileOpen && (
                <div
                    className="fixed inset-0 bg-black/50 z-40 lg:hidden"
                    onClick={onMobileClose}
                    data-testid="sidebar-backdrop"
                    aria-hidden="true"
                />
            )}

            <aside
                className={[
                    "fixed top-0 right-0 h-screen w-64 bg-white border-l border-border flex flex-col z-50",
                    "transition-transform duration-300 ease-out",
                    // Desktop: always visible
                    "lg:translate-x-0",
                    // Mobile: slide in from right when open, hide off-screen otherwise
                    mobileOpen ? "translate-x-0" : "translate-x-full lg:translate-x-0",
                ].join(" ")}
                data-testid="sidebar"
                aria-label="القائمة الجانبية"
            >
                {/* Brand + Mobile close */}
                <div className="px-6 py-5 border-b border-border flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-xl bg-brand flex items-center justify-center">
                            <span className="text-white text-xl font-black">ح</span>
                        </div>
                        <div>
                            <div className="text-brand text-xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>
                                حساب
                            </div>
                            <div className="text-xs text-muted-foreground">محاسبة سلة</div>
                        </div>
                    </div>
                    {/* Close button — mobile only */}
                    <button
                        type="button"
                        onClick={onMobileClose}
                        className="lg:hidden p-1.5 rounded-md hover:bg-accent text-muted-foreground"
                        data-testid="sidebar-close-btn"
                        aria-label="إغلاق القائمة"
                    >
                        <X size={22} weight="bold" />
                    </button>
                </div>

                {/* Nav */}
                <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-1 scrollbar-thin">
                    {links.map(({ to, label, icon: Icon, testid }) => (
                        <NavLink
                            key={to}
                            to={to}
                            end={to === "/"}
                            onClick={onMobileClose}
                            data-testid={testid}
                            className={({ isActive }) =>
                                [
                                    "flex items-center gap-3 px-4 py-3 rounded-lg text-[15px] transition-colors",
                                    isActive
                                        ? "bg-brand text-white font-semibold"
                                        : "text-foreground hover:bg-accent hover:text-brand",
                                ].join(" ")
                            }
                        >
                            <Icon size={20} weight="duotone" />
                            <span>{label}</span>
                        </NavLink>
                    ))}
                </nav>

                {/* User block */}
                <div className="border-t border-border p-4">
                    <div className="flex items-center gap-3 mb-3">
                        <div className="w-10 h-10 rounded-full bg-accent flex items-center justify-center text-brand font-bold">
                            {(user?.name || user?.email || "ض").slice(0, 1).toUpperCase()}
                        </div>
                        <div className="flex-1 min-w-0">
                            <div className="text-sm font-semibold truncate">{user?.name || "مستخدم"}</div>
                            <div className="text-xs text-muted-foreground truncate">{user?.email}</div>
                        </div>
                    </div>
                    <button
                        onClick={onLogout}
                        data-testid="logout-btn"
                        className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-border text-sm font-medium hover:bg-accent transition-colors"
                    >
                        <SignOut size={18} />
                        تسجيل الخروج
                    </button>
                </div>
            </aside>
        </>
    );
}
