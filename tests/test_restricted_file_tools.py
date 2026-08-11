import hashlib
from pathlib import Path

import pytest

from agent_runtime_lab.domain.errors import (
    RestrictedToolExecutionError,
    ToolArgumentValidationError,
    WorkspaceExecutionError,
)
from agent_runtime_lab.ownership.authorization import WorkspaceBoundary
from agent_runtime_lab.restricted_file_tools import (
    RestrictedFileToolRunner,
    make_restricted_file_registry,
)


def test_restricted_registry_owns_exact_tool_metadata() -> None:
    registry = make_restricted_file_registry()

    assert registry.resolve("read_file").retry_is_idempotent is True
    assert registry.resolve("write_file").retry_is_idempotent is False
    assert registry.resolve("delete_file").retry_is_idempotent is False
    assert all(
        registry.resolve(name).path_argument_names == ("path",)
        for name in ("read_file", "write_file", "delete_file")
    )


def test_runner_and_registry_expose_the_same_tool_names(tmp_path: Path) -> None:
    runner = RestrictedFileToolRunner(WorkspaceBoundary(tmp_path))

    assert runner.supported_tool_names == frozenset({"read_file", "write_file", "delete_file"})


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("read_file", {}),
        ("read_file", {"path": "a.txt", "extra": True}),
        ("read_file", {"path": 1}),
        ("write_file", {"path": "a.txt"}),
        ("write_file", {"path": "a.txt", "content": 1}),
        ("delete_file", {"path": None}),
    ],
)
def test_tool_arguments_are_exact_and_strict(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    runner = RestrictedFileToolRunner(WorkspaceBoundary(tmp_path))

    with pytest.raises(ToolArgumentValidationError):
        runner.invoke(
            tool_name=tool_name,
            arguments=arguments,
            idempotency_key="effect-1",
        )


@pytest.mark.parametrize(
    "target",
    [
        "/absolute.txt",
        "C:/drive.txt",
        "../outside.txt",
        "nested/../outside.txt",
        r"nested\..\outside.txt",
    ],
)
def test_execution_rejects_non_relative_or_parent_paths(
    tmp_path: Path,
    target: str,
) -> None:
    runner = RestrictedFileToolRunner(WorkspaceBoundary(tmp_path))

    with pytest.raises(WorkspaceExecutionError):
        runner.invoke(
            tool_name="write_file",
            arguments={"path": target, "content": "blocked"},
            idempotency_key="effect-1",
        )

    assert list(tmp_path.iterdir()) == []


def test_execution_rejects_directory_target_and_missing_write_parent(tmp_path: Path) -> None:
    directory = tmp_path / "folder"
    directory.mkdir()
    runner = RestrictedFileToolRunner(WorkspaceBoundary(tmp_path))

    with pytest.raises(WorkspaceExecutionError):
        runner.invoke(
            tool_name="read_file",
            arguments={"path": "folder"},
            idempotency_key="effect-1",
        )
    with pytest.raises(WorkspaceExecutionError):
        runner.invoke(
            tool_name="write_file",
            arguments={"path": "missing/file.txt", "content": "blocked"},
            idempotency_key="effect-2",
        )

    assert not tmp_path.joinpath("missing").exists()


def test_execution_rejects_symlink_component(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc.winerror or exc.errno}")

    runner = RestrictedFileToolRunner(WorkspaceBoundary(tmp_path))
    try:
        with pytest.raises(WorkspaceExecutionError):
            runner.invoke(
                tool_name="write_file",
                arguments={"path": "linked/outside.txt", "content": "blocked"},
                idempotency_key="effect-1",
            )
        assert not outside.joinpath("outside.txt").exists()
    finally:
        link.unlink(missing_ok=True)
        outside.rmdir()


def test_read_write_and_delete_return_structured_evidence(tmp_path: Path) -> None:
    runner = RestrictedFileToolRunner(WorkspaceBoundary(tmp_path))

    write_result = runner.invoke(
        tool_name="write_file",
        arguments={"path": "notes.txt", "content": "hello"},
        idempotency_key="effect-write",
    )
    read_result = runner.invoke(
        tool_name="read_file",
        arguments={"path": "notes.txt"},
        idempotency_key="effect-read",
    )
    delete_result = runner.invoke(
        tool_name="delete_file",
        arguments={"path": "notes.txt"},
        idempotency_key="effect-delete",
    )

    digest = hashlib.sha256(b"hello").hexdigest()
    assert write_result == {
        "path": "notes.txt",
        "bytes": 5,
        "sha256": digest,
        "replaced": False,
    }
    assert read_result == {
        "path": "notes.txt",
        "content": "hello",
        "bytes": 5,
        "sha256": digest,
    }
    assert delete_result == {"path": "notes.txt", "deleted": True}
    assert not tmp_path.joinpath("notes.txt").exists()
    assert not list(tmp_path.glob(".agent-runtime-*.tmp"))


def test_write_reports_replacement_and_accepts_empty_utf8(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("old", encoding="utf-8")
    runner = RestrictedFileToolRunner(WorkspaceBoundary(tmp_path))

    result = runner.invoke(
        tool_name="write_file",
        arguments={"path": "notes.txt", "content": ""},
        idempotency_key="effect-write",
    )

    assert result["replaced"] is True
    assert result["bytes"] == 0
    assert target.read_bytes() == b""


def test_read_and_write_enforce_utf8_byte_limit(tmp_path: Path) -> None:
    runner = RestrictedFileToolRunner(WorkspaceBoundary(tmp_path), max_bytes=4)
    tmp_path.joinpath("large.txt").write_bytes(b"12345")

    with pytest.raises(RestrictedToolExecutionError, match="byte limit"):
        runner.invoke(
            tool_name="read_file",
            arguments={"path": "large.txt"},
            idempotency_key="effect-read",
        )
    with pytest.raises(ToolArgumentValidationError, match="byte limit"):
        runner.invoke(
            tool_name="write_file",
            arguments={"path": "new.txt", "content": "你好"},
            idempotency_key="effect-write",
        )

    assert not tmp_path.joinpath("new.txt").exists()


def test_read_rejects_invalid_utf8_and_missing_targets(tmp_path: Path) -> None:
    tmp_path.joinpath("binary.dat").write_bytes(b"\xff")
    runner = RestrictedFileToolRunner(WorkspaceBoundary(tmp_path))

    with pytest.raises(RestrictedToolExecutionError, match="valid UTF-8"):
        runner.invoke(
            tool_name="read_file",
            arguments={"path": "binary.dat"},
            idempotency_key="effect-read",
        )
    for tool_name in ("read_file", "delete_file"):
        with pytest.raises(WorkspaceExecutionError, match="does not exist"):
            runner.invoke(
                tool_name=tool_name,
                arguments={"path": "missing.txt"},
                idempotency_key=f"effect-{tool_name}",
            )


def test_write_failure_is_sanitized_and_cleans_staging_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RestrictedFileToolRunner(WorkspaceBoundary(tmp_path))

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError(13, "sensitive host detail", str(tmp_path))

    monkeypatch.setattr("agent_runtime_lab.restricted_file_tools.os.replace", fail_replace)

    with pytest.raises(RestrictedToolExecutionError) as raised:
        runner.invoke(
            tool_name="write_file",
            arguments={"path": "notes.txt", "content": "hello"},
            idempotency_key="effect-write",
        )

    assert str(tmp_path) not in str(raised.value)
    assert "os_code=13" in str(raised.value)
    assert not tmp_path.joinpath("notes.txt").exists()
    assert not list(tmp_path.glob(".agent-runtime-*.tmp"))
