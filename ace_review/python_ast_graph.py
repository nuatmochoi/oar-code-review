"""Conservative Python AST call/argument enrichment for EvidenceGraph."""
from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode
from .schema import estimate_tokens


def _id(kind: str, identity: str) -> str:
    return "PYAST-" + kind.upper() + "-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _module(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@dataclass(frozen=True)
class _Function:
    symbol: str
    file: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    source: str
    parameters: tuple[str, ...]
    positional_count: int
    positional_only_count: int


@dataclass(frozen=True)
class _Binding:
    symbol: str
    name: str
    file: str
    node: ast.Assign | ast.AnnAssign
    source: str


@dataclass(frozen=True)
class _LocalAlias:
    name: str
    source_name: str
    node: ast.Assign | ast.AnnAssign


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self, module: str, file: str, source: str):
        self.module, self.file, self.source = module, file, source
        self.scope: list[str] = []
        self.functions: list[_Function] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        symbol = ".".join(part for part in (self.module, *self.scope, node.name) if part)
        positional = (*node.args.posonlyargs, *node.args.args)
        args = (*positional, *node.args.kwonlyargs)
        self.functions.append(_Function(
            symbol, self.file, node, self.source, tuple(arg.arg for arg in args),
            len(positional), len(node.args.posonlyargs),
        ))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


class _CallCollector(ast.NodeVisitor):
    def __init__(self):
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None


class _AccessCollector(ast.NodeVisitor):
    def __init__(self):
        self.loads: set[str] = set()
        self.stores: set[str] = set()
        self.globals: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.loads.add(node.id)
        elif isinstance(node.ctx, (ast.Store, ast.Del)):
            self.stores.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        name = _call_name(node)
        if isinstance(node.ctx, ast.Load) and name:
            self.loads.add(name)
            return
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None


class _AssignmentCollector(ast.NodeVisitor):
    def __init__(self):
        self.assignments: dict[str, list[ast.Assign | ast.AnnAssign]] = {}

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.assignments.setdefault(target.id, []).append(node)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.assignments.setdefault(node.target.id, []).append(node)
        if node.value is not None:
            self.generic_visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None


class _WriteCollector(ast.NodeVisitor):
    def __init__(self):
        self.counts: dict[str, int] = {}
        self.nonlocals: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.counts[node.id] = self.counts.get(node.id, 0) + 1

    def visit_Global(self, node: ast.Global) -> None:
        self.nonlocals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocals.update(node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None


def _calls(function: _Function) -> list[ast.Call]:
    collector = _CallCollector()
    for statement in function.node.body:
        collector.visit(statement)
    return collector.calls


def _accesses(function: _Function) -> set[str]:
    collector = _AccessCollector()
    for statement in function.node.body:
        collector.visit(statement)
    local_names = (set(function.parameters) | collector.stores) - collector.globals
    return {name for name in collector.loads if name.split(".", 1)[0] not in local_names}


def _local_aliases(function: _Function) -> tuple[list[_LocalAlias], int, int]:
    collector = _AssignmentCollector()
    writes = _WriteCollector()
    for statement in function.node.body:
        collector.visit(statement)
        writes.visit(statement)
    aliases: list[_LocalAlias] = []
    ambiguous = 0
    nonidentity = 0
    for name, write_count in writes.counts.items():
        if name in writes.nonlocals:
            continue
        assignments = collector.assignments.get(name, [])
        if write_count != 1:
            ambiguous += 1
            continue
        if len(assignments) != 1:
            nonidentity += 1
            continue
        assignment = assignments[0]
        value = assignment.value
        if not isinstance(value, ast.Name):
            nonidentity += 1
            continue
        aliases.append(_LocalAlias(name, value.id, assignment))
    return sorted(aliases, key=lambda alias: alias.node.lineno), ambiguous, nonidentity


def _bindings(module: str, file: str, tree: ast.AST, source: str) -> tuple[list[_Binding], int]:
    candidates: dict[str, list[ast.Assign | ast.AnnAssign]] = {}
    for node in getattr(tree, "body", []):
        names: list[str] = []
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        for name in names:
            candidates.setdefault(name, []).append(node)
    unique = [
        _Binding(".".join(part for part in (module, name) if part), name, file, nodes[0], source)
        for name, nodes in candidates.items() if len(nodes) == 1
    ]
    ambiguous = sum(len(nodes) != 1 for nodes in candidates.values())
    return unique, ambiguous


def _imports(tree: ast.AST, module: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.ImportFrom) and node.module:
            prefix = node.module
            if node.level and module:
                package = module.split(".")[:-node.level]
                prefix = ".".join((*package, node.module))
            for alias in node.names:
                result[alias.asname or alias.name] = prefix + "." + alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                result[alias.asname or alias.name.split(".")[0]] = alias.name
    return result


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return prefix + "." + node.attr if prefix else None
    return None


def _resolve_name(name: str, module: str, aliases: dict[str, str]) -> str:
    head, *tail = name.split(".")
    prefix = aliases.get(head, ".".join(part for part in (module, head) if part))
    return ".".join((prefix, *tail))


def _parse_snapshot(root: Path) -> tuple[
    dict[str, tuple[str, ast.AST, str]], list[_Function], list[_Binding], int,
]:
    parsed: dict[str, tuple[str, ast.AST, str]] = {}
    functions: list[_Function] = []
    bindings: list[_Binding] = []
    ambiguous_bindings = 0
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink():
            raise ValueError(f"symlink is not allowed in AST source: {path}")
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as error:
            raise ValueError(f"cannot parse {relative}: {error.msg}") from error
        module = _module(Path(relative))
        parsed[relative] = (module, tree, source)
        collector = _FunctionCollector(module, relative, source)
        collector.visit(tree)
        functions.extend(collector.functions)
        module_bindings, ambiguous = _bindings(module, relative, tree, source)
        bindings.extend(module_bindings)
        ambiguous_bindings += ambiguous
    return parsed, functions, bindings, ambiguous_bindings


def _is_changed(
    file: str,
    node: ast.AST,
    changed_files: set[str],
    changed_lines: dict[str, set[int]] | None,
) -> bool:
    if changed_lines is None:
        return file in changed_files
    line_range = set(range(node.lineno, node.end_lineno + 1))
    return bool(line_range & changed_lines.get(file, set()))


def _add_binding_nodes(
    bindings: list[_Binding], nodes: dict[str, EvidenceNode], *, revision: str,
    changed_files: set[str], changed_lines: dict[str, set[int]] | None,
) -> None:
    for binding in bindings:
        binding_id = _id("binding", binding.symbol)
        content = ast.get_source_segment(binding.source, binding.node) or ""
        is_changed = _is_changed(binding.file, binding.node, changed_files, changed_lines)
        nodes[binding_id] = EvidenceNode(
            id=binding_id, kind="binding",
            role="diff_seed" if is_changed else (
                "config_source" if binding.name.isupper() else "producer"
            ),
            file=binding.file, start_line=binding.node.lineno, end_line=binding.node.end_lineno,
            revision=revision, symbol=binding.symbol, content=content,
            token_cost=estimate_tokens(content), changed=is_changed,
            attributes={"provider": "python_ast", "binding_semantics": "module_level_single_assignment"},
        )


def _add_function_nodes(
    functions: list[_Function], nodes: dict[str, EvidenceNode],
    edges: dict[tuple[str, str, str], EvidenceEdge], *, revision: str,
    changed_files: set[str], changed_lines: dict[str, set[int]] | None,
) -> tuple[dict[str, bool], dict[tuple[str, int], str], dict[tuple[str, str], str]]:
    function_changed = {
        function.symbol: _is_changed(function.file, function.node, changed_files, changed_lines)
        for function in functions
    }
    parameter_ids: dict[tuple[str, int], str] = {}
    parameter_name_ids: dict[tuple[str, str], str] = {}
    for function in functions:
        function_id = _id("function", function.symbol)
        content = ast.get_source_segment(function.source, function.node) or ""
        nodes[function_id] = EvidenceNode(
            id=function_id, kind="function",
            role="diff_seed" if function_changed[function.symbol] else "semantic_endpoint",
            file=function.file, start_line=function.node.lineno, end_line=function.node.end_lineno,
            revision=revision, symbol=function.symbol, content=content,
            token_cost=estimate_tokens(content), changed=function_changed[function.symbol],
            attributes={"provider": "python_ast", "role_semantics": (
                "diff_line_overlap" if changed_lines is not None else "changed_file_membership_only"
            )},
        )
        for index, name in enumerate(function.parameters):
            identity = f"{function.symbol}:parameter:{index}:{name}"
            parameter_id = _id("parameter", identity)
            if index < function.positional_count:
                parameter_ids[(function.symbol, index)] = parameter_id
            if index >= function.positional_only_count:
                parameter_name_ids[(function.symbol, name)] = parameter_id
            nodes[parameter_id] = EvidenceNode(
                id=parameter_id, kind="parameter", role="transform", file=function.file,
                start_line=function.node.lineno, end_line=function.node.lineno,
                revision=revision, symbol=identity, content=name,
                token_cost=estimate_tokens(name), changed=function_changed[function.symbol],
                attributes={"provider": "python_ast", "position": index},
            )
            edge = EvidenceEdge(parameter_id, function_id, "parameter_of", revision=revision,
                                attributes={"provider": "python_ast", "relation_family": "call"})
            edges[edge.key] = edge
    return function_changed, parameter_ids, parameter_name_ids


def _binding_source_id(
    name: str, module: str, aliases: dict[str, str], binding_definitions: dict[str, _Binding],
) -> str | None:
    binding = binding_definitions.get(_resolve_name(name, module, aliases))
    return _id("binding", binding.symbol) if binding else None


def _add_local_alias_nodes(
    function: _Function, module: str, aliases: dict[str, str],
    binding_definitions: dict[str, _Binding],
    nodes: dict[str, EvidenceNode], edges: dict[tuple[str, str, str], EvidenceEdge],
    *, revision: str, changed_files: set[str], changed_lines: dict[str, set[int]] | None,
) -> tuple[dict[str, str], int, int, int]:
    sources = {
        name: _id("parameter", f"{function.symbol}:parameter:{index}:{name}")
        for index, name in enumerate(function.parameters)
    }
    local_aliases, ambiguous, nonidentity = _local_aliases(function)
    unresolved = 0
    for alias in local_aliases:
        source_id = sources.get(alias.source_name) or _binding_source_id(
            alias.source_name, module, aliases, binding_definitions,
        )
        if source_id is None:
            unresolved += 1
            continue
        identity = f"{function.symbol}:local:{alias.name}"
        local_id = _id("local", identity)
        content = ast.get_source_segment(function.source, alias.node) or ""
        alias_changed = _is_changed(function.file, alias.node, changed_files, changed_lines)
        nodes[local_id] = EvidenceNode(
            id=local_id, kind="local", role="transform", file=function.file,
            start_line=alias.node.lineno, end_line=alias.node.end_lineno,
            revision=revision, symbol=identity, content=content,
            token_cost=estimate_tokens(content), changed=alias_changed,
            attributes={"provider": "python_ast", "alias_semantics": "single_identity_assignment"},
        )
        edge = EvidenceEdge(
            source_id, local_id, "assigned_to", revision=revision, confidence=0.8,
            attributes={"provider": "python_ast", "relation_family": "data_flow",
                        "flow_kind": "may", "path_sensitivity": "none",
                        "semantics": "syntactic_identity_assignment"},
        )
        edges[edge.key] = edge
        sources[alias.name] = local_id
    return sources, ambiguous, nonidentity, unresolved


def _add_binding_read_edges(
    function: _Function, module: str, aliases: dict[str, str],
    binding_definitions: dict[str, _Binding],
    edges: dict[tuple[str, str, str], EvidenceEdge], *, revision: str,
) -> None:
    reader_id = _id("function", function.symbol)
    for name in sorted(_accesses(function)):
        binding = binding_definitions.get(_resolve_name(name, module, aliases))
        if binding is None:
            continue
        edge = EvidenceEdge(
            _id("binding", binding.symbol), reader_id, "binding_read_by", revision=revision,
            attributes={"provider": "python_ast", "relation_family": "config",
                        "read_name": name,
                        "semantics": "static_name_resolution_not_runtime_value_proof"},
        )
        edges[edge.key] = edge


def _call_arguments(
    call: ast.Call, resolved_symbol: str, resolved: bool,
    parameter_ids: dict[tuple[str, int], str], parameter_name_ids: dict[tuple[str, str], str],
) -> list[tuple[str, ast.expr, str | None, dict[str, int | str]]]:
    records = [
        (f"argument:{index}", argument,
         parameter_ids.get((resolved_symbol, index)) if resolved else None,
         {"position": index})
        for index, argument in enumerate(call.args)
    ]
    records.extend(
        (f"keyword:{keyword.arg}", keyword.value,
         parameter_name_ids.get((resolved_symbol, keyword.arg)) if resolved else None,
         {"keyword": keyword.arg})
        for keyword in call.keywords if keyword.arg is not None
    )
    return records


def _add_argument_edges(
    call: ast.Call, call_identity: str, call_id: str, function: _Function,
    module: str, aliases: dict[str, str], resolved_symbol: str, resolved: bool,
    parameter_ids: dict[tuple[str, int], str], parameter_name_ids: dict[tuple[str, str], str],
    local_sources: dict[str, str], binding_definitions: dict[str, _Binding],
    nodes: dict[str, EvidenceNode], edges: dict[tuple[str, str, str], EvidenceEdge],
    *, revision: str, changed_files: set[str], changed_lines: dict[str, set[int]] | None,
    boundary_producer: bool,
) -> None:
    for suffix, argument, parameter_id, argument_attributes in _call_arguments(
        call, resolved_symbol, resolved, parameter_ids, parameter_name_ids,
    ):
        argument_identity = f"{call_identity}:{suffix}"
        argument_id = _id("argument", argument_identity)
        argument_text = ast.get_source_segment(function.source, argument) or ""
        argument_changed = _is_changed(function.file, argument, changed_files, changed_lines)
        boundary_attributes = ({
            "boundary_role": "unchanged_caller_argument_to_changed_callee",
        } if boundary_producer else {})
        nodes[argument_id] = EvidenceNode(
            id=argument_id, kind="argument",
            role="producer" if boundary_producer else "transform", file=function.file,
            start_line=argument.lineno, end_line=argument.end_lineno, revision=revision,
            symbol=argument_identity, content=argument_text,
            token_cost=estimate_tokens(argument_text), changed=argument_changed,
            attributes={"provider": "python_ast", **argument_attributes, **boundary_attributes},
        )
        contains_argument = EvidenceEdge(
            call_id, argument_id, "contains_argument", revision=revision,
            attributes={"provider": "python_ast", "relation_family": "call"},
        )
        edges[contains_argument.key] = contains_argument
        value_name = _call_name(argument)
        value_source = local_sources.get(value_name or "")
        if value_source is None and value_name:
            value_source = _binding_source_id(value_name, module, aliases, binding_definitions)
        if value_source:
            flow = EvidenceEdge(
                value_source, argument_id, "value_flows_to", revision=revision, confidence=0.8,
                attributes={"provider": "python_ast", "relation_family": "data_flow",
                            "flow_kind": "may", "path_sensitivity": "none",
                            "semantics": "simple_name_argument_flow"},
            )
            edges[flow.key] = flow
        if parameter_id:
            binding = EvidenceEdge(
                argument_id, parameter_id, "argument_parameter", revision=revision,
                attributes={"provider": "python_ast", "relation_family": "call",
                            **argument_attributes},
            )
            edges[binding.key] = binding


def _add_call_edges(
    function: _Function, module: str, aliases: dict[str, str],
    definitions: dict[str, _Function], function_changed: dict[str, bool],
    parameter_ids: dict[tuple[str, int], str],
    parameter_name_ids: dict[tuple[str, str], str], local_sources: dict[str, str],
    binding_definitions: dict[str, _Binding],
    nodes: dict[str, EvidenceNode], edges: dict[tuple[str, str, str], EvidenceEdge],
    *, revision: str, changed_files: set[str], changed_lines: dict[str, set[int]] | None,
) -> int:
    unresolved = 0
    caller_id = _id("function", function.symbol)
    for call in _calls(function):
        identity = f"{function.symbol}:call:{call.lineno}:{call.col_offset}"
        call_id = _id("call", identity)
        segment = ast.get_source_segment(function.source, call) or ""
        call_changed = _is_changed(function.file, call, changed_files, changed_lines)
        nodes[call_id] = EvidenceNode(
            id=call_id, kind="call", role="transform", file=function.file,
            start_line=call.lineno, end_line=call.end_lineno, revision=revision,
            symbol=identity, content=segment, token_cost=estimate_tokens(segment),
            changed=call_changed, attributes={"provider": "python_ast"},
        )
        contains = EvidenceEdge(caller_id, call_id, "contains_call", revision=revision,
                                attributes={"provider": "python_ast", "relation_family": "call"})
        edges[contains.key] = contains
        raw_name = _call_name(call.func)
        resolved_symbol = _resolve_name(raw_name, module, aliases) if raw_name else ""
        resolved = resolved_symbol in definitions
        caller_changed = _is_changed(function.file, function.node, changed_files, changed_lines)
        boundary_producer = resolved and not caller_changed and function_changed[resolved_symbol]
        if resolved:
            callee_id = _id("function", resolved_symbol)
        else:
            unresolved += 1
            external_symbol = "external:" + (raw_name or ast.dump(call.func, include_attributes=False))
            callee_id = _id("external", external_symbol)
            nodes.setdefault(callee_id, EvidenceNode(
                id=callee_id, kind="external", role="semantic_endpoint", file=function.file,
                start_line=call.lineno, end_line=call.end_lineno, revision=revision,
                symbol=external_symbol, confidence=0.0,
                attributes={"provider": "python_ast", "unresolved_reason": "definition_not_indexed"},
            ))
        unresolved_attributes = {} if resolved else {"unresolved_reason": "definition_not_indexed"}
        for source, edge_type in ((caller_id, "calls"), (call_id, "callsite_resolves_to")):
            edge = EvidenceEdge(
                source, callee_id, edge_type, revision=revision,
                confidence=1.0 if resolved else 0.0, resolved=resolved,
                attributes={"provider": "python_ast", "relation_family": "call",
                            **unresolved_attributes},
            )
            edges[edge.key] = edge
        _add_argument_edges(
            call, identity, call_id, function, module, aliases, resolved_symbol, resolved,
            parameter_ids, parameter_name_ids, local_sources, binding_definitions, nodes, edges,
            revision=revision, changed_files=changed_files, changed_lines=changed_lines,
            boundary_producer=boundary_producer,
        )
    return unresolved


def build_python_ast_graph(
    snapshot: str | Path,
    *,
    revision: str,
    changed_files: Iterable[str] = (),
    changed_lines: dict[str, set[int]] | None = None,
) -> EvidenceGraph:
    if revision not in {"base", "head"}:
        raise ValueError("AST revision must be base or head")
    root = Path(snapshot).resolve()
    if not root.is_dir():
        raise ValueError(f"snapshot is not a directory: {root}")
    changed = set(changed_files)
    parsed, functions, bindings, ambiguous_bindings = _parse_snapshot(root)
    definitions = {function.symbol: function for function in functions}
    binding_definitions = {binding.symbol: binding for binding in bindings}
    nodes: dict[str, EvidenceNode] = {}
    edges: dict[tuple[str, str, str], EvidenceEdge] = {}
    _add_binding_nodes(bindings, nodes, revision=revision, changed_files=changed,
                       changed_lines=changed_lines)
    function_changed, parameter_ids, parameter_name_ids = _add_function_nodes(
        functions, nodes, edges, revision=revision, changed_files=changed,
        changed_lines=changed_lines,
    )
    unresolved = 0
    ambiguous_local_aliases = 0
    nonidentity_local_assignments = 0
    unresolved_local_aliases = 0
    for function in functions:
        module, tree, _ = parsed[function.file]
        aliases = _imports(tree, module)
        _add_binding_read_edges(function, module, aliases, binding_definitions, edges,
                                revision=revision)
        local_sources, ambiguous, nonidentity, unresolved_aliases = _add_local_alias_nodes(
            function, module, aliases, binding_definitions, nodes, edges,
            revision=revision, changed_files=changed, changed_lines=changed_lines,
        )
        ambiguous_local_aliases += ambiguous
        nonidentity_local_assignments += nonidentity
        unresolved_local_aliases += unresolved_aliases
        unresolved += _add_call_edges(
            function, module, aliases, definitions, function_changed, parameter_ids, parameter_name_ids,
            local_sources, binding_definitions, nodes, edges,
            revision=revision, changed_files=changed, changed_lines=changed_lines,
        )

    seed_ids = sorted(
        node.id for node in nodes.values()
        if node.changed and node.kind in {"function", "binding"}
    )
    flow_seed_ids = sorted(
        node.id for node in nodes.values()
        if node.changed and node.kind in {"local", "argument"}
    )
    return EvidenceGraph(
        nodes=tuple(nodes[key] for key in sorted(nodes)),
        edges=tuple(edges[key] for key in sorted(edges)),
        metadata={
            "provider": "python_ast_graph_v4", "revision": revision,
            "capabilities": ["direct_call", "argument_parameter", "module_binding_read",
                             "local_alias_value_flow", "keyword_argument_binding",
                             "changed_value_flow_seed", "unchanged_caller_argument_boundary"],
            "source_files": len(parsed), "functions": len(functions),
            "bindings": len(bindings), "ambiguous_bindings_skipped": ambiguous_bindings,
            "ambiguous_local_aliases_skipped": ambiguous_local_aliases,
            "nonidentity_local_assignments_skipped": nonidentity_local_assignments,
            "unresolved_local_aliases_skipped": unresolved_local_aliases,
            "unresolved_calls": unresolved, "seed_ids": seed_ids,
            "flow_seed_ids": flow_seed_ids,
            "seed_mode": "diff_line_overlap" if changed_lines is not None else "changed_file_membership",
            "limits": ["no runtime dispatch resolution", "identity aliases and simple-name arguments only",
                       "module-level single-assignment bindings only", "no field-sensitive flow"],
        },
    )
