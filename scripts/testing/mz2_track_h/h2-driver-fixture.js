// Synthetic review-only payloads matching PR #1220. No customer or production data.
export default function fixture(url) {
    if (url === "/accounting-module/shipping-v2/context") return {
        store_drivers: [{ id: "H2-SYN-DRIVER", name: "موصل تجريبي" }],
        couriers: [{ courier_key: "H2-SYN-COURIER", name: "شركة شحن تجريبية", status: "active", confirmed_by: "H2-SYN-OWNER", confirmed_at: "2026-10-01T09:00:00+03:00" }],
        stages: { "7": { ready: true, reasons: [] }, "8": { ready: true, reasons: [] }, "9": { ready: true, reasons: [] } },
        setup_version: 1, p02: "LOCKED_BY_EXISTING_ACTIVATION_GATE", activation_performed: false,
        bank_port: { ready: false, code: "mz2_shipping_bank_port_not_integrated" },
        driver_payment_destination: { ready: false, code: "mz2_driver_payment_destination_not_integrated", card_terminal_destination: "pos_receivable", direct_pos_to_bank_on_accept: false },
        delivery_policy: "canonical_salla_delivered_no_upload", legacy_evidence_used: false,
    };
    if (url === "/store-delivery/payment-review/pending") return { items: [
        { id: "H2-SYN-BANK-REVIEW", assignment_id: "H2-SYN-ASSIGNMENT-1", driver_id: "H2-SYN-DRIVER", driver_name: "موصل تجريبي", order_number: "H2-SYN-ORDER-1", payment_method: "bank_transfer", amount: "500.00", status: "pending", receipt_reference: "H2-SYN-PROOF-1", submitted_at: "2026-10-01T10:00:00+03:00" },
        { id: "H2-SYN-POS-REVIEW", assignment_id: "H2-SYN-ASSIGNMENT-2", driver_id: "H2-SYN-DRIVER", driver_name: "موصل تجريبي", order_number: "H2-SYN-ORDER-2", payment_method: "card_terminal", amount: "200.00", status: "pending", receipt_reference: "H2-SYN-PROOF-2", submitted_at: "2026-10-01T10:15:00+03:00" },
    ], total: 2 };
    const prefix = "/accounting-module/shipping-v2/statements/";
    if (url.startsWith(prefix)) {
        const [kind, id] = url.slice(prefix.length).split("/");
        if (!["store_driver", "courier"].includes(kind) || !["H2-SYN-DRIVER", "H2-SYN-COURIER"].includes(decodeURIComponent(id))) return undefined;
        return { ledger_source: "accounting_v2", party_type: kind, party_id: decodeURIComponent(id), cod_receivable: "700.00", payable: "34.50", collections: "0.00", payments: "0.00", recognized_fees: "34.50", entries: [], unreconciled_items: [] };
    }
    return undefined;
}
