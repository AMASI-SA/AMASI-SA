import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export function isAccountingWorkspaceLocation(location) {
    return location?.pathname === "/integrations-v2"
        && new URLSearchParams(location?.search || "").get("workspace") === "financial";
}

export default function OwnerOnlyRoute({ children }) {
    const { user, loading } = useAuth();
    const location = useLocation();

    if (loading) {
        return (
            <div
                className="min-h-[50vh] flex items-center justify-center"
                data-testid="orders-v2-owner-loading"
            >
                <div className="text-brand font-bold">جاري التحقق من الصلاحية…</div>
            </div>
        );
    }

    if (!user) {
        return <Navigate to="/login" state={{ from: location }} replace />;
    }

    const reviewerAllowedPaths = new Set([
        "/integrations-v2",
        "/integrations-v2/instagram",
        "/ads-manager",
    ]);
    const metaReviewerAllowed = user.role === "meta_reviewer"
        && reviewerAllowedPaths.has(location.pathname);
    // Only the accounting workspace may pass this outer owner gate.  The
    // unified AccountingWorkspace then enforces its independent per-page
    // permissions from the backend. Other integrations remain owner-only.
    const accountingWorkspaceAllowed = isAccountingWorkspaceLocation(location);

    if (!user.is_owner && !metaReviewerAllowed && !accountingWorkspaceAllowed) {
        return (
            <div
                className="max-w-2xl mx-auto mt-12 rounded-2xl border border-amber-200 bg-amber-50 p-8 text-center"
                data-testid="orders-v2-owner-denied"
            >
                <div className="text-4xl mb-3">🔒</div>
                <h1 className="text-xl font-extrabold text-amber-950">
                    صفحة إدارية خاصة بالمالك
                </h1>
                <p className="mt-2 text-sm text-amber-800">
                    ستتاح لاحقًا للموظفين حسب صلاحيات مستقلة للطلبات والتجهيز
                    والمحاسبة والربحية.
                </p>
            </div>
        );
    }

    return children;
}
