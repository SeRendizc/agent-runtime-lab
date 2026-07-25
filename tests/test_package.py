import agent_runtime_lab


def test_package_exposes_version() -> None:
    assert agent_runtime_lab.__version__ == "0.1.0"
