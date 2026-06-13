import React from "react";
import EntityLedgerPage from "../components/EntityLedgerPage";

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
            { label: "إجمالي مستحق للموردين", totalsKey: "outstanding_debt", color: "rose" },
        ],
        columns: [
            { key: "outstanding_debt", label: "المستحق", color: "rose" },
            { key: "debits", label: "إجمالي مدين", color: "slate" },
            { key: "credits", label: "إجمالي دائن", color: "slate" },
        ],
    }} />;
}
