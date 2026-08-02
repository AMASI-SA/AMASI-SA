"""Freeze review after forward transition and install the approved PDF layout."""
from __future__ import annotations


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

    order_review_routes.REVIEW_COMPLETED_STAGES.update(
        FORWARD_FULFILLMENT_STAGES
    )
    # Sorting must wrap the original catalogue loader before the reference PDF
    # layer adds its image-access context around that same loader.
    install_reviewed_product_sorting()
    install_preparation_pdf_reference_layout()
    # The reference builder first creates one snapshot per allocated source
    # line; expand it here so every selected physical piece becomes one card.
    install_preparation_pdf_unit_card_expansion()
    install_preparation_pdf_wrapped_text()
    install_preparation_pdf_card_file_number()
    # Keep wrapping and durable numbering, then apply only the merchant-approved
    # compact media/detail geometry as the final renderer overlay.
    install_preparation_pdf_compact_operational_layout()


__all__ = [
    "FORWARD_FULFILLMENT_STAGES",
    "install_order_review_forward_stage_guard",
]
