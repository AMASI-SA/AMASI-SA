# Snapchat AI budget direction — second increment

Task branch: codex/snap-budget-direction-20261025. Base: 442bddc40493a05945cd6dd17fb1fe1ef1ea5a20 (merged readiness PR1483). Root owns source, tests, review and sequential publication. This increment does not activate autonomy or change an advertising account.

The existing minimum of five native currency units could turn a reduce recommendation at budget4 into budget5, or leave budget5 unchanged. Before creating any governed provider proposal, require the calculated native-micro budget to move strictly in the recommendation's direction. Normal changes and all existing evidence, approval, drift and profit controls remain intact.

Fresh baseline through the actual execution seam:4 failed and7 passed in1.16s. The four failures are reduce at4/5 native units on campaign/ad-group targets. Initial test-fixture omissions were corrected before counting this baseline. No provider call was made.

Final affected run after correction:79 passed in1.71s, exit0, two inherited Pydantic warnings (budget drift, alignment, owner approval preflight and quality checks). Additive cases cover normal reduce/scale and a reduction from5.01 to the valid minimum. Earlier75-test checkpoint also passed before these extra positive controls.

The existing Production Campaign AI Snapchat Source V2 workflow explicitly includes the budget-drift regression in its trigger, compilation and pytest command. No test exclusion or release-guard weakening.

Next: final focused run, source review, remote checkpoint/Draft PR, exact-source CI and fresh v5 intent. Publish only after PR1483's official deployment/verification and only on the then-current Production base. No real budget changes are part of this test.

Production changed by this increment: no. Advertising writes: zero. PR1483 publication is separately in progress under root's codex-snap1483 lease after successful58.73s build retry and prepublish. Do not modify shared /app or publish this second increment until the first release verifies and its lease closes.
