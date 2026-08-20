"""Restricted UTF-8 file tools for a trusted temporary workspace."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from agent_runtime_lab.domain.errors import (
    RestrictedToolExecutionError,
    ToolArgumentValidationError,
    UnknownToolError,
    WorkspaceExecutionError,
)
from agent_runtime_lab.ownership.authorization import WorkspaceBoundary
from agent_runtime_lab.tool_registry import ToolDefinition, ToolRegistry

RESTRICTED_FILE_TOOL_DEFINITIONS = (
    ToolDefinition("read_file", retry_is_idempotent=True, path_argument_names=("path",)),
    ToolDefinition("write_file", retry_is_idempotent=False, path_argument_names=("path",)),
    ToolDefinition("delete_file", retry_is_idempotent=False, path_argument_names=("path",)),
)
DEFAULT_MAX_BYTES = 1024 * 1024
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def make_restricted_file_registry() -> ToolRegistry:
    """Build the canonical trusted registry for restricted file tools."""

    return ToolRegistry(RESTRICTED_FILE_TOOL_DEFINITIONS)


class RestrictedFileToolRunner:
    """Dispatch restricted file effects inside one workspace boundary."""

    supported_tool_names = frozenset(
        definition.tool_name for definition in RESTRICTED_FILE_TOOL_DEFINITIONS
    )

    def __init__(
        self,
        workspace: WorkspaceBoundary,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self._workspace = workspace
        self._max_bytes = max_bytes

    def invoke(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        """Dispatch one canonical file tool."""

        del idempotency_key
        handlers = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "delete_file": self._delete_file,
        }
        try:
            handler = handlers[tool_name]
        except KeyError:
            raise UnknownToolError(f"tool {tool_name!r} is not registered") from None
        return handler(arguments)

    def _read_file(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self._require_exact_arguments("read_file", arguments, frozenset({"path"}))
        original_path = self._require_text(arguments, "path")
        relative_path, target = self._resolve_for_execution(original_path)
        self._require_regular_file(target, relative_path, operation="read_file")

        try:
            with target.open("rb") as stream:
                data = stream.read(self._max_bytes + 1)
        except OSError as exc:
            self._raise_os_error("read_file", relative_path, exc)

        if len(data) > self._max_bytes:
            raise RestrictedToolExecutionError(
                f"read_file target {relative_path!r} exceeds the byte limit"
            )
        try:
            content = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RestrictedToolExecutionError(
                f"read_file target {relative_path!r} is not valid UTF-8"
            ) from exc
        return {
            "path": relative_path,
            "content": content,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _write_file(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self._require_exact_arguments(
            "write_file",
            arguments,
            frozenset({"path", "content"}),
        )
        original_path = self._require_text(arguments, "path")
        content = self._require_text(arguments, "content", allow_empty=True)
        try:
            data = content.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ToolArgumentValidationError("write_file content must be valid UTF-8") from exc
        if len(data) > self._max_bytes:
            raise ToolArgumentValidationError("write_file content exceeds the byte limit")

        relative_path, target = self._resolve_for_execution(original_path)
        parent_relative = PurePosixPath(relative_path).parent.as_posix()
        self._require_directory(target.parent, parent_relative, operation="write_file")
        replaced = os.path.lexists(target)
        if replaced:
            self._require_regular_file(target, relative_path, operation="write_file")

        staging_path: Path | None = None
        try:
            descriptor, staging_name = tempfile.mkstemp(
                prefix=".agent-runtime-",
                suffix=".tmp",
                dir=target.parent,
            )
            staging_path = Path(staging_name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging_path, target)
        except OSError as exc:
            self._raise_os_error("write_file", relative_path, exc)
        finally:
            if staging_path is not None:
                try:
                    staging_path.unlink(missing_ok=True)
                except OSError:
                    pass

        return {
            "path": relative_path,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "replaced": replaced,
        }

    def _delete_file(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self._require_exact_arguments("delete_file", arguments, frozenset({"path"}))
        original_path = self._require_text(arguments, "path")
        relative_path, target = self._resolve_for_execution(original_path)
        self._require_regular_file(target, relative_path, operation="delete_file")
        try:
            target.unlink()
        except OSError as exc:
            self._raise_os_error("delete_file", relative_path, exc)
        return {"path": relative_path, "deleted": True}

    @staticmethod
    def _require_exact_arguments(
        tool_name: str,
        arguments: Mapping[str, Any],
        expected: frozenset[str],
    ) -> None:
        if frozenset(arguments) != expected:
            expected_names = ", ".join(sorted(expected))
            raise ToolArgumentValidationError(
                f"{tool_name} requires exactly these arguments: {expected_names}"
            )

    @staticmethod
    def _require_text(
        arguments: Mapping[str, Any],
        field: str,
        *,
        allow_empty: bool = False,
    ) -> str:
        value = arguments[field]
        if not isinstance(value, str) or (not allow_empty and not value):
            requirement = "a string" if allow_empty else "a non-empty string"
            raise ToolArgumentValidationError(f"{field} must be {requirement}")
        return value

    def _resolve_for_execution(self, original_path: str) -> tuple[str, Path]:
        portable_path = original_path.replace("\\", "/")
        original_parts = PurePosixPath(portable_path).parts
        if ".." in original_parts:
            raise WorkspaceExecutionError("workspace path must not contain parent traversal")
        try:
            relative_path = self._workspace.normalize(original_path)
        except ValueError as exc:
            raise WorkspaceExecutionError(
                "workspace path violates the configured boundary"
            ) from exc

        current = self._workspace.root
        walked: list[str] = []
        for part in original_parts:
            if part in ("", "."):
                continue
            current = current / part
            walked.append(part)
            if not os.path.lexists(current):
                continue
            walked_relative = PurePosixPath(*walked).as_posix()
            try:
                metadata = os.lstat(current)
            except OSError as exc:
                self._raise_os_error("inspect", walked_relative, exc)
            if stat.S_ISLNK(metadata.st_mode) or (
                getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
            ):
                raise WorkspaceExecutionError(
                    f"workspace path component {walked_relative!r} is a link or reparse point"
                )

        return relative_path, self._workspace.root / relative_path

    def _require_regular_file(self, target: Path, relative_path: str, *, operation: str) -> None:
        if not os.path.lexists(target):
            raise WorkspaceExecutionError(f"{operation} target {relative_path!r} does not exist")
        try:
            metadata = os.lstat(target)
        except OSError as exc:
            self._raise_os_error(operation, relative_path, exc)
        if not stat.S_ISREG(metadata.st_mode):
            raise WorkspaceExecutionError(
                f"{operation} target {relative_path!r} must be a regular file"
            )

    def _require_directory(self, target: Path, relative_path: str, *, operation: str) -> None:
        if not os.path.lexists(target):
            raise WorkspaceExecutionError(f"{operation} parent {relative_path!r} does not exist")
        try:
            metadata = os.lstat(target)
        except OSError as exc:
            self._raise_os_error(operation, relative_path, exc)
        if not stat.S_ISDIR(metadata.st_mode):
            raise WorkspaceExecutionError(
                f"{operation} parent {relative_path!r} must be a directory"
            )

    @staticmethod
    def _raise_os_error(operation: str, relative_path: str, exc: OSError) -> None:
        code = getattr(exc, "winerror", None) or exc.errno or "unknown"
        raise RestrictedToolExecutionError(
            f"{operation} failed for {relative_path!r} (os_code={code})"
        ) from exc
