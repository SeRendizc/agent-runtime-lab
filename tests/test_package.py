import agent_runtime_lab


def test_package_exposes_version() -> None:
    assert agent_runtime_lab.__version__ == "0.1.0"


def test_package_exposes_versioned_trace_api() -> None:
    assert agent_runtime_lab.RunTrace.__name__ == "RunTrace"
    assert callable(agent_runtime_lab.build_run_trace)
