from progressive_login_security import (
    FAILURES_PER_STAGE,
    LOCK_LADDER_SECONDS,
    lock_seconds_for_stage,
)


def test_five_failures_per_stage():
    assert FAILURES_PER_STAGE == 5


def test_merchant_lockout_ladder_exact():
    assert LOCK_LADDER_SECONDS == (
        5 * 60,
        10 * 60,
        60 * 60,
        5 * 60 * 60,
        24 * 60 * 60,
        5 * 24 * 60 * 60,
        30 * 24 * 60 * 60,
    )


def test_stage_lookup_matches_ladder():
    for index, seconds in enumerate(LOCK_LADDER_SECONDS):
        assert lock_seconds_for_stage(index) == seconds


def test_stage_after_month_becomes_hard_lock():
    assert lock_seconds_for_stage(len(LOCK_LADDER_SECONDS)) is None
    assert lock_seconds_for_stage(len(LOCK_LADDER_SECONDS) + 10) is None


def test_negative_stage_is_first_stage():
    assert lock_seconds_for_stage(-1) == 5 * 60


def test_auth_installs_progressive_guard_before_legacy_guard():
    source = open("backend/auth.py", encoding="utf-8").read()
    progressive = source.index("await install_progressive_login_security(app, db,")
    legacy = source.index("await install_login_security(app, db,")
    assert progressive < legacy


def test_legacy_pair_guard_does_not_override_progressive_five_attempt_policy():
    source = open("backend/auth.py", encoding="utf-8").read()
    assert 'os.environ.setdefault("AUTH_LOGIN_PAIR_LIMIT", "1000000")' in source
    assert 'os.environ.setdefault("AUTH_LOGIN_DEVICE_LIMIT", "30")' in source
