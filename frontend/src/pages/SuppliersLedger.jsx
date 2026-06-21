import React from "react";
import EntityLedgerPage from "../components/EntityLedgerPage";

// Iter-250b · P1.5.x — Suppliers list now surfaces DRIFT counts
// alongside the GL balance. The merchant must see (but never confuse
// with) `financial_movements` entries that lack a `general_ledger`
// post. The balance column STAYS strictly GL-derived; the drift
// column is purely informational.
export default function SuppliersLedger() {
    return <EntityLedgerPage config={{
        listEndpoint: "/accounting/suppliers/list",
        itemsKey: "suppliers",
        entityType: "supplier",
        subAccount: "payable",
        headerTitle: "🏭 الموردون (نظام Ledger)",
        testIdPrefix: "sup",
        noDataText: "لا يوجد موردون",
        summaryCards: [
            { label: "إجمالي مستحق للموردين",
              totalsKey: "outstanding_debt", color: "rose" },
            { label: "فواتير بدون قيد",
              totalsKey: "drifted_count",
              color: "amber", isCurrency: false },
            { label: "إجمالي مبلغ بدون قيد",
              totalsKey: "drifted_total", color: "amber" },
        ],
        columns: [
            { key: "outstanding_debt", label: "المستحق (GL)", color: "rose" },
            { key: "debits",  label: "إجمالي مدين", color: "slate" },
            { key: "credits", label: "إجمالي دائن", color: "slate" },
            // Iter-250b · P1.5.x — Drift column: counts + amount in
            // one cell so it never overflows on narrow screens.
            {
                key: "drifted_count",
                label: "فواتير بدون قيد",
                color: "amber",
                isCurrency: false,
                renderCell: (r) => {
                    const cnt = Number(r.drifted_count || 0);
                    if (cnt === 0) {
                        return <span className="text-slate-300 font-bold">—</span>;
                    }
                    const tot = Number(r.drifted_total || 0).toLocaleString(
                        "en-US",
                        { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                    return (
                        <span className="inline-flex items-center gap-1 text-amber-800">
                            <span className="font-extrabold">{cnt}</span>
                            <span className="text-[11px]">فاتورة</span>
                            <span className="text-[11px] text-slate-500">·</span>
                            <span className="font-bold">{tot}</span>
                        </span>
                    );
                },
            },
        ],
    }} />;
}
