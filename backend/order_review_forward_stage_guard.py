"""Freeze review after forward transition and install the approved PDF layout."""
from __future__ import annotations

# CI fan-out marker: canonical resource catalog and supplier-bank changes must exercise Fulfillment V2.

FORWARD_FULFILLMENT_STAGES = {
    "in_progress",
    "preparation",
    "assembly",
}


def install_order_review_forward_stage_guard() -> None:
    import order_review_routes
    from reviewed_product_sorting import (
        install_reviewed_product_sorting,
    )
    from preparation_pdf_reference_layout import (
        install_preparation_pdf_reference_layout,
    )
    from preparation_pdf_unit_card_expansion import (
        install_preparation_pdf_unit_card_expansion,
    )
    from preparation_pdf_wrapped_text import (
        install_preparation_pdf_wrapped_text,
    )
    from preparation_pdf_card_file_number import (
        install_preparation_pdf_card_file_number,
    )
    from preparation_pdf_compact_operational_layout import (
        install_preparation_pdf_compact_operational_layout,
    )
    from preparation_pdf_amasi_a4_layout import (
        install_preparation_pdf_amasi_a4_layout,
    )
    from preparation_pdf_card_readability_overlay import (
        install_preparation_pdf_card_readability_overlay,
    )
    from preparation_pdf_physical_piece_overlay import (
        install_preparation_pdf_physical_piece_overlay,
    )

    order_review_routes.REVIEW_COMPLETED_STAGES.update(
        FORWARD_FULFILLMENT_STAGES
    )
    install_reviewed_product_sorting()
    install_preparation_pdf_reference_layout()
    install_preparation_pdf_unit_card_expansion()
    install_preparation_pdf_wrapped_text()
    install_preparation_pdf_card_file_number()
    install_preparation_pdf_compact_operational_layout()
    # The merchant-approved Amasi renderer must win over every legacy layout.
    install_preparation_pdf_amasi_a4_layout()
    # Apply the real-file readability corrections without changing A4/3x5.
    install_preparation_pdf_card_readability_overlay()
    # Final identity-safe overlay: one stored physical unit -> one card/QR.
    install_preparation_pdf_physical_piece_overlay()


__all__ = [
    "FORWARD_FULFILLMENT_STAGES",
    "install_order_review_forward_stage_guard",
]
