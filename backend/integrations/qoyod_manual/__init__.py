"""Plan-B Manual Send — bare-bones, isolated Qoyod push path.

Created per user directive (2026-02) after Rev32→Rev48 layered guards
proved unable to release the first Canary. The legacy pipeline stays
FROZEN (kept as reference, gated by `qoyod_settings.legacy_pipeline_frozen`),
and this module provides a single, manual, one-order-at-a-time send:

    GET  /api/integrations/qoyod/manual/pending-orders
    POST /api/integrations/qoyod/manual/send/{order_number}

Contract (immutable):
    • Only 4 sequential steps: customer → products → invoice → payment.
    • Only 4 guards:
        1. No duplicate invoice for the same order_number
           (atomic idempotency lock).
        2. Exact amount parity: Salla total == Qoyod-expected total (0.00 diff).
        3. No DRY/PREVIEW ids ever accepted or emitted.
        4. Payment method must be mapped to a Qoyod account
           (via existing `qoyod_settings.payment_method_mapping`).
    • Zero Canary / Budget / SKIPPED-terminal / DEAD_LETTER / SSOT / Rev32.
    • Old rows are NEVER auto-sent — they only appear on the page.

Small rounding residuals are absorbed into existing positive product lines
through the package-local item-line LRM installer. No synthetic adjustment
product is added to the invoice.
"""


def _install_exact_rounding() -> None:
    # Importing the package occurs before routes import symbols from send.py.
    # Install once here so manual send and diagnostic routes use the same
    # exact-parity builder without touching the frozen legacy pipeline.
    from integrations.qoyod_manual import send as _send
    from integrations.qoyod_manual.rounding_lrm import install

    install(_send)


_install_exact_rounding()
del _install_exact_rounding
