import "./dashboardPanel.css";

export default function DashboardTableScroll({ children, label }) {
    return <>
        <p className="dashboard-table-hint">اسحب الجدول يمينًا ويسارًا لعرض جميع الأعمدة</p>
        <div className="dashboard-table-scroll overflow-x-auto" role="region" aria-label={label} tabIndex={0}>
            {children}
        </div>
    </>;
}
