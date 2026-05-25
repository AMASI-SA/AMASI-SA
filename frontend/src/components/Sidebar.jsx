import { NavLink, useNavigate } from "react-router-dom";
import {
    House,
    UploadSimple,
    Gear,
    ClockCounterClockwise,
    Receipt,
    SignOut,
    ChartPieSlice,
} from "@phosphor-icons/react";
import { useAuth } from "../context/AuthContext";

const links = [
    { to: "/", label: "لوحة التحكم", icon: House, testid: "nav-dashboard" },
    { to: "/upload", label: "رفع ملف Excel", icon: UploadSimple, testid: "nav-upload" },
    { to: "/history", label: "سجل التحليلات", icon: ClockCounterClockwise, testid: "nav-history" },
    { to: "/daily-costs", label: "التكاليف اليومية", icon: Receipt, testid: "nav-daily-costs" },
    { to: "/reports", label: "التقارير", icon: ChartPieSlice, testid: "nav-reports" },
    { to: "/settings", label: "الإعدادات", icon: Gear, testid: "nav-settings" },
];

export default function Sidebar() {
    const { user, logout } = useAuth();
    const navigate = useNavigate();

    const onLogout = async () => {
        await logout();
        navigate("/login");
    };

    return (
        <aside
            className="fixed top-0 right-0 h-screen w-64 bg-white border-l border-border flex flex-col z-30"
            data-testid="sidebar"
        >
            {/* Brand */}
            <div className="px-6 py-7 border-b border-border">
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
            </div>

            {/* Nav */}
            <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-1 scrollbar-thin">
                {links.map(({ to, label, icon: Icon, testid }) => (
                    <NavLink
                        key={to}
                        to={to}
                        end={to === "/"}
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
    );
}
