"""Pure in-memory Python AST builder for GRAPH-002 contains/imports edges."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping

from rag.code_graph import (
    GraphEdge,
    GraphEdgeKind,
    GraphNode,
    GraphNodeKind,
    normalize_graph_path,
)


MAX_GRAPH_FILES = 10_000
MAX_SOURCE_CHARS_PER_FILE = 2_000_000
MAX_GRAPH_NODES = 100_000
MAX_GRAPH_EDGES = 250_000
MAX_ISSUE_REFERENCE_CHARS = 1_024


class GraphBuildIssueCode(str, Enum):
    SYNTAX_ERROR = "syntax_error"
    UNRESOLVED_IMPORT = "unresolved_import"
    SELF_IMPORT = "self_import"
    DUPLICATE_SYMBOL = "duplicate_symbol"


@dataclass(frozen=True, slots=True)
class GraphBuildIssue:
    code: GraphBuildIssueCode
    file: str
    line: int
    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, GraphBuildIssueCode):
            raise TypeError("code must be GraphBuildIssueCode")
        object.__setattr__(self, "file", normalize_graph_path(self.file))
        if isinstance(self.line, bool) or not isinstance(self.line, int):
            raise TypeError("line must be an integer")
        if self.line < 1:
            raise ValueError("line must be positive")
        if not isinstance(self.reference, str):
            raise TypeError("reference must be a string")
        if (
            not self.reference
            or len(self.reference) > MAX_ISSUE_REFERENCE_CHARS
            or any(char in self.reference for char in ("\n", "\r", "\0"))
        ):
            raise ValueError("reference must be bounded single-line text")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "code": self.code.value,
            "file": self.file,
            "line": self.line,
            "reference": self.reference,
        }


@dataclass(frozen=True, slots=True)
class CodeGraphBuildResult:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    issues: tuple[GraphBuildIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _module_name(file: str) -> str:
    path = PurePosixPath(file)
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _current_package(file: str) -> str:
    module = _module_name(file)
    if PurePosixPath(file).stem == "__init__":
        return module
    return module.rpartition(".")[0]


def _relative_module(file: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module or ""
    package = _current_package(file)
    if not package:
        return None
    parts = package.split(".")
    ascent = node.level - 1
    if ascent >= len(parts):
        return None
    anchor = parts[: len(parts) - ascent]
    if node.module:
        anchor.extend(node.module.split("."))
    return ".".join(anchor)


class _FileVisitor(ast.NodeVisitor):
    def __init__(
        self,
        file: str,
        file_node: GraphNode,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        issues: list[GraphBuildIssue],
        seen_node_ids: set[str],
    ) -> None:
        self.file = file
        self.file_node = file_node
        self.nodes = nodes
        self.edges = edges
        self.issues = issues
        self.seen_node_ids = seen_node_ids
        self.parents = [file_node]
        self.qualified_names: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_symbol(node, GraphNodeKind.CLASS)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_symbol(node, GraphNodeKind.FUNCTION)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_symbol(node, GraphNodeKind.FUNCTION)

    def _visit_symbol(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: GraphNodeKind,
    ) -> None:
        qualified = ".".join((*self.qualified_names, node.name))
        graph_node = GraphNode.symbol_node(
            kind,
            self.file,
            node.name,
            qualified,
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            parent_id=self.parents[-1].node_id,
        )
        if graph_node.node_id in self.seen_node_ids:
            self.issues.append(
                GraphBuildIssue(
                    GraphBuildIssueCode.DUPLICATE_SYMBOL,
                    self.file,
                    node.lineno,
                    qualified,
                )
            )
            return
        self.seen_node_ids.add(graph_node.node_id)
        self.nodes.append(graph_node)
        self.edges.append(
            GraphEdge.create(
                GraphEdgeKind.CONTAINS,
                self.parents[-1].node_id,
                graph_node.node_id,
            )
        )
        self.parents.append(graph_node)
        self.qualified_names.append(node.name)
        self.generic_visit(node)
        self.qualified_names.pop()
        self.parents.pop()


def _import_targets(
    file: str,
    node: ast.Import | ast.ImportFrom,
    module_nodes: dict[str, GraphNode],
) -> tuple[set[str], set[str]]:
    resolved: set[str] = set()
    unresolved: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name in module_nodes:
                resolved.add(alias.name)
            else:
                unresolved.add(alias.name)
        return resolved, unresolved

    base = _relative_module(file, node)
    if base is None:
        reference = "." * node.level + (node.module or "")
        return resolved, {reference or "."}
    for alias in node.names:
        candidate = ".".join(part for part in (base, alias.name) if part)
        if alias.name != "*" and candidate in module_nodes:
            resolved.add(candidate)
        elif base in module_nodes:
            resolved.add(base)
        else:
            unresolved.add(candidate or base or alias.name)
    return resolved, unresolved


def build_python_code_graph(
    sources: Mapping[str, str],
) -> CodeGraphBuildResult:
    """Build deterministic nodes and direct contains/local-import edges."""
    if not isinstance(sources, Mapping):
        raise TypeError("sources must be a mapping of path to source text")
    if len(sources) > MAX_GRAPH_FILES:
        raise ValueError(f"sources exceeds the {MAX_GRAPH_FILES} file limit")

    normalized_sources: dict[str, str] = {}
    for raw_file, source in sources.items():
        file = normalize_graph_path(raw_file)
        if PurePosixPath(file).suffix != ".py":
            raise ValueError(f"GRAPH-002 accepts only Python source files: {file}")
        if file in normalized_sources:
            raise ValueError(f"duplicate normalized source path: {file}")
        if not isinstance(source, str):
            raise TypeError(f"source for {file} must be a string")
        if len(source) > MAX_SOURCE_CHARS_PER_FILE:
            raise ValueError(f"source for {file} exceeds the size limit")
        normalized_sources[file] = source

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    issues: list[GraphBuildIssue] = []
    file_nodes: dict[str, GraphNode] = {}
    module_nodes: dict[str, GraphNode] = {}
    parsed: dict[str, ast.Module] = {}

    for file, source in sorted(normalized_sources.items()):
        end_line = max(1, len(source.splitlines()))
        file_node = GraphNode.file_node(file, end_line=end_line)
        file_nodes[file] = file_node
        nodes.append(file_node)
        module = _module_name(file)
        if module in module_nodes:
            raise ValueError(f"ambiguous Python module mapping: {module or '<root>'}")
        module_nodes[module] = file_node
        try:
            parsed[file] = ast.parse(source, filename=file)
        except SyntaxError as exc:
            issues.append(
                GraphBuildIssue(
                    GraphBuildIssueCode.SYNTAX_ERROR,
                    file,
                    max(1, int(exc.lineno or 1)),
                    "invalid_python_syntax",
                )
            )

    seen_node_ids = {node.node_id for node in nodes}
    imports_by_file: dict[str, list[ast.Import | ast.ImportFrom]] = {}
    for file, tree in sorted(parsed.items()):
        visitor = _FileVisitor(
            file, file_nodes[file], nodes, edges, issues, seen_node_ids
        )
        visitor.visit(tree)
        imports_by_file[file] = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        if len(nodes) > MAX_GRAPH_NODES or len(edges) > MAX_GRAPH_EDGES:
            raise ValueError("code graph exceeds node or edge limits")

    seen_edges = {edge.edge_id for edge in edges}
    for file, imports in sorted(imports_by_file.items()):
        source_node = file_nodes[file]
        for import_node in imports:
            resolved, unresolved = _import_targets(
                file, import_node, module_nodes
            )
            for module in sorted(resolved):
                target = module_nodes[module]
                if target.node_id == source_node.node_id:
                    issues.append(
                        GraphBuildIssue(
                            GraphBuildIssueCode.SELF_IMPORT,
                            file,
                            import_node.lineno,
                            module or ".",
                        )
                    )
                    continue
                edge = GraphEdge.create(
                    GraphEdgeKind.IMPORTS,
                    source_node.node_id,
                    target.node_id,
                )
                if edge.edge_id not in seen_edges:
                    edges.append(edge)
                    seen_edges.add(edge.edge_id)
            for reference in sorted(unresolved):
                issues.append(
                    GraphBuildIssue(
                        GraphBuildIssueCode.UNRESOLVED_IMPORT,
                        file,
                        import_node.lineno,
                        reference[:MAX_ISSUE_REFERENCE_CHARS],
                    )
                )
            if len(edges) > MAX_GRAPH_EDGES:
                raise ValueError("code graph exceeds the edge limit")

    nodes.sort(
        key=lambda node: (
            node.file,
            node.start_line,
            node.kind.value,
            node.node_id,
        )
    )
    edges.sort(key=lambda edge: (edge.kind.value, edge.source_id, edge.target_id))
    issues.sort(
        key=lambda issue: (
            issue.file,
            issue.line,
            issue.code.value,
            issue.reference,
        )
    )
    return CodeGraphBuildResult(tuple(nodes), tuple(edges), tuple(issues))
