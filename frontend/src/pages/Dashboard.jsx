import { Navigate } from "react-router-dom";
import AdvancedDashboard from "./AdvancedDashboard";
import "./retiredDashboard.css";

/**
 * Compatibility boundary for the retired dashboard implementation.
 *
 * `/dashboard-v2` remains the Mezan 2 home route, but it now renders the
 * advanced dashboard directly inside the Mezan 2 navigation shell. The older
 * `/legacy-dashboard` bookmark redirects to the supported advanced route.
 * The former dashboard implementation and all of its polling/API side effects
 * are intentionally removed.
 */
export default function RetiredDashboard({ sourceMode = "legacy" }) {
    if (sourceMode === "mezan_v2") return <AdvancedDashboard />;
    return <Navigate to="/dashboard-advanced" replace />;
}
