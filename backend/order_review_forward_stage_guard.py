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
    from preparation_pdf_amasi_a4_layout import (
        install_preparation_pdf_amasi_a4_layout,
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
    # Final overlay. This intentionally runs last so the merchant-approved
    # Amasi A4 design is not altered by legacy layout wrappers.
    install_preparation_pdf_amasi_a4_layout()


__all__ = [
    "FORWARD_FULFILLMENT_STAGES",
    "install_order_review_forward_stage_guard",
]
