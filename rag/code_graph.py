"""Bounded, content-free node contracts for the lightweight Python code graph.

GRAPH-001 defines identity and serialization only.  AST parsing and graph edges
belong to later tasks; this module performs no file I/O and stores no source.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import PurePosixPath
import re
from typing import Any


GRAPH_NODE_SCHEMA_VERSION = 1
GRAPH_LANGUAGE = "python"
MAX_GRAPH_PATH_CHARS = 1_024
MAX_GRAPH_NAME_CHARS = 256
MAX_QUALIFIED_NAME_CHARS = 1_024
MAX_SOURCE_LINE = 10_000_000
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:/")
_NODE_ID = re.compile(r"^py:(?:file|class|function):[0-9a-f]{64}$")


class GraphNodeKind(str, Enum):
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"


def normalize_graph_path(value: object) -> str:
    """Return a bounded repository-relative POSIX path."""
    if not isinstance(value, str):
        raise TypeError("file must be a string")
    raw = value.replace("\\", "/")
    if not raw or len(raw) > MAX_GRAPH_PATH_CHARS:
        raise ValueError("file must be non-empty and bounded")
    if raw.startswith("/") or _WINDOWS_ABSOLUTE.match(raw):
        raise ValueError("file must be repository-relative")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("file must not contain empty, dot, or traversal segments")
    normalized = PurePosixPath(*parts).as_posix()
    if any(character in normalized for character in ("\n", "\r", "\0")):
        raise ValueError("file must be a single-line text path")
    return normalized


def _bounded_text(name: str, value: object, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or len(value) > maximum:
        raise ValueError(f"{name} must be non-empty and bounded")
    if any(character in value for character in ("\n", "\r", "\0")):
        raise ValueError(f"{name} must be single-line text")
    return value


def stable_graph_node_id(
    kind: GraphNodeKind, file: str, qualified_name: str
) -> str:
    """Create a deterministic identity that survives source-line movement."""
    if not isinstance(kind, GraphNodeKind):
        raise TypeError("kind must be GraphNodeKind")
    normalized_file = normalize_graph_path(file)
    normalized_name = _bounded_text(
        "qualified_name", qualified_name, MAX_QUALIFIED_NAME_CHARS
    )
    canonical = "\0".join((GRAPH_LANGUAGE, kind.value, normalized_file, normalized_name))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"py:{kind.value}:{digest}"


@dataclass(frozen=True, slots=True)
class GraphNode:
    """Immutable file/class/function identity and location metadata."""

    node_id: str
    kind: GraphNodeKind
    name: str
    qualified_name: str
    file: str
    start_line: int
    end_line: int
    parent_id: str | None
    language: str = GRAPH_LANGUAGE
    schema_version: int = GRAPH_NODE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.kind, GraphNodeKind):
            raise TypeError("kind must be GraphNodeKind")
        file = normalize_graph_path(self.file)
        name = _bounded_text("name", self.name, MAX_GRAPH_NAME_CHARS)
        qualified_name = _bounded_text(
            "qualified_name", self.qualified_name, MAX_QUALIFIED_NAME_CHARS
        )
        for field_name, value in (
            ("start_line", self.start_line),
            ("end_line", self.end_line),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")
            if not 1 <= value <= MAX_SOURCE_LINE:
                raise ValueError(f"{field_name} is outside the supported range")
        if self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        if self.language != GRAPH_LANGUAGE:
            raise ValueError(f"language must be {GRAPH_LANGUAGE}")
        if self.schema_version != GRAPH_NODE_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {GRAPH_NODE_SCHEMA_VERSION}"
            )

        expected_id = stable_graph_node_id(self.kind, file, qualified_name)
        if not isinstance(self.node_id, str) or not _NODE_ID.fullmatch(self.node_id):
            raise ValueError("node_id has an invalid format")
        if self.node_id != expected_id:
            raise ValueError("node_id does not match kind/file/qualified_name")

        if self.kind is GraphNodeKind.FILE:
            if self.parent_id is not None:
                raise ValueError("file nodes must not have a parent_id")
            if name != PurePosixPath(file).name or qualified_name != file:
                raise ValueError("file node name and qualified_name must match file")
        else:
            if not name.isidentifier():
                raise ValueError("symbol name must be a valid Python identifier")
            qualified_parts = qualified_name.split(".")
            if not all(part.isidentifier() for part in qualified_parts):
                raise ValueError(
                    "qualified_name must contain dotted Python identifiers"
                )
            if qualified_parts[-1] != name:
                raise ValueError("qualified_name must end with the symbol name")
            if not isinstance(self.parent_id, str) or not _NODE_ID.fullmatch(
                self.parent_id
            ):
                raise ValueError("symbol nodes require a valid parent_id")
            if self.parent_id == self.node_id:
                raise ValueError("a node cannot be its own parent")

        object.__setattr__(self, "file", file)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "qualified_name", qualified_name)

    @classmethod
    def file_node(cls, file: str, *, end_line: int) -> "GraphNode":
        normalized = normalize_graph_path(file)
        name = PurePosixPath(normalized).name
        return cls(
            node_id=stable_graph_node_id(
                GraphNodeKind.FILE, normalized, normalized
            ),
            kind=GraphNodeKind.FILE,
            name=name,
            qualified_name=normalized,
            file=normalized,
            start_line=1,
            end_line=end_line,
            parent_id=None,
        )

    @classmethod
    def symbol_node(
        cls,
        kind: GraphNodeKind,
        file: str,
        name: str,
        qualified_name: str,
        *,
        start_line: int,
        end_line: int,
        parent_id: str,
    ) -> "GraphNode":
        if kind not in {GraphNodeKind.CLASS, GraphNodeKind.FUNCTION}:
            raise ValueError("symbol_node kind must be class or function")
        normalized = normalize_graph_path(file)
        return cls(
            node_id=stable_graph_node_id(kind, normalized, qualified_name),
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            file=normalized,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "node_id": self.node_id,
            "kind": self.kind.value,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "parent_id": self.parent_id,
            "language": self.language,
        }
