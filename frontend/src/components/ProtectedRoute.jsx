import { lazy, Suspense, useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import AmasiDeliveryApp from "../pages/AmasiDeliveryApp";
import AuthLoadingScreen from "./AuthLoadingScreen";
import AuthRecoveryScreen from "./AuthRecoveryScreen";
import EmployeeWebLayout, { EmployeePageUnavailable } from "./EmployeeWebLayout";
import { grantedPageAt, managedWebPages } from "../lib/webPageAccess";

const EmployeeBalanceSpecialist = lazy(() => import("../pages/operational/EmployeeBalanceSpecialist"));
const balanceReports = new Set(["balances.returns", "balances.exchanges", "balances.fees", "balances.reports", "balances.specialized", "balances.access"]);
const EmployeeAdditionalPage = lazy(() => import("../pages/EmployeeAdditionalPage"));
const EmployeeDetailPage = lazy(() => import("../pages/EmployeeDetailPage"));
const EmployeeMissingCosts = lazy(() => import("../pages/EmployeeMissingCosts"));
const EmployeeLegacyRead = lazy(() => import("../pages/EmployeeLegacyRead"));
const EmployeeMarketingRead = lazy(() => import("../pages/EmployeeMarketingRead"));
const EmployeeOperationsReadPage = lazy(() => import("../pages/EmployeeOperationsReadPage"));
const operationsReports = new Set(["employees.staff", "employees.drivers", "employees.migration", "finance.collections", "finance.settlements", "shipping.settings"]);

export default function ProtectedRoute({ children }) {
    const { user, loading, authStatus, retryAuth } = useAuth();
    const location = useLocation();

    useEffect(() => {
        if (user?.role !== "store_driver") return undefined;
        const previousTitle = document.title;
        document.title = "توصيل أماسي";
        let manifest = document.querySelector('link[data-amas-delivery-manifest="true"]');
        if (!manifest) {
            manifest = document.createElement("link");
            manifest.rel = "manifest";
            manifest.href = "/amas-delivery-manifest.webmanifest";
            manifest.dataset.amasDeliveryManifest = "true";
            document.head.appendChild(manifest);
        }
        let theme = document.querySelector('meta[data-amas-delivery-theme="true"]');
        if (!theme) {
            theme = document.createElement("meta");
            theme.name = "theme-color";
            theme.content = "#047857";
            theme.dataset.amasDeliveryTheme = "true";
            document.head.appendChild(theme);
        }
        return () => {
            document.title = previousTitle;
            manifest?.remove();
            theme?.remove();
        };
    }, [user?.role]);

    if (authStatus === "unavailable") return <AuthRecoveryScreen onRetry={retryAuth} />;
    if (loading) return <AuthLoadingScreen />;
    if (!user) return <Navigate to="/login" state={{ from: location }} replace />;

    // Purpose-bound courier accounts never render Mezan administrative UI.
    // The standalone delivery workspace is returned directly regardless of the
    // URL the courier tries to open, while backend role checks remain the final
    // authorization boundary.
    if (user.role === "store_driver") return <AmasiDeliveryApp />;

    if (user.role === "meta_reviewer") {
        const allowed = [
            "/integrations-v2",
            "/integrations-v2/instagram",
            "/customer-intelligence",
            "/ads-manager",
        ];
        if (!allowed.includes(location.pathname)) {
            return <Navigate to="/integrations-v2?provider=meta_ads" replace />;
        }
    }
    const pages = managedWebPages(user);
    if (pages) {
        // Existing bookmarks of the retired all-in-one screen go through the
        // approved home entry and its ordinary grant check.
        if (location.pathname === "/operational-balances" && location.search === "?view=previous-workspace") {
            return <Navigate to="/operational-balances" replace />;
        }
        const page = grantedPageAt(pages, location);
        if (!page && pages.length > 0 && ["/", "/dashboard-advanced"].includes(location.pathname) && !location.search) {
            return <Navigate to={pages[0].to} replace />;
        }
        if (!page) return <EmployeePageUnavailable pages={pages} />;
        const Reader = page.review_component === "legacy_read" ? EmployeeLegacyRead
            : page.review_component === "extra_read" ? EmployeeAdditionalPage
            : page.review_component === "detail_read" ? EmployeeDetailPage
            : page.review_component === "missing_costs" ? EmployeeMissingCosts
            : page.review_component === "marketing_read" ? EmployeeMarketingRead
            : balanceReports.has(page.id) ? EmployeeBalanceSpecialist
            : operationsReports.has(page.id) ? EmployeeOperationsReadPage : null;
        if (Reader) {
            return <EmployeeWebLayout pages={pages}><Suspense fallback={<p role="status">جارٍ فتح الصفحة…</p>}>
                <Reader key={JSON.stringify([user.id, user.created_by, page.id, page.mode])} pageId={page.id} page={page} search={location.search} />
            </Suspense></EmployeeWebLayout>;
        }
    }
    return children;
}
