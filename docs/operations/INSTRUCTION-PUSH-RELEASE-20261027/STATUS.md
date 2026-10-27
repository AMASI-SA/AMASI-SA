# Instruction and Push release — WIP

User authorization relayed by APK task01a17494-6cd2-7323-8edb-0cc78ba00854: repair notifications end to end and deploy server; synthetic tests only, preserve Preview and no actual financial/business writes or real-recipient test messages.

Base Production5c3353034be910ab2bde62c12512d1d8ded26faa. Branch codex/instruction-push-release-20261027. Integrated PR1478 source commits b81f731d5,7a81df04d,f07f20ac1,160b49de6 onto current base; intentionally not copying its obsolete release intent. APK60 edit_flow:false accepted explicitly while extra=forbid remains. New contract tests: 10passed+2subtests, one expected failure for absent capabilities.edit_flow_version.

Independent bounded ACL review found no concrete cross-tenant escape, but identified order-added-item revision validation, resend/reassignment and completed-response retry gaps. These require correction and focused tests before source freeze. Capability1 not yet implemented. Production push setting and delivery readiness remain unverified; Preview workers intentionally remain disabled and protected.

No new PR, sourceA/intentB freeze, merge, host change, lease or deployment. Production changed: no. Next: fix the confirmed flow gaps, add capability1 and test APK60/general/urgent/edit/comments/push with synthetic storage, review combined diff, then new CI/readiness/rehearsal and protocolv5 gates. Issue1006 previously reached comment limit; this file is durable handoff.

## Recovery contract verified locally (2026-10-27)
Implemented capability edit_flow_version=1 and actor-scoped recovery capabilities; exact APK60 false flag remains accepted with unknown fields rejected. Order-wide revisions reject added items. Creator may resend a newer revision only before execution starts. An atomic instruction-document fence prevents resend/submission races in both orderings. Reassigned pending workers can acknowledge the current sent revision. Successful completed-request retries are read-only and tenant/actor/revision constrained.

Fresh isolated verification: 91 passed, 4 subtests passed, 4 skipped (real loopback Mongo required), exit 0. Suites: instruction_edit_flow, instruction_orders_permission, instruction_product_cards, instruction_comments_progress, operational_push, operational_instruction_notifications, order_tracking_notes, operational_courier_instructions, assembly_search_instruction_batch. Courier scan fixture now includes the canonical order required by current Production; no courier production code changed. Expanded Mobile Order Tracking CI to cover these suites with its isolated Mongo. Production origin freshly remains 5c3353034be910ab2bde62c12512d1d8ded26faa. Independent bounded review found no remaining concrete blocker after the concurrency fence; reverse-order race also passes locally.

Remaining: CI and reviewed source/intent freeze, actual notification runtime configuration evidence, fresh host guard, official rehearsal and authorized publish/verify. No new Production changes, lease or deployment. Preview runtime and financial data untouched. This checkpoint is not release acceptance.
