# Mezan AI incomplete-response hotfix

This temporary branch hardens the existing asynchronous Mezan AI analyzer against OpenAI Responses API outcomes that are `incomplete`, `failed`, or completed without usable structured output.

The final patch keeps the analyzer read-only, sets `store=false`, lowers reasoning effort, increases the bounded output allowance, and preserves only safe failure metadata for polling clients.
