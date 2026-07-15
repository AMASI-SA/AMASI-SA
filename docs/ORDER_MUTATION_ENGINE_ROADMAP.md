# Order Mutation Engine Roadmap

Status: Approved future phase

## Placement in the project

This phase starts only after the Mezan OS Order Details page is complete and accepted.
It must not be implemented as ad-hoc buttons that directly modify local order data.

The actions will appear at the bottom of the Order Details page, in a workflow similar to Salla, while the business logic remains inside a dedicated `Order Mutation Engine`.

## Planned actions

- Cancel an order.
- Add a product to an order.
- Remove a product from an order.
- Increase or decrease an ordered quantity.
- Replace a product, variant, option, size or color.
- Create a partial return.
- Create a full return.
- Add an internal note.
- Record a customer-facing note when supported.

## Source-of-truth rule

Salla remains the external source of truth for the order.

The required write path is:

1. User requests an action in Mezan.
2. Mezan validates permissions, order state and financial consequences.
3. Mezan prepares a preview of the proposed mutation.
4. The approved mutation is sent to the supported Salla API.
5. Mezan confirms the Salla response.
6. Mezan re-syncs the authoritative order.
7. Mezan records the event, reason and measured consequences.

Mezan must never change the canonical order locally when the provider operation failed or was not confirmed.

## Required reason data

Free-text notes alone are insufficient. Every cancellation, deletion, replacement or return must support a structured reason plus an optional note.

Initial controlled reasons include:

- Customer changed their mind.
- Order was placed by mistake.
- Duplicate order.
- Product unavailable.
- Incorrect product, variant, size or color.
- Product did not match the image or description.
- Size issue.
- Product defect.
- Price objection.
- Shipping cost objection.
- Shipping delay.
- Payment problem.
- Customer service problem.
- Fraud or risk control.
- Other, with a mandatory explanatory note.

## Immutable event record

Every mutation creates an append-only event containing at least:

- Order number and stable order ID.
- Order item ID when applicable.
- Mutation type.
- State before and after.
- Product, variant, options and quantity before and after.
- Structured reason and optional note.
- Initiator: customer, employee, system, carrier or AI recommendation.
- Acting user and permission context.
- Requested time, provider-confirmed time and ingestion time.
- Business timezone `Asia/Riyadh` and UTC timestamps.
- Provider request/response references.
- Payment, shipping, inventory, profit, Qoyod and tax consequences.
- Approval state and rollback/compensation state.

## Safety gates

A mutation must be blocked or routed to a specialized workflow when:

- The order is already shipped or delivered.
- The operation requires a return rather than an edit.
- A Qoyod invoice or payment already exists and the accounting treatment is unresolved.
- Tamara, Tabby or another payment provider requires adjustment or refund handling.
- The amount changes without a verified settlement path.
- Inventory reservation or preparation has already started.
- Another mutation is in progress.
- The provider does not support the requested action safely.

## Implementation sequence

1. Complete and accept the Order Details page.
2. Build the append-only Order Event Log.
3. Build `Order Mutation Engine` contracts and permission model.
4. Implement read-only Preview mode.
5. Enable controlled order cancellation.
6. Enable item and quantity changes.
7. Enable replacements and option changes.
8. Enable partial and full return workflows.
9. Integrate financial, inventory, shipping and Qoyod consequences.
10. Allow AI to classify causes and recommend actions.
11. Allow approved low-risk execution only after reliability gates pass.

## Future intelligence value

The structured history will support analysis such as:

- Cancellation and return reasons by product, variant and option.
- Problems caused by product images, descriptions, sizes or colors.
- Campaigns that generate low-quality or high-return orders.
- Cities, payment methods or carriers associated with cancellation patterns.
- Customer-service issues that prevent purchase completion or cause returns.
- Product and store improvements with measured effects.

## Current gate

Do not begin this phase before the Order Details page is complete and accepted.
The current active work remains finishing Order Engine v1.0 and its details experience.
