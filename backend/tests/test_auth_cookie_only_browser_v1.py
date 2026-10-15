"""Static browser-auth contract: JWTs must not be persisted in Web Storage."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUTH_CONTEXT = ROOT / "frontend" / "src" / "context" / "AuthContext.jsx"
API_JS = ROOT / "frontend" / "src" / "lib" / "api.js"


def test_auth_context_never_persists_access_token_to_local_storage():
    source = AUTH_CONTEXT.read_text(encoding="utf-8")

    assert 'localStorage.setItem("access_token"' not in source
    assert "clearLegacyBrowserAccessToken" in source
    assert 'localStorage.removeItem("access_token")' in source


def test_legacy_access_token_is_removed_before_initial_auth_probe():
    source = AUTH_CONTEXT.read_text(encoding="utf-8")
    provider_pos = source.index("export function AuthProvider")
    cleanup_pos = source.index("clearLegacyBrowserAccessToken();", provider_pos)
    bootstrap_pos = source.index("runBoundedAuthBootstrap({", cleanup_pos)
    probe_pos = source.index("loadCurrentUser(requestOptions)", bootstrap_pos)

    assert cleanup_pos < bootstrap_pos < probe_pos


def test_shared_api_uses_credentials_cookie_transport():
    source = API_JS.read_text(encoding="utf-8")

    assert "withCredentials: true" in source


def test_browser_401_uses_single_flight_cookie_refresh_before_retry():
    source = API_JS.read_text(encoding="utf-8")

    assert "authRefreshPromise" in source
    assert '`${API_BASE}/auth/refresh`' in source
    assert "_mezanAuthRetried" in source
    assert "return api.request" in source
    assert "browserSessionRefreshTimeout(error.config)" in source
    assert "timeout: timeoutMs" in source
    assert "catch (refreshError)" in source
    assert "Promise.reject(refreshError)" in source


def test_transient_auth_probe_is_bounded_without_marking_browser_anonymous():
    source = AUTH_CONTEXT.read_text(encoding="utf-8")

    catch_start = source.index("} catch (error) {")
    preservation_comment = source.index("temporary network/origin failure", catch_start)
    throw_pos = source.index("throw error;", preservation_comment)
    false_pos = source.index("setUser(false);", catch_start)

    assert false_pos < preservation_comment < throw_pos
    assert 'setAuthStatus("unavailable")' in source
    assert "runBoundedAuthBootstrap" in source
    assert "window.setTimeout(probe, 2000)" not in source


def test_login_and_register_still_refresh_full_user_after_cookie_is_set():
    source = AUTH_CONTEXT.read_text(encoding="utf-8")

    login_start = source.index("const login = async")
    register_start = source.index("const register = async")
    logout_start = source.index("const logout = async")

    login_block = source[login_start:register_start]
    register_block = source[register_start:logout_start]

    assert 'api.post("/auth/login"' in login_block
    assert "await refreshUser();" in login_block
    assert 'api.post("/auth/register"' in register_block
    assert "await refreshUser();" in register_block
