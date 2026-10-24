# Salla payment clear and automatic sync

WIP based on production branch1fbb7ea4b54ab5574f3ee6937939e21440f88b0a. Root cause reproduced live: central refresh non-empty overlay preserves previous remaining amount when current provider says has_remaining_amount=false and remaining_amount=null. Current provider read confirms settled amount; no customer data retained here.

Implemented explicit settled snapshot normalization in refresh/sync/DTO and payment.updated handler; details addition capability refresh follows payment changes. Regression cases added for manual clear, DTO fallback, automatic webhook and stale replay. Tests pending CI; local python lacks pytest (exit1). No production code deployment yet. Automatic delivery registration/retry coverage and CI/release remain incomplete. Next: validate CI and automatic delivery path, then reviewed v5 release. Keep existing assembly mobile release intact. Do not manually mark business orders reviewed or modify payment data.
