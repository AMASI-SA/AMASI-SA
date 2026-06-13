import React from "react";
import EntityLedgerPage from "../components/EntityLedgerPage";

export default function ExternalsLedger() {
    return <EntityLedgerPage config={{
        listEndpoint: "/accounting/externals/list",
        itemsKey: "externals",
        entityType: "external_person",
        subAccount: "receivable",
        headerTitle: "🤝 الأشخاص الخارجيون (نظام Ledger)",
        testIdPrefix: "ext",
        noDataText: "لا يوجد أشخاص خارجيون",
        summaryCards: [
            { label: "إجمالي المستحق لنا", totalsKey: "receivable", color: "emerald" },
        ],
        columns: [
            { key: "receivable", label: "المستحق لنا", color: "emerald" },
            { key: "kind", label: "النوع", isCurrency: false, color: "slate" },
        ],
    }} />;
}
