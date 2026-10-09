"""Explicit test-only access to the retained provider completion contract.

Production routes always select local completion and park historical provider
operations. These scoped replacements preserve regression coverage of the old
provider/confirmation/recovery engine; they are not a production configuration
switch and must never be installed by a shared or default application fixture.
"""
from contextlib import ExitStack, contextmanager
from unittest.mock import patch


def _legacy_contract_patches():
    import order_review_completion as completion
    import order_review_resume_worker as worker

    return (
        patch.object(completion, "complete_local_review_operation", completion.complete_review_operation),
        patch.object(worker, "run_once", worker.run_legacy_once),
    )


def enable_legacy_review_contract(testcase):
    """Opt a named legacy fixture in until its existing patch teardown runs."""
    for replacement in _legacy_contract_patches():
        replacement.start()
        testcase.patches.append(replacement)


@contextmanager
def legacy_review_contract():
    """Opt one provider-contract operation/process in, then restore defaults."""
    with ExitStack() as stack:
        for replacement in _legacy_contract_patches():
            stack.enter_context(replacement)
        yield
