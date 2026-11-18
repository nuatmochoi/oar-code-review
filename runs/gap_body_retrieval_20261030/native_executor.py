"""Hash-checked HEAD source and actual product LSP operations.

This module does not choose targets, admit evidence, or enforce the experiment's
six-query budget. Every execute call, including a denied/empty call, costs one
logical query. Server RPCs and local filesystem reads are independent counters.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess
import threading
import time

import bootstrap  # establishes read-only access to the sealed product modules


OPERATIONS = frozenset(("go_to_definition", "find_references", "symbol_search",
                        "document_symbols", "incoming_calls", "outgoing_calls", "read_lines"))
RPC_METHODS = frozenset(("textDocument/definition", "textDocument/references",
                         "workspace/symbol", "textDocument/documentSymbol",
                         "textDocument/prepareCallHierarchy", "callHierarchy/incomingCalls",
                         "callHierarchy/outgoingCalls"))
STATUS_OPERATION = dict(go_to_definition="definition", find_references="references",
                        symbol_search="workspace_symbols", document_symbols="document_symbols",
                        incoming_calls="incoming_calls", outgoing_calls="outgoing_calls")
_PATCH_LOCK = threading.RLock()


class NativeExecutionError(ValueError):
    pass


def _manifest(value):
    rows = value.get("files", value) if isinstance(value, dict) else value
    if isinstance(rows, list):
        pairs = [(r["path"], r["sha256"]) for r in rows]
    elif isinstance(rows, dict):
        pairs = [(p, v.get("sha256") if isinstance(v, dict) else v) for p, v in rows.items()]
    else:
        raise NativeExecutionError("manifest must map relative paths to SHA256")
    out = {}
    for path, digest in pairs:
        _relative(path)
        if path in out or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise NativeExecutionError("duplicate path or invalid manifest SHA256")
        out[path] = digest.lower()
    return out


def _relative(path):
    if (not isinstance(path, str) or not path or "\\" in path or "\x00" in path
            or PurePosixPath(path).is_absolute() or any(x in ("", ".", "..") for x in path.split("/"))):
        raise NativeExecutionError("path must be a canonical relative workspace path")
    return path


def _positive_int(value):
    return type(value) is int and value >= 1


def _mask_source(text):
    from ace_review.protocol_hypothesis import mask_source
    return mask_source(text)


def _character_offset(line, column):
    if not _positive_int(column):
        raise NativeExecutionError("column must be a 1-based UTF-16 integer")
    offset = 2 * (column - 1)
    data = line.encode("utf-16-le")
    if offset > len(data):
        raise NativeExecutionError("column outside source line")
    try:
        return len(data[:offset].decode("utf-16-le"))
    except UnicodeDecodeError as exc:
        raise NativeExecutionError("column splits a UTF-16 surrogate pair") from exc


class NativeExecutor:
    def __init__(self, root, manifest, *, resolver_factory=None, timeout_seconds=90):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise NativeExecutionError("workspace root is not a directory")
        self.manifest = _manifest(manifest)
        self.resolver_factory = resolver_factory
        self.timeout_seconds = timeout_seconds
        self.resolver = None
        self._entered = self._closed = False
        self._patch = None
        self._clients, self._client_ids, self._processes = [], set(), {}
        self._stderr_threads = []
        self.audit = dict(requests=[], rpc_events=[], rpc_responses=[], file_reads=[],
                          server_open_reads=[], cleanup=None,
                          semantics={
                              "logical_queries": "one per execute, including empty/denied/failed; no retries",
                              "rpc_delta": "successful writes of query methods; call hierarchy includes prepare plus edge RPC",
                              "file_read_attempts": "executor hash-check/read attempts; separate from product didOpen reads",
                              "body_read_attempts": "read_lines filesystem attempts, a subset of file_read_attempts",
                              "server_open_reads": "product didOpen source-read attempts, not model evidence",
                          })

    def __enter__(self):
        if self._entered or self._closed:
            raise NativeExecutionError("executor context is single-use")
        _PATCH_LOCK.acquire()
        self._entered = True
        try:
            if self.resolver_factory is None:
                import oar_product_adapter as ad
                ad.load_product()
                from review_agent.lsp import client as module
                from review_agent.managed_tools import tool_environment
                original = module.LspClient.__init__
                executor = self

                def initialize(client, *args, **kwargs):
                    original(client, *args, **kwargs)
                    executor._track_client(client)

                module.LspClient.__init__ = initialize
                self._patch = (module.LspClient, original)
                self.resolver = module.LspResolver(
                    self.root, sorted(p for p in self.manifest if p.endswith(".py")),
                    timeout_seconds=self.timeout_seconds,
                    initialize_timeout_seconds=self.timeout_seconds,
                    environment=tool_environment())
            else:
                self.resolver = self.resolver_factory(self.root, sorted(self.manifest))
                original_client = getattr(self.resolver, "_client", None)
                if not callable(original_client):
                    raise NativeExecutionError("resolver cannot expose actual RPC instrumentation")

                def get_client(language):
                    client = original_client(language)
                    if client is not None:
                        self._track_client(client)
                    return client

                self.resolver._client = get_client
            return self
        except BaseException:
            self.close()
            raise

    def _track_client(self, client):
        if id(client) in self._client_ids:
            return
        self._client_ids.add(id(client))
        self._clients.append(client)
        writer = client._write
        request = client._request

        def write(message):
            event = None
            if message.get("method") in RPC_METHODS:
                event = dict(message=copy.deepcopy(message), write_succeeded=False)
                self.audit["rpc_events"].append(event)
            try:
                result = writer(message)
            except Exception as exc:
                if event is not None:
                    event["error"] = f"{type(exc).__name__}: {exc}"
                raise
            if event is not None:
                event["write_succeeded"] = True
            return result

        def rpc(method, params, *args, **kwargs):
            event = None
            if method in RPC_METHODS:
                event = dict(method=method, params=copy.deepcopy(params), status="pending")
                self.audit["rpc_responses"].append(event)
            try:
                result = request(method, params, *args, **kwargs)
            except Exception as exc:
                if event is not None:
                    event.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                raise
            if event is not None:
                event.update(status="completed", raw_result=copy.deepcopy(result))
            return result

        client._write, client._request = write, rpc
        opener = getattr(client, "_open", None)
        if callable(opener):
            def open_source(path, language):
                if path in getattr(client, "_opened", set()):
                    return opener(path, language)
                event = dict(path=path, read_attempted=True, completed=False)
                self.audit["server_open_reads"].append(event)
                try:
                    result = opener(path, language)
                    event["completed"] = True
                    return result
                except Exception as exc:
                    event["error"] = f"{type(exc).__name__}: {exc}"
                    raise
            client._open = open_source
        starter = getattr(client, "start", None)
        if callable(starter):
            def start(*args, **kwargs):
                try:
                    return starter(*args, **kwargs)
                finally:
                    self._capture_process(client)
            client.start = start

    def _capture_process(self, client):
        process = getattr(client, "process", None)
        if process is not None:
            self._processes[process.pid] = process
        thread = getattr(client, "_stderr_thread", None)
        if thread is not None and thread not in self._stderr_threads:
            self._stderr_threads.append(thread)

    def _read(self, path, purpose):
        path = _relative(path)
        if path not in self.manifest:
            raise NativeExecutionError("path outside frozen HEAD manifest")
        target = (self.root / path).resolve(strict=True)
        if not target.is_relative_to(self.root) or not target.is_file():
            raise NativeExecutionError("resolved path leaves workspace or is not a file")
        event = dict(path=path, purpose=purpose, read_attempted=True, read_succeeded=False,
                     expected_sha256=self.manifest[path])
        self.audit["file_reads"].append(event)
        try:
            raw = target.read_bytes()
        except Exception as exc:
            event["error"] = type(exc).__name__
            raise
        digest = hashlib.sha256(raw).hexdigest()
        event.update(read_succeeded=True, actual_sha256=digest, hash_matches=digest == self.manifest[path])
        if digest != self.manifest[path]:
            raise NativeExecutionError("HEAD source hash differs from frozen manifest")
        text = raw.decode("utf-8", errors="replace")
        return text.splitlines(), digest, text

    def _symbol(self, request):
        anchor = dict(request.get("anchor") or {})
        anchor.update({k: request[k] for k in ("path", "line", "column", "name") if k in request})
        path, line, column, name = (anchor.get(k) for k in ("path", "line", "column", "name"))
        rows, _, _ = self._read(path, "anchor_hash_verification")
        if not _positive_int(line) or line > len(rows) or not isinstance(name, str) or not name:
            raise NativeExecutionError("missing or invalid exact source anchor")
        offset = _character_offset(rows[line - 1], column)
        leaf = name.rsplit(".", 1)[-1]
        tail = rows[line - 1][offset:]
        if (not leaf.isidentifier() or not tail.startswith(leaf)
                or (len(tail) > len(leaf) and (tail[len(leaf)].isalnum() or tail[len(leaf)] == "_"))
                or (offset and (rows[line - 1][offset - 1].isalnum() or rows[line - 1][offset - 1] == "_"))):
            raise NativeExecutionError("anchor name does not match exact source token")
        from types import SimpleNamespace
        # The product resolver uses the StructuralSymbol attributes; synthetic
        # factories avoid importing the product, while production uses its type.
        values = dict(path=path, name=leaf, kind="exact_use_site", line_start=line,
                      line_end=line, column_start=column, column_end=column + len(leaf.encode("utf-16-le")) // 2,
                      language="python", source="frozen-source-exact-anchor")
        if self.resolver_factory is not None:
            return SimpleNamespace(**values)
        from review_agent.code_intelligence.tree_sitter_index import StructuralSymbol
        return StructuralSymbol(**values)

    def _locations(self, values):
        out, rejected, verified = [], [], {}
        for value in values:
            raw = dataclasses.asdict(value) if dataclasses.is_dataclass(value) else dict(value)
            try:
                path = raw.get("path")
                if not isinstance(path, str) or not path.endswith(".py"):
                    raise NativeExecutionError("native target is not manifest Python source")
                if not str(raw.get("source", "")).startswith("lsp:"):
                    raise NativeExecutionError("native resolver returned a non-native location label")
                if path not in verified:
                    verified[path] = self._read(path, "returned_location_hash_verification")
                lines, sha, _ = verified[path]
                start = raw.get("line_start", raw.get("start"))
                end = raw.get("line_end", raw.get("end", start))
                column = raw.get("column_start", raw.get("column", 1))
                if not _positive_int(start) or not _positive_int(end) or start > end or start > len(lines):
                    raise NativeExecutionError("native location outside source lines")
                _character_offset(lines[start - 1], column)
                # LSP end is exclusive; a final newline may put it on EOF+1.
                if end > len(lines) and not (end == len(lines) + 1 and raw.get("column_end") == 1):
                    raise NativeExecutionError("native end outside source lines")
                out.append(dict(path=path, start=start, end=min(end, len(lines)), column=column,
                                name=raw.get("name"), kind=raw.get("kind"), source=raw.get("source", ""),
                                line_start=start, line_end=min(end, len(lines)), column_start=column,
                                source_sha256=sha, native_range=raw, position_only=True))
            except (ValueError, OSError, TypeError) as exc:
                rejected.append(dict(location=raw, reason=f"{type(exc).__name__}: {exc}"))
        return out, rejected

    def execute(self, request):
        before = {k: len(self.audit[k]) for k in ("rpc_events", "rpc_responses", "file_reads", "server_open_reads")}
        result = dict(status="failed", locations=[], lines=[], error=None, logical_queries=1,
                      native_invocations=0, rejected_locations=[], request=copy.deepcopy(request))
        started = time.monotonic()
        try:
            if not self._entered or self._closed:
                raise NativeExecutionError("execute requires an active context")
            if not isinstance(request, dict) or request.get("operation") not in OPERATIONS:
                raise NativeExecutionError("unsupported request operation")
            operation = request["operation"]
            if operation == "read_lines":
                start, end = request.get("start"), request.get("end")
                if not _positive_int(start) or not _positive_int(end) or not start <= end or end - start + 1 > 200:
                    raise NativeExecutionError("read_lines requires a 1..200 line interval")
                if not isinstance(request.get("path"), str) or not request["path"].endswith(".py"):
                    raise NativeExecutionError("only Python source has the frozen masking convention")
                rows, digest, text = self._read(request.get("path"), "body_read")
                try:
                    masked = _mask_source(text)
                    masked_rows = masked.splitlines()
                except Exception as exc:
                    raise NativeExecutionError(f"mask_source failed ({type(exc).__name__}); raw source not supplied") from exc
                if len(masked_rows) != len(rows):
                    raise NativeExecutionError("mask_source changed source line count; raw source not supplied")
                rows = masked_rows
                if start > len(rows):
                    raise NativeExecutionError("read start is past EOF")
                result.update(lines=[[i, rows[i - 1]] for i in range(start, min(end, len(rows)) + 1)],
                              path=request["path"], source_sha256=digest, snapshot="head",
                              normalization="mask_source", masked_source_sha256=hashlib.sha256(masked.encode()).hexdigest(),
                              status="completed" if end <= len(rows) else "partial", truncated=end > len(rows))
            else:
                if operation == "symbol_search":
                    query = request.get("query", request.get("name"))
                    if not isinstance(query, str) or not query.strip() or len(query) > 200 or "\n" in query:
                        raise NativeExecutionError("invalid native symbol query")
                    method, argument = "workspace_symbols", query
                elif operation == "document_symbols":
                    self._read(request.get("path"), "document_hash_verification")
                    method, argument = "document_symbols", request["path"]
                else:
                    method, argument = operation, self._symbol(request)
                result["native_invocations"] = 1
                values = getattr(self.resolver, method)(argument)
                result["native_returned_locations"] = len(values)
                result["locations"], result["rejected_locations"] = self._locations(values)
                server = self.resolver.status()
                result["server_status"] = copy.deepcopy(server)
                states = [s.get("operations", {}).get(STATUS_OPERATION[operation], {}).get("state", s.get("state"))
                          for s in server.values()]
                failures = [s for s in states if s in ("timeout", "failed", "unavailable", "unsupported")]
                result["status"] = ("completed" if result["locations"] else failures[0] if failures
                                    else "filtered" if values else "empty")
                if failures:
                    result["error"] = "; ".join(str(s.get("error") or s.get("operations", {}).get(STATUS_OPERATION[operation], {}).get("detail") or failures[0]) for s in server.values())
        except Exception as exc:
            result.update(status="denied" if isinstance(exc, NativeExecutionError) else "failed",
                          error=f"{type(exc).__name__}: {exc}")
        finally:
            for client in self._clients:
                self._capture_process(client)
            for key, offset in before.items():
                result[key] = copy.deepcopy(self.audit[key][offset:])
            result.update(rpc_delta=sum(e["write_succeeded"] for e in result["rpc_events"]),
                          rpc_write_attempts=len(result["rpc_events"]),
                          file_read_attempts=len(result["file_reads"]),
                          hash_verification_reads=len(result["file_reads"]),
                          body_read_attempts=sum(e["purpose"] == "body_read" for e in result["file_reads"]),
                          elapsed_seconds=round(time.monotonic() - started, 6))
            self.audit["requests"].append(copy.deepcopy(result))
        return result

    def close(self):
        if self._closed:
            return self.audit["cleanup"]
        errors, processes = [], []
        try:
            for client in self._clients:
                self._capture_process(client)
            if self.resolver is not None:
                try:
                    self.resolver.close()
                except Exception as exc:
                    errors.append(f"resolver.close: {type(exc).__name__}: {exc}")
            for pid, process in self._processes.items():
                forced = False
                try:
                    if process.poll() is None:
                        forced = True
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                    else:
                        process.wait(timeout=0)
                    processes.append(dict(pid=pid, exit_code=process.poll(), reaped=process.poll() is not None,
                                          forced=forced))
                except Exception as exc:
                    processes.append(dict(pid=pid, reaped=False, error=f"{type(exc).__name__}: {exc}"))
            for thread in self._stderr_threads:
                if thread.is_alive():
                    thread.join(timeout=2)
                    if thread.is_alive():
                        errors.append("stderr reader did not drain")
            self.audit["cleanup"] = dict(passed=not errors and all(p["reaped"] for p in processes),
                                         processes=processes, errors=errors, tracked_clients=len(self._clients),
                                         stderr_drained=all(not t.is_alive() for t in self._stderr_threads))
            return self.audit["cleanup"]
        finally:
            self._closed = True
            if self._patch:
                cls, original = self._patch
                cls.__init__ = original
                self._patch = None
            if self._entered:
                self._entered = False
                _PATCH_LOCK.release()

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False
