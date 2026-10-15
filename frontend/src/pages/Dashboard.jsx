import { Navigate } from "react-router-dom";
import "./retiredDashboard.css";

/**
 * Frozen compatibility boundary for every retired dashboard surface.
 *
 * Both the legacy Mezan dashboard and the former Mezan 2 dashboard converge
 * immediately on the single supported advanced dashboard. The retired UI is
 * never rendered here, so none of its timers, requests, observers, or local
 * state can start from these compatibility routes.
 */
export default function RetiredDashboard() {
    return <Navigate to="/dashboard-advanced" replace />;
}
