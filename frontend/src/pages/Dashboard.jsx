import { Navigate } from "react-router-dom";
import "./retiredDashboard.css";

/**
 * Compatibility boundary for retired dashboard URLs.
 *
 * Both `/legacy-dashboard` and `/dashboard-v2` still import this component
 * so old bookmarks remain safe, but the former dashboard implementation and
 * all of its polling/API side effects are intentionally removed.  Every visit
 * converges on the single supported dashboard.
 */
export default function RetiredDashboard() {
    return <Navigate to="/dashboard-advanced" replace />;
}
