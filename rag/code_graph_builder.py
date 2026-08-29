"""Pure in-memory Python AST builder for bounded structural graph edges."""

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
MAX_GRAPH_ISSUES = 250_000
MAX_ISSUE_REFERENCE_CHARS = 1_024


class GraphBuildIssueCode(str, Enum):
    SYNTAX_ERROR = "syntax_error"
    UNRESOLVED_IMPORT = "unresolved_import"
    SELF_IMPORT = "self_import"
    DUPLICATE_SYMBOL = "duplicate_symbol"
    UNRESOLVED_CALL = "unresolved_call"
    UNRESOLVED_BASE = "unresolved_base"
    SELF_INHERITANCE = "self_inheritance"


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


@dataclass(frozen=True, slots=True)
class _ImportBinding:
    module: str | None = None
    symbol: GraphNode | None = None


@dataclass(slots=True)
class _RelationScope:
    node: GraphNode
    qualified_name: str | None
    bound_names: set[str]
    shadowed_names: set[str]
    imports: dict[str, _ImportBinding]
    receivers: dict[str, GraphNode]


class _BoundNameCollector(ast.NodeVisitor):
    """Collect bindings owned by one Python lexical scope."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.shadowed_names: set[str] = set()
        self.outer_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)
            self.shadowed_names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".", 1)[0]
            self.names.add(name)
            self.shadowed_names.add(name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                name = alias.asname or alias.name
                self.names.add(name)
                self.shadowed_names.add(name)

    def visit_Global(self, node: ast.Global) -> None:
        self.outer_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.outer_names.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
            self.shadowed_names.add(node.name)
        self.generic_visit(node)


def _scope_bound_names(
    body: list[ast.stmt],
    arguments: ast.arguments | None = None,
) -> tuple[set[str], set[str]]:
    collector = _BoundNameCollector()
    for statement in body:
        collector.visit(statement)
    if arguments is not None:
        parameter_names = {
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            )
        }
        collector.names.update(parameter_names)
        collector.shadowed_names.update(parameter_names)
        if arguments.vararg is not None:
            collector.names.add(arguments.vararg.arg)
            collector.shadowed_names.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            collector.names.add(arguments.kwarg.arg)
            collector.shadowed_names.add(arguments.kwarg.arg)
    return (
        collector.names - collector.outer_names,
        collector.shadowed_names,
    )


def _dotted_reference(node: ast.AST) -> tuple[str, ...] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return tuple(reversed(parts))


def _unwrap_base(node: ast.expr) -> ast.expr:
    while isinstance(node, ast.Subscript):
        node = node.value
    return node


class _RelationVisitor(ast.NodeVisitor):
    """Resolve deliberately simple calls and inheritance references."""

    def __init__(
        self,
        file: str,
        tree: ast.Module,
        file_node: GraphNode,
        module_nodes: dict[str, GraphNode],
        symbols: dict[tuple[str, str], GraphNode],
        edges: list[GraphEdge],
        issues: list[GraphBuildIssue],
        seen_edges: set[str],
    ) -> None:
        self.file = file
        self.module_nodes = module_nodes
        self.module_roots = {
            module.split(".", 1)[0] for module in module_nodes if module
        }
        self.symbols = symbols
        self.edges = edges
        self.issues = issues
        self.seen_edges = seen_edges
        bound_names, shadowed_names = _scope_bound_names(tree.body)
        self.scopes = [
            _RelationScope(
                file_node,
                None,
                bound_names,
                shadowed_names,
                {},
                {},
            )
        ]

    def _issue(
        self,
        code: GraphBuildIssueCode,
        node: ast.AST,
        reference: str,
    ) -> None:
        self.issues.append(
            GraphBuildIssue(
                code,
                self.file,
                max(1, int(getattr(node, "lineno", 1))),
                reference[:MAX_ISSUE_REFERENCE_CHARS],
            )
        )

    def _add_edge(
        self,
        kind: GraphEdgeKind,
        source: GraphNode,
        target: GraphNode,
    ) -> None:
        edge = GraphEdge.create(kind, source.node_id, target.node_id)
        if edge.edge_id not in self.seen_edges:
            self.edges.append(edge)
            self.seen_edges.add(edge.edge_id)

    def _symbol(self, qualified_name: str) -> GraphNode | None:
        return self.symbols.get((self.file, qualified_name))

    def _scope_qualified_name(self, name: str) -> str:
        parent = self.scopes[-1].qualified_name
        return f"{parent}.{name}" if parent else name

    def _class_scope(self) -> _RelationScope | None:
        for scope in reversed(self.scopes):
            if scope.node.kind is GraphNodeKind.CLASS:
                return scope
        return None

    def _receiver_class(self, name: str) -> GraphNode | None:
        for scope in reversed(self.scopes):
            target = scope.receivers.get(name)
            if target is not None:
                return target
            if name in scope.bound_names:
                return None
        return None

    def _skip_class_scope(self, index: int) -> bool:
        return (
            self.scopes[index].node.kind is GraphNodeKind.CLASS
            and any(
                scope.node.kind is GraphNodeKind.FUNCTION
                for scope in self.scopes[index + 1 :]
            )
        )

    def _binding_target(
        self,
        binding: _ImportBinding,
        attributes: tuple[str, ...],
        allowed: set[GraphNodeKind],
    ) -> GraphNode | None:
        if binding.symbol is not None:
            if not attributes:
                return (
                    binding.symbol
                    if binding.symbol.kind in allowed
                    else None
                )
            qualified = ".".join(
                (binding.symbol.qualified_name, *attributes)
            )
            target = self.symbols.get((binding.symbol.file, qualified))
            return target if target is not None and target.kind in allowed else None

        if binding.module is None or not attributes:
            return None
        for module_parts in range(len(attributes) - 1, -1, -1):
            module = ".".join(
                (binding.module, *attributes[:module_parts])
            )
            module_node = self.module_nodes.get(module)
            if module_node is None:
                continue
            qualified = ".".join(attributes[module_parts:])
            target = self.symbols.get((module_node.file, qualified))
            if target is not None and target.kind in allowed:
                return target
        return None

    def _resolve_name(
        self,
        name: str,
        allowed: set[GraphNodeKind],
    ) -> GraphNode | None:
        for index in range(len(self.scopes) - 1, -1, -1):
            if self._skip_class_scope(index):
                continue
            scope = self.scopes[index]
            binding = scope.imports.get(name)
            if binding is not None:
                return self._binding_target(binding, (), allowed)
            if name in scope.shadowed_names:
                return None
            qualified = (
                f"{scope.qualified_name}.{name}"
                if scope.qualified_name
                else name
            )
            target = self._symbol(qualified)
            if target is not None and target.kind in allowed:
                return target
            if name in scope.bound_names:
                return None
        return None

    def _resolve_reference(
        self,
        node: ast.expr,
        allowed: set[GraphNodeKind],
    ) -> GraphNode | None:
        parts = _dotted_reference(node)
        if parts is None:
            return None
        if len(parts) == 1:
            if parts[0] == "cls":
                receiver_class = self._receiver_class("cls")
                if receiver_class is not None and receiver_class.kind in allowed:
                    return receiver_class
            return self._resolve_name(parts[0], allowed)

        if parts[0] in {"self", "cls"} and len(parts) == 2:
            receiver_class = self._receiver_class(parts[0])
            if receiver_class is None:
                return None
            target = self.symbols.get(
                (
                    receiver_class.file,
                    f"{receiver_class.qualified_name}.{parts[1]}",
                )
            )
            return target if target is not None and target.kind in allowed else None

        root = parts[0]
        for index in range(len(self.scopes) - 1, -1, -1):
            if self._skip_class_scope(index):
                continue
            binding = self.scopes[index].imports.get(root)
            if binding is not None:
                return self._binding_target(binding, parts[1:], allowed)
            if root in self.scopes[index].bound_names:
                break

        local_class = self._resolve_name(root, {GraphNodeKind.CLASS})
        if local_class is not None:
            qualified = ".".join((local_class.qualified_name, *parts[1:]))
            target = self.symbols.get((local_class.file, qualified))
            return target if target is not None and target.kind in allowed else None
        return None

    def _bind_import(self, node: ast.Import) -> None:
        scope = self.scopes[-1]
        for alias in node.names:
            if alias.asname:
                scope.imports.pop(alias.asname, None)
                if alias.name in self.module_nodes:
                    scope.imports[alias.asname] = _ImportBinding(
                        module=alias.name
                    )
                continue
            root = alias.name.split(".", 1)[0]
            scope.imports.pop(root, None)
            if root in self.module_roots:
                scope.imports[root] = _ImportBinding(module=root)

    def _bind_import_from(self, node: ast.ImportFrom) -> None:
        base = _relative_module(self.file, node)
        scope = self.scopes[-1]
        if base is None:
            for alias in node.names:
                if alias.name != "*":
                    scope.imports.pop(alias.asname or alias.name, None)
            return
        base_node = self.module_nodes.get(base)
        for alias in node.names:
            if alias.name == "*":
                continue
            bound_name = alias.asname or alias.name
            scope.imports.pop(bound_name, None)
            candidate = ".".join(
                part for part in (base, alias.name) if part
            )
            if candidate in self.module_nodes:
                scope.imports[bound_name] = _ImportBinding(module=candidate)
                continue
            if base_node is None:
                continue
            symbol = self.symbols.get((base_node.file, alias.name))
            if symbol is not None:
                scope.imports[bound_name] = _ImportBinding(symbol=symbol)

    def visit_Import(self, node: ast.Import) -> None:
        self._bind_import(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._bind_import_from(node)

    def visit_Call(self, node: ast.Call) -> None:
        allowed = {GraphNodeKind.FUNCTION, GraphNodeKind.CLASS}
        target = self._resolve_reference(node.func, allowed)
        if target is None:
            parts = _dotted_reference(node.func)
            self._issue(
                GraphBuildIssueCode.UNRESOLVED_CALL,
                node,
                ".".join(parts) if parts else "dynamic_call",
            )
        else:
            self._add_edge(
                GraphEdgeKind.CALLS,
                self.scopes[-1].node,
                target,
            )
        self.generic_visit(node)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for expression in (
            *node.decorator_list,
            *node.args.defaults,
            *node.args.kw_defaults,
        ):
            if expression is not None:
                self.visit(expression)
        qualified = self._scope_qualified_name(node.name)
        graph_node = self._symbol(qualified)
        if graph_node is None:
            return
        receivers: dict[str, GraphNode] = {}
        class_scope = self._class_scope()
        positional = (*node.args.posonlyargs, *node.args.args)
        if (
            self.scopes[-1].node.kind is GraphNodeKind.CLASS
            and class_scope is not None
            and positional
            and positional[0].arg in {"self", "cls"}
        ):
            receivers[positional[0].arg] = class_scope.node
        bound_names, shadowed_names = _scope_bound_names(node.body, node.args)
        self.scopes.append(
            _RelationScope(
                graph_node,
                qualified,
                bound_names,
                shadowed_names,
                {},
                receivers,
            )
        )
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = self._scope_qualified_name(node.name)
        graph_node = self._symbol(qualified)
        if graph_node is None:
            return
        for base in node.bases:
            reference = _unwrap_base(base)
            target = self._resolve_reference(reference, {GraphNodeKind.CLASS})
            parts = _dotted_reference(reference)
            label = ".".join(parts) if parts else "dynamic_base"
            if target is None:
                self._issue(GraphBuildIssueCode.UNRESOLVED_BASE, base, label)
            elif target.node_id == graph_node.node_id:
                self._issue(
                    GraphBuildIssueCode.SELF_INHERITANCE,
                    base,
                    label,
                )
            else:
                self._add_edge(
                    GraphEdgeKind.INHERITS,
                    graph_node,
                    target,
                )
            self.visit(base)
        for expression in node.decorator_list:
            self.visit(expression)
        bound_names, shadowed_names = _scope_bound_names(node.body)
        self.scopes.append(
            _RelationScope(
                graph_node,
                qualified,
                bound_names,
                shadowed_names,
                {},
                {},
            )
        )
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return


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
    """Build deterministic nodes and conservative structural edges."""
    if not isinstance(sources, Mapping):
        raise TypeError("sources must be a mapping of path to source text")
    if len(sources) > MAX_GRAPH_FILES:
        raise ValueError(f"sources exceeds the {MAX_GRAPH_FILES} file limit")

    normalized_sources: dict[str, str] = {}
    for raw_file, source in sources.items():
        file = normalize_graph_path(raw_file)
        if PurePosixPath(file).suffix != ".py":
            raise ValueError(f"code graph accepts only Python source files: {file}")
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

    symbols = {
        (node.file, node.qualified_name): node
        for node in nodes
        if node.kind is not GraphNodeKind.FILE
    }
    for file, tree in sorted(parsed.items()):
        visitor = _RelationVisitor(
            file,
            tree,
            file_nodes[file],
            module_nodes,
            symbols,
            edges,
            issues,
            seen_edges,
        )
        visitor.visit(tree)
        if len(edges) > MAX_GRAPH_EDGES:
            raise ValueError("code graph exceeds the edge limit")
        if len(issues) > MAX_GRAPH_ISSUES:
            raise ValueError("code graph exceeds the issue limit")

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
