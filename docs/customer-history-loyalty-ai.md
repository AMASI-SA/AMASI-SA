# Customer history and AI loyalty recommendations

## Scope

- Pending-review customer history is read-only and belongs to Mezan OS V2.
- Customer identity is matched by normalized mobile first, then email; names are never used alone.
- Prior orders exclude the current order and expose number, date, total, payment method and status.
- COD warnings distinguish successful, cancelled/failed and first-time COD customers.
- Loyalty and gift suggestions are recommendations only. They never add gifts, discounts, products, or Salla mutations without an explicit approved action.

## Recommendation guardrails

The recommendation policy considers completed-order count, completed spend, average order value, recency, cancellation/return ratio, COD completion history and the current order value. Every recommendation includes a reason, a maximum suggested cost and a human-approval requirement. It must not use protected or sensitive personal attributes and must not reward abusive or high-risk behaviour.
