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
        2. Amount parity within one halalah (inclusive):
           abs(Salla total - Qoyod expected total) <= 0.01 SAR.
        3. No DRY/PREVIEW ids ever accepted or emitted.
        4. Payment method must be mapped to a Qoyod account
           (via existing `qoyod_settings.payment_method_mapping`).
    • Zero Canary / Budget / SKIPPED-terminal / DEAD_LETTER / SSOT / Rev32.
    • Old rows are NEVER auto-sent — they only appear on the page.

Small rounding residuals are absorbed into existing positive product lines
through the package-local item-line LRM installer when representable. A
remaining difference of exactly one halalah is allowed by the public guard.
No synthetic adjustment product is added to the invoice.
"""


def _install_exact_rounding() -> None:
    from integrations.qoyod_manual import send as _send
    from integrations.qoyod_manual.rounding_lrm_dynamic import install

    install(_send)


_install_exact_rounding()
del _install_exact_rounding


def _install_auto_send_payment_freshness() -> None:
    from qoyod_auto_payment_freshness import (
        install_auto_send_payment_freshness_patch,
    )

    install_auto_send_payment_freshness_patch()


_install_auto_send_payment_freshness()
del _install_auto_send_payment_freshness


def _install_per_order_failure_isolation() -> None:
    from qoyod_auto_per_order_isolation import install_per_order_isolation

    install_per_order_isolation()


_install_per_order_failure_isolation()
del _install_per_order_failure_isolation
