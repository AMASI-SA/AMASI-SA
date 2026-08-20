import { useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import AmasiDeliveryApp from "../pages/AmasiDeliveryApp";
import AuthLoadingScreen from "./AuthLoadingScreen";
import AuthRecoveryScreen from "./AuthRecoveryScreen";

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
    return children;
}
