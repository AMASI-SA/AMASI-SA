import React from "react";
import EntityLedgerPage from "../components/EntityLedgerPage";

export default function CouriersLedger() {
    return <EntityLedgerPage config={{
        listEndpoint: "/accounting/couriers/list",
        itemsKey: "couriers",
        entityType: "courier",
        headerTitle: "📦 شركات الشحن (نظام Ledger)",
        testIdPrefix: "cour",
        noDataText: "لا توجد شركات شحن",
        summaryCards: [
            { label: "مستحق لشركات الشحن", totalsKey: "payable", color: "rose" },
            { label: "COD لم يُحوَّل بعد", totalsKey: "cod_receivable", color: "amber" },
        ],
        columns: [
            { key: "payable", label: "المستحق علينا", color: "rose" },
            { key: "cod_receivable", label: "COD مفتوح", color: "amber" },
        ],
    }} />;
}
