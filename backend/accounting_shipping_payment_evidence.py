"""Use the same producer normalizer as the stored MZ2 Salla order evidence.

No duplicate list of payment gateways and no guessed Apple Pay method alias.
Apple Pay in the existing export fixtures is provider_raw=applepay together
with method مدى / البطاقة الإئتمانية, normalized by the producer to salla.
Bank transfer here means non-COD, NOT proof that a bank payment was received.
"""
from accounting_salla_order_evidence import _payment_method, _provider_reference_matches, _norm
from accounting_shipping_p02 import ShippingAccountingError


def shipping_order_provider(evidence):
    raw_method = evidence.get("payment_method_raw")
    provider = _payment_method(raw_method)
    if provider == "unknown" or provider != evidence.get("accounting_provider"):
        raise ShippingAccountingError("shipping_customer_payment_mode_unproven")
    if provider not in {"cod", "bank_transfer"}:
        # The aliases and the decision both belong to the evidence producer.
        reference = evidence.get("payment_reference")
        valid_reference = (isinstance(reference, dict) and bool(reference.get("reference"))
                           and reference.get("provider_normalized") == _norm(reference.get("provider_raw")))
        try:
            matches = valid_reference and _provider_reference_matches(provider, reference)
        except (KeyError, TypeError):
            matches = False
        if not matches:
            raise ShippingAccountingError("shipping_payment_reference_provider_conflict")
    return provider
