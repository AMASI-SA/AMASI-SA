# PR1917 visible financial acceptance

Run only after official production verify exits0 and fresh status is inactive. Root controls UI. The financial page must remain visible and online.

1. Open exactly one new visible tab at the previously verified financial-position URL. Mount automatically POSTs refresh with visible:true, then GETs the report after auth readiness. Do not also click refresh/reload. Hidden/offline cancellation can queue another visible attempt; keep document.visibilityState visible.
2. Stored snapshot timestamp is [data-testid="financial-position-content"] .ofp-scope-bar > div:first-child bdi (report.as_of, displayed in Riyadh). It must advance from12/11/2026,01:07 to the current attempt. Footer generated_at is only report rendering time and cannot prove a fresh calculation.
3. Require report content and no root main error or fallback warning:
   - root > .ofp-page-notice.ofp-error[role="alert"]
   - root > p.ofp-warning[role="alert"]:not(.ofp-no-print)
   Treat content > aside.ofp-warning[role="status"] separately as declared missing financial evidence.
4. A table with more than one page: next then previous using aria-label prefixes within that table. Check row change, restored page and unchanged as_of. Pagination is local rows.slice.
5. Positive .ofp-pending-orders opens dialog.ofp-order-review[open]. Read headings/details; search an already visible order number, clear it, page if possible, close via aria-label إغلاق نافذة الطلبات. All these controls are local.
6. Never click تأكيد التوصيل بعد بداية التشغيل: it previews then performs a financial confirm without another confirmation. Never use approvals, settlements, balance save or fee/custody actions as tests.
7. If the dialog declares details available for only some orders, record the limitation separately. Do not expose private order IDs, business counts or amounts in public repository evidence.
8. Runtime Logs have remained unopened since before publication. If first refresh is still pending at about87s, capture a fresh runtime window (only fixedstage/status/duration). Verify financial visibility before/after any other-tab action. If refresh completes earlier, capture logs promptly. Never share logs with the platform agent.
9. Success requires fresh as_of and successful visible report. On failure retain exact fixed source substage/HTTP outcome; do not blind retry.

Source review was read-only against B1469f4916715458b8374beb3bfddfc1f3c01f125, tree=M9d8844f32f9db18cd036ff1b55edaec2c0dbe1b8. Source files: OperationalFinancialPosition.jsx, visibleOperationalRead.js, PaginatedReportTable.jsx, PendingOrdersDialog.jsx, api.js, operational_balance_routes.py, operational_financial_position_core.py. No production request/test/edit was performed by the reviewer.
