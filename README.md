# Mezan OS

Mezan OS is one **AI-native commerce operating system** built through additive, parallel development — not a separate AI rewrite.

## First read for any new development conversation or coding agent

Read `AGENTS.md` before proposing or implementing major work.

Then follow the binding architecture and project decisions in:

- `docs/adr/ADR-001-architecture-principles.md`
- `docs/adr/ADR-002-ai-native-operating-model.md`
- `docs/adr/ADR-003-ai-commerce-discovery.md`
- `docs/PROJECT_DECISIONS.md`
- `docs/AI_COMMERCE_OPERATING_SYSTEM_ROADMAP.md`

## Project contract in one sentence

> Current operational, product, accounting, advertising, customer and integration work continues in parallel with AI development toward the same Mezan OS; existing validated work is the deterministic foundation for future AI agents, not throwaway work or a separate path.

The primary optimization target is sustainable net profit. Correctness-critical business rules remain deterministic. AI reasons above trusted facts and any future AI-initiated production write must pass through the governed Mezan Action Gateway.

A core AI growth objective is to make Amasi products increasingly **understandable, discoverable and recommendable through natural-language AI commerce**. Mezan must preserve rich product knowledge, shopper-intent evidence, recommendation provenance and outcome measurement so a future customer request such as “find me a delicate gold-colored gift necklace under 250 SAR” can be matched to truthful Amasi product facts and measured through to order-item profit.
