"""Contract for Mezan's requested five-failure device lockout policy."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUTH = ROOT / "backend" / "auth.py"
LOGIN_SECURITY = ROOT / "backend" / "login_security.py"


def test_mezan_installs_five_as_default_device_failure_limit():
    source = AUTH.read_text(encoding="utf-8")
    assert 'os.environ.setdefault("AUTH_LOGIN_DEVICE_LIMIT", "5")' in source


def test_active_block_check_always_includes_device_key():
    source = LOGIN_SECURITY.read_text(encoding="utf-8")
    assert "keys = [self.pair_key, self.device_key]" in source


def test_device_counter_participates_in_failure_thresholds():
    source = LOGIN_SECURITY.read_text(encoding="utf-8")
    assert '(identity.device_key, "device", _device_limit())' in source


def test_default_window_and_block_duration_remain_one_hour():
    source = LOGIN_SECURITY.read_text(encoding="utf-8")
    assert 'AUTH_LOGIN_WINDOW_SECONDS", 60 * 60' in source
    assert 'AUTH_LOGIN_BLOCK_SECONDS", 60 * 60' in source
