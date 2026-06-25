"""Mezan Integration Platform — root package.

ADR-001 (project architecture principles) is the contract that every
module under this package must respect. See
/app/docs/adr/ADR-001-architecture-principles.md once it is created.

Current state (P0 — Foundation):
    • Single tenant (user_id="main") — multi-tenant ready by design.
    • First connector: Qoyod (output) for invoice push MVP.

Sub-packages:
    integrations.qoyod   — Qoyod Output Connector (MVP).
    (future) integrations.core, .pipeline, .domain, .input_connectors, …
"""
