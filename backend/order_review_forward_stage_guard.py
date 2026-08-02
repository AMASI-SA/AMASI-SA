"""Freeze stage-one review once an order advances into fulfillment.

The review module historically knew only terminal shipping stages. Preparation
batches introduce intermediate forward stages that must also reject review
mutations and repeated review completion.
"""
from __future__ import annotations


FORWARD_FULFILLMENT_STAGES = {
    "in_progress",
    "preparation",
    "assembly",
}


def install_order_review_forward_stage_guard() -> None:
    import order_review_routes

    order_review_routes.REVIEW_COMPLETED_STAGES.update(
        FORWARD_FULFILLMENT_STAGES
    )


__all__ = [
    "FORWARD_FULFILLMENT_STAGES",
    "install_order_review_forward_stage_guard",
]
