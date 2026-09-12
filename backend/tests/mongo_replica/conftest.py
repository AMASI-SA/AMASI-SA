"""Pytest registration for isolated Mongo replica-set contracts."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "mongo_replica: requires the loopback MongoDB replica-set CI service",
    )
