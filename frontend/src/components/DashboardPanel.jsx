import { Children, cloneElement, useId, useState } from "react";
import { ChevronDown } from "lucide-react";
import "./dashboardPanel.css";

/** The first child is the existing card header; the remaining children form its body. */
export default function DashboardPanel({ children, title, className = "", ...props }) {
    const bodyId = useId();
    const [collapsed, setCollapsed] = useState(false);
    const [header, ...body] = Children.toArray(children);

    // Other screens can keep using the same card without dashboard controls.
    if (!title) return <section className={className} {...props}>{children}</section>;

    return <section className={`dashboard-panel min-w-0 max-w-full ${className}`} {...props}>
        {cloneElement(header, {
            className: `${header.props.className || ""} dashboard-panel-header`,
        }, <>
            {header.props.children}
            <button
                type="button"
                className="dashboard-panel-toggle"
                aria-label={`${collapsed ? "فتح" : "طي"} بطاقة ${title}`}
                title={`${collapsed ? "فتح" : "طي"} بطاقة ${title}`}
                aria-expanded={!collapsed}
                aria-controls={bodyId}
                onClick={() => setCollapsed((value) => !value)}
            >
                <ChevronDown aria-hidden="true" className={`h-4 w-4 ${collapsed ? "" : "rotate-180"}`} />
            </button>
        </>)}
        <div id={bodyId} className="dashboard-panel-content" hidden={collapsed}>
            {body}
        </div>
    </section>;
}
