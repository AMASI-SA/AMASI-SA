"""Memory-only acceptance state; pipes expose fixed control messages only.

No state/metadata file, encryption key or serialized session crosses phases.
EOF, phase failure and cancellation fail closed and release state references.
Process teardown is the lifetime boundary, not a claim of memory zeroization.
"""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from contextvars import ContextVar
import signal
import sys

PHASES = ("setup", "http", "mongo-down", "after-restart")
PREPARATION_PHASES = ("prep-setup", "prep-review", "prep-create", "prep-resume", "prep-finish")
REVIEW_CHECK_IDS = (
    "OWNER_LOGIN", "EMPLOYEE_LOGIN", "VIEWER_LOGIN", "OUTSIDER_LOGIN",
    "OWNER_SESSION", "EMPLOYEE_SESSION", "VIEWER_SESSION", "OUTSIDER_SESSION",
    "REVIEW_INVARIANTS", "TENANT_SNAPSHOT", "ORDER_READ", "PRODUCT_IDENTITIES",
    "QUANTITIES_OPTIONS", "IMAGE_UPLOAD", "IMAGE_CHOICE", "ITEM_NOTE",
    "IMAGE_GALLERY_LINK_SET", "IMAGE_RESOURCE_KNOWN", "IMAGE_HTTP_RESPONSE", "IMAGE_CONTENT_MATCH", "IMAGE_TENANT_DENIAL", "REVIEW_ROLE_DENIAL", "REVIEW_COMPLETE",
    "REVIEW_ERROR_CODE", "REVIEW_STORED_STATE", "NO_PREPARATION_ENTITIES",
    "PROVIDER_COUNTERS",
)
CHECKS_BY_PHASE = {
    'prep-review': REVIEW_CHECK_IDS,
    'prep-create': ('EMPLOYEE_CATALOG', 'INITIAL_CATALOG', 'SAFE_DRAFT', 'FILE_CREATE',
                    'FINALIZE_FALLBACK', 'FILE_CREATE_REPEAT', 'EARLY_START_DENIAL',
                    'DRAFT_ROLE_DENIAL', 'IMAGE_PERSISTENCE', 'FILE_REGISTRY_READ',
                    'PDF_HTTP', 'PDF_CONTENT', 'PDF_TENANT_DENIAL', 'INCOMPLETE_DRAFT',
                    'INCOMPLETE_FINALIZE_DENIAL', 'CHECKPOINT_CAPTURE'),
    'prep-resume': ('OWNER_SESSION', 'EMPLOYEE_SESSION', 'VIEWER_SESSION', 'OUTSIDER_SESSION',
                    'RESUME_INVARIANTS', 'SNAPSHOT_IDENTITY', 'SNAPSHOT_MATCH', 'OTHER_TENANT_MATCH',
                    'IMAGE_PERSISTENCE', 'FILE_REGISTRY_READ', 'PDF_HTTP', 'PDF_CONTENT', 'PDF_TENANT_DENIAL',
                    'INCOMPLETE_RELEASE', 'COMPLETED_RELEASE_DENIAL', 'REALLOCATION_DRAFT',
                    'REALLOCATION_DENIAL', 'REALLOCATION_RELEASE', 'REMAINING_CATALOG',
                    'SAFE_DRAFT', 'FILE_CREATE', 'FINALIZE_FALLBACK', 'FILE_CREATE_REPEAT',
                    'ASSIGNMENT_STATES', 'UNIT_QUANTITIES_OPTIONS', 'EMPLOYEE_START', 'START_REPEAT',
                    'SUPPLIER_WORKSPACE', 'SUPPLIER_DISPATCH', 'SUPPLIER_DISPATCH_REPEAT', 'SUPPLIER_READY',
                    'SUPPLIER_PIECE_IDENTITY', 'RECEIVING_SEARCH', 'PIECE_RECEIVE', 'RECEIVE_REPEAT',
                    'FILE_COMPLETED_STATE', 'ASSEMBLY_SEARCH', 'PIECE_ASSEMBLY', 'SIMULATED_LABEL_FAILURE',
                    'ASSEMBLY_STATES', 'RESUME_PROVIDER_COUNTERS', 'CHECKPOINT_CAPTURE'),
    'prep-finish': ('OWNER_SESSION', 'EMPLOYEE_SESSION', 'VIEWER_SESSION', 'OUTSIDER_SESSION',
                    'RESUME_INVARIANTS', 'SNAPSHOT_IDENTITY', 'SNAPSHOT_MATCH', 'OTHER_TENANT_MATCH',
                    'IMAGE_PERSISTENCE', 'FILE_REGISTRY_READ', 'PDF_HTTP', 'PDF_CONTENT', 'PDF_TENANT_DENIAL',
                    'FINAL_PROVIDER_COUNTERS', 'FINAL_LABEL_COUNTS'),
}
CHECK_IDS = tuple(dict.fromkeys(c for ids in CHECKS_BY_PHASE.values() for c in ids))
_ACTIVE = ContextVar('acceptance_diagnostic_context', default=None)
FAILURE_TYPES = ("TIMEOUT", "CHANNEL_CLOSED", "CANCELLED", "PHASE_ORDER",
                 "ASSERTION_FAILED", "HTTP_STATUS_MISMATCH", "UNCLASSIFIED_FAILURE")


class HTTPStatusFailure(AssertionError):
    def __init__(self, expected, actual):
        super().__init__("HTTP_STATUS_MISMATCH")
        self.expected, self.actual = expected, actual


class CheckFailure(AssertionError):
    def __init__(self, check_id, reason, expected=None, actual=None):
        super().__init__("CHECK_FAILED")
        self.check_id, self.reason = check_id, reason
        self.expected, self.actual = expected, actual


def failure_type(error):
    if isinstance(error, KeyboardInterrupt):
        return "CANCELLED"
    if isinstance(error, TimeoutError):
        return "TIMEOUT"
    if isinstance(error, HTTPStatusFailure):
        return "HTTP_STATUS_MISMATCH"
    if isinstance(error, AssertionError):
        return "ASSERTION_FAILED"
    return "UNCLASSIFIED_FAILURE"


@contextmanager
def check(check_id):
    # No untrusted identifier can reach a protocol message, even on failure.
    if type(check_id) is not str or check_id not in CHECK_IDS:
        raise CheckFailure(None, "UNCLASSIFIED_FAILURE")
    active = _ACTIVE.get()
    if active is not None and check_id not in CHECKS_BY_PHASE.get(active[0], ()):
        raise CheckFailure(None, "UNCLASSIFIED_FAILURE")
    try:
        yield
    except CheckFailure:
        raise
    except BaseException as error:
        expected = error.expected if type(error) is HTTPStatusFailure else None
        actual = error.actual if type(error) is HTTPStatusFailure else None
        raise CheckFailure(check_id, failure_type(error), expected, actual) from None
    else:
        if active is not None:
            active[1].setdefault('_completed_checks', []).append(check_id)


def check_diagnostic(error, phase):
    if (type(error.check_id) is not str or error.check_id not in CHECKS_BY_PHASE.get(phase, ())
            or type(error.reason) is not str or error.reason not in FAILURE_TYPES):
        return None
    suffix = error.reason + " " + error.check_id
    if error.reason == "HTTP_STATUS_MISMATCH":
        if not all(type(n) is int and 100 <= n <= 599 for n in (error.expected, error.actual)):
            return None
        suffix += " " + str(error.expected) + " " + str(error.actual)
    elif error.expected is not None or error.actual is not None:
        return None
    return suffix


class Discard:
    def write(self, text):
        return len(text)

    def flush(self):
        pass


def emit_review_evidence(state, replies, phase):
    from simulator_evidence import protocol_lines, CHECKPOINTS_BY_PHASE
    from snapshot_evidence import validate_snapshot_line
    completed = state.pop('_completed_checks', [])
    if type(completed) is not list or len(completed) > 512:
        replies.write('EVIDENCE_UNAVAILABLE\n')
        completed = []
    for identifier in completed:
        if type(identifier) is str and identifier in CHECKS_BY_PHASE.get(phase, ()):
            replies.write('CHECK ' + phase + ' ' + identifier + ' PASS\n')
    snapshots = state.pop('_snapshot_evidence', [])
    if type(snapshots) is not list or len(snapshots) > 128:
        snapshots = ['SNAPSHOT unavailable']
    for line in snapshots:
        if phase in ('prep-resume', 'prep-finish') and validate_snapshot_line(line):
            replies.write(line + '\n')
        else:
            replies.write('SNAPSHOT unavailable\n')
    records = state.pop('_review_evidence', [])
    if type(records) is not list or len(records) > 16:
        records = [None]
    for record in records:
        if type(record) not in (tuple, list) or len(record) != 2:
            replies.write('EVIDENCE_UNAVAILABLE\n')
            continue
        checkpoint, counters = record
        if type(checkpoint) is not str or checkpoint not in CHECKPOINTS_BY_PHASE.get(phase, ()):
            replies.write('EVIDENCE_UNAVAILABLE\n')
            continue
        for line in protocol_lines(checkpoint, counters):
            replies.write(line + '\n')
    replies.flush()


def serve(phases, commands, replies, *, profile="runtime"):
    if profile not in ("runtime", "preparation", "preparation-denied"):
        replies.write("FAIL controller PHASE_ORDER\n")
        return 1
    ordered = PREPARATION_PHASES if profile == "preparation" else (PREPARATION_PHASES[:2] if profile == "preparation-denied" else PHASES)
    state = {}
    name = ordered[0]
    reason = "UNCLASSIFIED_FAILURE"
    in_phase = False
    try:
        for name in (*ordered, "finish"):
            command = commands.readline(64)
            if command == "":
                reason = "CHANNEL_CLOSED"
                raise RuntimeError
            if command != name + "\n":
                reason = "PHASE_ORDER"
                raise RuntimeError
            if name == "finish":
                state.clear()
            else:
                # Never format exception text/locals or echo phase output.
                in_phase = True
                token = _ACTIVE.set((name, state))
                try:
                    with redirect_stdout(Discard()), redirect_stderr(Discard()):
                        phases[name](state)
                finally:
                    _ACTIVE.reset(token)
                in_phase = False
                if name in CHECKS_BY_PHASE: emit_review_evidence(state, replies, name)
            replies.write("PASS " + name + "\n")
            replies.flush()
        return 0
    except BaseException as error:
        # Classify only observed types/context, never messages or external fields.
        if isinstance(error, KeyboardInterrupt):
            reason = "CANCELLED"
        elif isinstance(error, TimeoutError):
            reason = "TIMEOUT"
        elif in_phase and isinstance(error, AssertionError):
            reason = "ASSERTION_FAILED"
        elif not in_phase and isinstance(error, (BrokenPipeError, EOFError)):
            reason = "CHANNEL_CLOSED"
        if type(error) is CheckFailure:
            reason = (check_diagnostic(error, name) if in_phase else None) or "UNCLASSIFIED_FAILURE"
        try:
            if name in CHECKS_BY_PHASE: emit_review_evidence(state, replies, name)
        except BaseException:
            pass  # Evidence cannot replace the primary failure.
        try:
            replies.write("FAIL " + name + " " + reason + "\n")
            replies.flush()
        except BaseException:
            pass  # Disconnected output cannot carry a diagnostic; still fail.
        return 1
    finally:
        state.clear()


def run(phases, *, profile="runtime"):
    def cancelled(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, cancelled)
    try:
        return serve(phases, sys.stdin, sys.stdout, profile=profile)
    finally:
        signal.signal(signal.SIGTERM, previous)
