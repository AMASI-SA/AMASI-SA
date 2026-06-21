// Iter-250b · P1.5.r — Entity Ledger deep-link page.
//
// Backend reports (e.g. `suppliers_report_routes.py:220`) generate
// links of the form `/entity-ledger/<type>/<id>` (e.g.
// `/entity-ledger/supplier/abc123…`). This wrapper resolves the
// `:type` segment to the same config the dedicated list page
// (`SuppliersLedger`, `ExternalsLedger`, `CouriersLedger`) uses, then
// renders the shared `EntityLedgerPage` with `autoOpenId={:id}` so
// the matching row's drawer opens immediately.
//
// Supported types: supplier, external, external_person, courier.
// Anything else falls back to a friendly Arabic "unknown type" error
// so we never silently render a blank page again.

import React from "react";
import { useParams, Navigate } from "react-router-dom";
import EntityLedgerPage from "../components/EntityLedgerPage";

const TYPE_CONFIGS = {
    supplier: {
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
        ],
        columns: [
            { key: "outstanding_debt", label: "المستحق", color: "rose" },
            { key: "debits",  label: "إجمالي مدين", color: "slate" },
            { key: "credits", label: "إجمالي دائن", color: "slate" },
        ],
    },
    external: {
        listEndpoint: "/accounting/externals/list",
        itemsKey: "externals",
        entityType: "external_person",
        subAccount: "receivable",
        headerTitle: "🤝 الأشخاص الخارجيون (نظام Ledger)",
        testIdPrefix: "ext",
        noDataText: "لا يوجد أشخاص خارجيون",
        summaryCards: [
            { label: "إجمالي المستحق لنا",
              totalsKey: "receivable", color: "emerald" },
        ],
        columns: [
            { key: "receivable", label: "المستحق لنا", color: "emerald" },
            { key: "kind",       label: "النوع",
              isCurrency: false, color: "slate" },
        ],
    },
    courier: {
        listEndpoint: "/accounting/couriers/list",
        itemsKey: "couriers",
        entityType: "courier",
        headerTitle: "📦 شركات الشحن (نظام Ledger)",
        testIdPrefix: "cour",
        noDataText: "لا توجد شركات شحن",
        summaryCards: [
            { label: "مستحق لشركات الشحن",
              totalsKey: "payable", color: "rose" },
            { label: "COD لم يُحوَّل بعد",
              totalsKey: "cod_receivable", color: "amber" },
        ],
        columns: [
            { key: "payable",        label: "المستحق علينا", color: "rose" },
            { key: "cod_receivable", label: "COD مفتوح",      color: "amber" },
        ],
    },
};
// Alias for backend variations.
TYPE_CONFIGS.external_person = TYPE_CONFIGS.external;

// Where to land users who deep-link with an unsupported type.
const FALLBACK_BY_TYPE = {
    employee: "/employees-ledger",
};

export default function EntityLedgerByIdPage() {
    const { type, id } = useParams();
    const config = TYPE_CONFIGS[type];

    if (!config) {
        const fb = FALLBACK_BY_TYPE[type];
        if (fb) return <Navigate to={fb} replace />;
        return (
            <div className="p-8 max-w-2xl mx-auto"
                 data-testid="entity-ledger-unknown-type">
                <div className="bg-rose-50 border border-rose-200 rounded-2xl p-6">
                    <h1 className="text-xl font-extrabold text-rose-800 mb-2">
                        نوع غير مدعوم
                    </h1>
                    <p className="text-sm text-rose-700">
                        لا يوجد دفتر Ledger من النوع{" "}
                        <code className="font-mono">{type}</code>.
                        تأكد من الرابط أو ارجع إلى التقارير.
                    </p>
                </div>
            </div>
        );
    }

    return <EntityLedgerPage config={config} autoOpenId={id} />;
}
