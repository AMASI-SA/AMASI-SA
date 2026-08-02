"""Freeze review after forward transition and install the approved PDF layout."""
from __future__ import annotations


FORWARD_FULFILLMENT_STAGES = {
    "in_progress",
    "preparation",
    "assembly",
}


def install_order_review_forward_stage_guard() -> None:
    import order_review_routes
    from preparation_pdf_reference_layout import (
        install_preparation_pdf_reference_layout,
    )
    from preparation_pdf_wrapped_text import (
        install_preparation_pdf_wrapped_text,
    )
    from preparation_pdf_card_file_number import (
        install_preparation_pdf_card_file_number,
    )

    order_review_routes.REVIEW_COMPLETED_STAGES.update(
        FORWARD_FULFILLMENT_STAGES
    )
    install_preparation_pdf_reference_layout()
    install_preparation_pdf_wrapped_text()
    install_preparation_pdf_card_file_number()


__all__ = [
    "FORWARD_FULFILLMENT_STAGES",
    "install_order_review_forward_stage_guard",
]
