# Priority app release — 2026-11-06

Owner authorized Android delivery and the required Mezan production update.
Server source starts at ae2fe9ce01 (resolve exact parent with git), preserving PR1774 and all current production commits. Reapplies only grouped waiting pages/photo preservation, paid-review snapshot normalization-v3 and diagnostics, disjoint assembly queue reads, and physical assigned_at projection from old reviewed PR1769. Broader notification WIP is remotely preserved on codex/app-notifications-wip-20261106 and excluded from this candidate.

Local paid-review/mapping/snapshot suites: first120passed before a combined-suite query-count fixture failed; standalone reproduction of that fixture passed, so investigate test interaction rather than remove source checks. Dedicated waiting/dispatch/queue suites rerunning independently. Old source90b4a496e had all applicable PR1769 CI checks green. Exact integrated candidate CI remains required. No live approval/custody, notification, financial or deployment write.

Companion mobile branch codex/android70-priority-release-20261106: Android1.0.55/code70; fast overview/grouped loading, explicit review flow, exact native direct-receipt endpoint, no automatic Ready, normal auth and scanner preserved. Build69 is not this candidate. Next: sourceCI -> frozen intent-onlyB -> actual clean-clone adapter after shared guardinactive -> merge/appff/build/prepare/prepublish/deploy/newSucceeded/verify3. No lease prepared; Production unchanged by this task.
