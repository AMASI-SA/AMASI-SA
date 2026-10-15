from integrations.qoyod.base_url import normalize_qoyod_api_base


def test_canonical_host_without_version_gets_v2_path():
    assert normalize_qoyod_api_base("https://api.qoyod.com") == (
        "https://api.qoyod.com/2.0"
    )


def test_canonical_host_with_legacy_api_path_gets_v2_path():
    assert normalize_qoyod_api_base("https://api.qoyod.com/api/2.0/") == (
        "https://api.qoyod.com/2.0"
    )


def test_retired_hosts_get_canonical_origin_and_path():
    assert normalize_qoyod_api_base(
        "https://legacy.qoyod.com/api/2.0"
    ) == "https://api.qoyod.com/2.0"
    assert normalize_qoyod_api_base(
        "https://www.qoyod.com/api/2.0/"
    ) == "https://api.qoyod.com/2.0"


def test_explicit_future_version_and_custom_origins_are_preserved():
    assert normalize_qoyod_api_base(
        "https://api.qoyod.com/v3.0"
    ) == "https://api.qoyod.com/v3.0"
    assert normalize_qoyod_api_base(
        "https://qoyod.invalid/custom"
    ) == "https://qoyod.invalid/custom"


def test_both_clients_use_the_same_effective_base():
    from integrations.qoyod.api_client import QoyodAPIClient
    from integrations.qoyod_manual.client import ManualQoyodClient

    expected = "https://api.qoyod.com/2.0"
    assert QoyodAPIClient(
        "synthetic", base_url="https://api.qoyod.com"
    )._base_url == expected
    assert ManualQoyodClient(
        api_key="synthetic", base_url="https://api.qoyod.com"
    )._base_url == expected
