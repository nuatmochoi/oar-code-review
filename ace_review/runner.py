from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .bm25 import retrieve_bm25
from .adaptive import compose_adaptive_labels, nested_repository_adaptive
from .ablation import factorial_ablation
from .batch_judge import judge_predictions_batch
from .buildtest import collect_buildtest
from .conditions import CONDITIONS, make_request, select_contexts
from .codeql import collect_codeql
from .converters import convert_aacr, convert_go_artifact
from .external import ExternalContextAdapter
from .evaluation import aggregate_labels
from .diff import split_task_hunks
from .gopls import collect_gopls
from .io import load_tasks, read_jsonl, write_json, write_tasks
from .judge import judge_predictions
from .repository import RepositoryCache, hydrate_diff, revision_worktree
from .reviewer import run_codex_requests
from .schema import ContextBlock
from .selector import UtilitySelector
from .statistics import paired_bootstrap
from .stratified import stratified_metrics
from .testcontext import retrieve_test_context
from .history import retrieve_history_context
from .oracle import build_oracle_candidates
from .cochange import retrieve_cochange_context
from .placebo import make_token_matched_placebos
from .targetedtest import collect_targeted_tests


def command_convert_go(args: argparse.Namespace) -> None:
    count = convert_go_artifact(args.source, args.output)
    print(json.dumps({"tasks": count, "output": args.output}))


def command_convert_aacr(args: argparse.Namespace) -> None:
    count = convert_aacr(args.source, args.output, defects_only=args.defects_only)
    print(json.dumps({"tasks": count, "output": args.output}))


def command_filter(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.manifest)
    if args.language:
        tasks = [task for task in tasks if task.language.lower() == args.language.lower()]
    if args.repo:
        tasks = [task for task in tasks if task.repo == args.repo]
    if args.task_id:
        tasks = [task for task in tasks if task.id == args.task_id]
    if args.all_gold_extension:
        suffix = args.all_gold_extension.lower()
        tasks = [
            task for task in tasks
            if task.gold_issues and all(Path(issue.file).suffix.lower() == suffix for issue in task.gold_issues)
        ]
    if args.smallest_first:
        tasks.sort(key=lambda task: (task.metadata.get("change_line_count") or 10**12, task.id))
    if args.limit is not None:
        tasks = tasks[: args.limit]
    count = write_tasks(args.output, tasks)
    print(json.dumps({"tasks": count, "output": args.output}))


def command_hydrate(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.manifest)
    if args.limit is not None:
        tasks = tasks[: args.limit]
    cache = RepositoryCache(args.repo_cache)
    hydrated = []
    failures = []
    for task in tasks:
        path, status = cache.ensure(task)
        if not path:
            failures.append({"task_id": task.id, "status": status})
            continue
        try:
            hydrated.append(hydrate_diff(task, path))
        except Exception as error:
            failures.append({"task_id": task.id, "error": str(error)})
    count = write_tasks(args.output, hydrated)
    write_json(str(args.output) + ".failures.json", failures)
    print(json.dumps({"tasks": count, "failures": len(failures), "output": args.output}))


def command_split_hunks(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.manifest)
    split = [unit for task in tasks for unit in split_task_hunks(task)]
    count = write_tasks(args.output, split)
    print(json.dumps({"parent_tasks": len(tasks), "hunk_tasks": count, "output": args.output}))


def command_prepare(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.manifest)
    if args.limit is not None:
        tasks = tasks[: args.limit]
    cache = RepositoryCache(args.repo_cache) if args.repo_cache else None
    dataflow_adapter = ExternalContextAdapter("dataflow", args.dataflow_command) if args.dataflow_command else None
    conditions = args.conditions or list(CONDITIONS)
    def prepare_task(task):
        local = Counter()
        extra: list[ContextBlock] = []
        repo_path = None
        if cache:
            repo_path, repo_status = cache.ensure(task)
            local[f"repo:{repo_status.get('available', False)}"] += 1
            if repo_path:
                try:
                    task = hydrate_diff(task, repo_path)
                    with revision_worktree(repo_path, task.head_commit) as snapshot:
                        extra.extend(retrieve_bm25(task, snapshot, top_k=args.bm25_top_k))
                        local["bm25:available"] += 1
                        if args.test_context:
                            blocks = retrieve_test_context(task, snapshot, top_k=args.test_context_top_k)
                            extra.extend(blocks)
                            local[f"testsource:{bool(blocks)}"] += 1
                        if args.history_context:
                            blocks = retrieve_history_context(task, repo_path, top_k=args.history_top_k)
                            extra.extend(blocks)
                            local[f"history:{bool(blocks)}"] += 1
                        if args.cochange_context:
                            blocks = retrieve_cochange_context(
                                task, repo_path, snapshot, top_k=args.cochange_top_k,
                                max_commits=args.cochange_max_commits,
                            )
                            extra.extend(blocks)
                            local[f"cochange:{bool(blocks)}"] += 1
                        has_cached_lsp = any(block.kind == "lsp" for block in task.cached_contexts)
                        if args.gopls and (args.refresh_lsp or not has_cached_lsp):
                            blocks, status = collect_gopls(task, snapshot, args.gopls)
                            extra.extend(blocks)
                            local[f"lsp:{status.get('available', False)}"] += 1
                        if args.codeql:
                            blocks, status = collect_codeql(
                                task, snapshot, args.codeql, args.codeql_search_path,
                                args.codeql_cache, timeout_seconds=args.codeql_timeout,
                                max_source_lines=args.codeql_max_source_lines,
                            )
                            extra.extend(blocks)
                            local[f"dataflow:{status.get('available', False)}"] += 1
                        elif dataflow_adapter:
                            blocks, status = dataflow_adapter.collect(task, snapshot)
                            extra.extend(blocks)
                            local[f"dataflow:{status.get('available', False)}"] += 1
                except Exception as error:  # preserve task and expose failure in summary
                    local[f"evidence:error:{type(error).__name__}"] += 1
                if args.run_buildtest:
                    blocks, status = collect_buildtest(task, repo_path, timeout=args.test_timeout)
                    extra.extend(blocks)
                    local[f"buildtest:{status.get('available', False)}"] += 1
                if args.run_targetedtest:
                    blocks, status = collect_targeted_tests(
                        task, repo_path, timeout=args.targetedtest_timeout,
                        max_packages=args.targetedtest_max_packages,
                    )
                    extra.extend(blocks)
                    local[f"targetedtest:{status.get('available', False)}"] += 1
        requests = []
        for condition in conditions:
            if condition not in CONDITIONS:
                raise SystemExit(f"Unknown condition: {condition}")
            contexts = select_contexts(task, condition, extra=extra, token_budget=args.token_budget)
            request = make_request(task, condition, contexts)
            requested = set(CONDITIONS[condition].kinds)
            present = {block.kind for block in contexts}
            available = {block.kind for block in (*task.cached_contexts, *tuple(extra))}
            request["context_status"] = {
                "requested": sorted(requested), "present": sorted(present),
                "unavailable": sorted(requested - available),
                "budget_omitted": sorted((requested & available) - present),
                "token_budget": args.token_budget,
                "context_tokens": sum(block.to_dict()["token_cost"] for block in contexts),
            }
            requests.append(request)
            local[f"condition:{condition}"] += 1
        return requests, local

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = Counter()
    with output.open("w", encoding="utf-8") as handle:
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as executor:
            for requests, local in executor.map(prepare_task, tasks):
                summary.update(local)
                for request in requests:
                    handle.write(json.dumps(request, ensure_ascii=False) + "\n")
                handle.flush()
    summary_path = output.with_suffix(output.suffix + ".summary.json")
    write_json(summary_path, dict(summary))
    print(json.dumps({"requests": sum(value for key, value in summary.items() if key.startswith("condition:")), "output": str(output), "summary": str(summary_path)}))


def command_recompose(args: argparse.Namespace) -> None:
    """Build new condition requests from evidence already preserved in request rows."""
    tasks = load_tasks(args.manifest)
    source_rows = list(read_jsonl(args.requests))
    evidence: dict[str, dict[tuple[str, str], ContextBlock]] = {}
    for row in source_rows:
        task_evidence = evidence.setdefault(row["task_id"], {})
        for item in row.get("contexts", []):
            block = ContextBlock.from_dict(item)
            task_evidence[(block.kind, block.id)] = block

    conditions = args.conditions or list(CONDITIONS)
    rows = []
    for task in tasks:
        extra = list(evidence.get(task.id, {}).values())
        available = {block.kind for block in (*task.cached_contexts, *extra)}
        for condition in conditions:
            if condition not in CONDITIONS:
                raise SystemExit(f"Unknown condition: {condition}")
            contexts = select_contexts(task, condition, extra=extra, token_budget=args.token_budget)
            request = make_request(task, condition, contexts)
            requested = set(CONDITIONS[condition].kinds)
            present = {block.kind for block in contexts}
            request["context_status"] = {
                "requested": sorted(requested), "present": sorted(present),
                "unavailable": sorted(requested - available),
                "budget_omitted": sorted((requested & available) - present),
                "token_budget": args.token_budget,
                "context_tokens": sum(block.to_dict()["token_cost"] for block in contexts),
                "recomposed_from": args.requests,
            }
            rows.append(request)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"requests": len(rows), "tasks": len(tasks), "output": str(output)}))


def command_preflight(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.manifest)
    if args.limit is not None:
        tasks = tasks[: args.limit]
    cache = RepositoryCache(args.repo_cache)
    rows = []
    for task in tasks:
        path, status = cache.ensure(task)
        row = {"task_id": task.id, "repo": task.repo, "repository": status}
        if path:
            try:
                hydrated = hydrate_diff(task, path)
                row["diff"] = {"available": bool(hydrated.diff), "bytes": len(hydrated.diff.encode("utf-8")), "files": len(hydrated.changed_files)}
            except Exception as error:
                row["diff"] = {"available": False, "error": str(error)}
            if args.run_buildtest:
                _, row["buildtest"] = collect_buildtest(task, path, timeout=args.test_timeout)
        rows.append(row)
    write_json(args.output, rows)
    print(json.dumps({"tasks": len(rows), "output": args.output}))


def command_train_selector(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.manifest)
    outcomes = list(read_jsonl(args.outcomes))
    selector = UtilitySelector.fit(tasks, outcomes, neighbors=args.neighbors)
    selector.save(args.output)
    print(json.dumps({"training_tasks": len(selector.training), "conditions": selector.conditions, "output": args.output}))


def command_select(args: argparse.Namespace) -> None:
    selector = UtilitySelector.load(args.model)
    tasks = load_tasks(args.manifest)
    rows = [{"task_id": task.id, **selector.predict(task)} for task in tasks]
    write_json(args.output, rows)
    print(json.dumps({"tasks": len(rows), "output": args.output}))


def command_run_codex(args: argparse.Namespace) -> None:
    result = run_codex_requests(
        args.requests, args.output, args.prompt, args.schema,
        limit=args.limit, condition=args.condition, model=args.model,
        timeout_seconds=args.timeout, jobs=args.jobs, repeat_id=args.repeat_id,
        task_ids=set(args.task_id or ()),
    )
    print(json.dumps(result))


def command_merge_predictions(args: argparse.Namespace) -> None:
    """Deduplicate model outputs, preferring a successful retry for each run key."""
    records: dict[tuple[str, str, int], dict] = {}
    order: list[tuple[str, str, int]] = []
    for path in args.predictions:
        for row in read_jsonl(path):
            key = (row["task_id"], row["condition"], int(row.get("repeat", 0)))
            if key not in records:
                records[key] = row
                order.append(key)
            elif records[key].get("status") != "ok" and row.get("status") == "ok":
                records[key] = row
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for key in order:
            handle.write(json.dumps(records[key], ensure_ascii=False) + "\n")
    failures = sum(
        "status" in records[key] and records[key].get("status") != "ok"
        for key in order
    )
    print(json.dumps({"rows": len(order), "failures": failures, "output": str(target)}))


def command_analyze(args: argparse.Namespace) -> None:
    result = aggregate_labels(load_tasks(args.manifest), list(read_jsonl(args.labels)), baseline=args.baseline)
    outcomes = result.pop("selector_outcomes")
    write_json(args.output, result)
    outcomes_path = str(args.output) + ".selector_outcomes.jsonl"
    target = Path(outcomes_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in outcomes:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"output": args.output, "selector_outcomes": outcomes_path}))


def command_judge(args: argparse.Namespace) -> None:
    result = judge_predictions(
        args.manifest, args.predictions, args.output, model=args.model,
        line_tolerance=args.line_tolerance, jobs=args.jobs, timeout_seconds=args.timeout,
        schema_path=args.schema, semantic_anywhere=args.semantic_anywhere,
    )
    print(json.dumps(result))


def command_compose_adaptive(args: argparse.Namespace) -> None:
    print(json.dumps(compose_adaptive_labels(args.manifest, args.labels, args.output)))


def command_nested_adaptive(args: argparse.Namespace) -> None:
    result = nested_repository_adaptive(
        args.manifest, args.outcomes, args.labels, args.output,
        neighbor_candidates=tuple(args.neighbor_candidates),
    )
    print(json.dumps(result))


def command_bootstrap(args: argparse.Namespace) -> None:
    result = paired_bootstrap(
        load_tasks(args.manifest), list(read_jsonl(args.labels)), baseline=args.baseline,
        requests=list(read_jsonl(args.requests)) if args.requests else None,
        samples=args.samples, seed=args.seed, match_mode=args.match_mode,
        resample_unit=args.resample_unit,
    )
    write_json(args.output, result)
    print(json.dumps({"output": args.output, "tasks": result["tasks"], "samples": args.samples}))


def command_judge_batch(args: argparse.Namespace) -> None:
    result = judge_predictions_batch(
        args.manifest, args.predictions, args.output, model=args.model,
        line_tolerance=args.line_tolerance, jobs=args.jobs, timeout_seconds=args.timeout,
        schema_path=args.schema,
    )
    print(json.dumps(result))


def command_stratify(args: argparse.Namespace) -> None:
    result = stratified_metrics(
        load_tasks(args.manifest), list(read_jsonl(args.labels)),
        requests=list(read_jsonl(args.requests)) if args.requests else None,
    )
    write_json(args.output, result)
    print(json.dumps({"output": args.output}))


def command_ablation(args: argparse.Namespace) -> None:
    result = factorial_ablation(
        load_tasks(args.manifest), list(read_jsonl(args.labels)),
        requests=list(read_jsonl(args.requests)) if args.requests else None,
        samples=args.samples, seed=args.seed, match_mode=args.match_mode,
        resample_unit=args.resample_unit,
    )
    write_json(args.output, result)
    print(json.dumps({"output": args.output, "samples": args.samples}))


def command_oracle_candidates(args: argparse.Namespace) -> None:
    rows = build_oracle_candidates(
        load_tasks(args.manifest), args.requests, context_level=args.context_level,
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"gold_issues": len(rows), "output": str(target)}))


def command_make_placebo(args: argparse.Namespace) -> None:
    rows = make_token_matched_placebos(
        list(read_jsonl(args.requests)), tuple(args.source_conditions), seed=args.seed,
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"requests": len(rows), "output": str(target)}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ACE-Review experiment runner")
    sub = parser.add_subparsers(dest="command", required=True)

    go = sub.add_parser("convert-go")
    go.add_argument("--source", required=True); go.add_argument("--output", required=True)
    go.set_defaults(func=command_convert_go)

    aacr = sub.add_parser("convert-aacr")
    aacr.add_argument("--source", required=True); aacr.add_argument("--output", required=True)
    aacr.add_argument("--defects-only", action="store_true")
    aacr.set_defaults(func=command_convert_aacr)

    filtered = sub.add_parser("filter")
    filtered.add_argument("--manifest", required=True); filtered.add_argument("--output", required=True)
    filtered.add_argument("--language"); filtered.add_argument("--repo"); filtered.add_argument("--task-id")
    filtered.add_argument("--all-gold-extension")
    filtered.add_argument("--smallest-first", action="store_true"); filtered.add_argument("--limit", type=int)
    filtered.set_defaults(func=command_filter)

    hydrate = sub.add_parser("hydrate")
    hydrate.add_argument("--manifest", required=True); hydrate.add_argument("--repo-cache", required=True)
    hydrate.add_argument("--output", required=True); hydrate.add_argument("--limit", type=int)
    hydrate.set_defaults(func=command_hydrate)

    split = sub.add_parser("split-hunks")
    split.add_argument("--manifest", required=True); split.add_argument("--output", required=True)
    split.set_defaults(func=command_split_hunks)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--manifest", required=True); prepare.add_argument("--output", required=True)
    prepare.add_argument("--conditions", nargs="*"); prepare.add_argument("--limit", type=int)
    prepare.add_argument("--repo-cache"); prepare.add_argument("--token-budget", type=int, default=4096)
    prepare.add_argument("--bm25-top-k", type=int, default=5)
    prepare.add_argument("--test-context", action="store_true")
    prepare.add_argument("--test-context-top-k", type=int, default=4)
    prepare.add_argument("--history-context", action="store_true")
    prepare.add_argument("--history-top-k", type=int, default=4)
    prepare.add_argument("--cochange-context", action="store_true")
    prepare.add_argument("--cochange-top-k", type=int, default=4)
    prepare.add_argument("--cochange-max-commits", type=int, default=30)
    prepare.add_argument("--gopls"); prepare.add_argument("--refresh-lsp", action="store_true")
    prepare.add_argument("--dataflow-command"); prepare.add_argument("--run-buildtest", action="store_true")
    prepare.add_argument("--codeql")
    prepare.add_argument("--codeql-search-path", default="tools/codeql-src-v2.26.2")
    prepare.add_argument("--codeql-cache", default="runs/codeql-cache")
    prepare.add_argument("--codeql-timeout", type=int, default=1800)
    prepare.add_argument("--codeql-max-source-lines", type=int, default=64)
    prepare.add_argument("--test-timeout", type=int, default=600)
    prepare.add_argument("--run-targetedtest", action="store_true")
    prepare.add_argument("--targetedtest-timeout", type=int, default=180)
    prepare.add_argument("--targetedtest-max-packages", type=int, default=3)
    prepare.add_argument("--jobs", type=int, default=1)
    prepare.set_defaults(func=command_prepare)

    recompose = sub.add_parser("recompose")
    recompose.add_argument("--manifest", required=True)
    recompose.add_argument("--requests", required=True)
    recompose.add_argument("--output", required=True)
    recompose.add_argument("--conditions", nargs="*")
    recompose.add_argument("--token-budget", type=int, default=4096)
    recompose.set_defaults(func=command_recompose)

    preflight = sub.add_parser("preflight")
    preflight.add_argument("--manifest", required=True); preflight.add_argument("--repo-cache", required=True)
    preflight.add_argument("--output", required=True); preflight.add_argument("--limit", type=int)
    preflight.add_argument("--run-buildtest", action="store_true"); preflight.add_argument("--test-timeout", type=int, default=600)
    preflight.set_defaults(func=command_preflight)

    train = sub.add_parser("train-selector")
    train.add_argument("--manifest", required=True); train.add_argument("--outcomes", required=True)
    train.add_argument("--output", required=True); train.add_argument("--neighbors", type=int, default=15)
    train.set_defaults(func=command_train_selector)

    select = sub.add_parser("select")
    select.add_argument("--model", required=True); select.add_argument("--manifest", required=True)
    select.add_argument("--output", required=True); select.set_defaults(func=command_select)

    review = sub.add_parser("run-codex")
    review.add_argument("--requests", required=True); review.add_argument("--output", required=True)
    review.add_argument("--prompt", default="ace_review/prompts/reviewer.txt")
    review.add_argument("--schema", default="ace_review/schemas/review_output.schema.json")
    review.add_argument("--condition"); review.add_argument("--limit", type=int)
    review.add_argument("--model"); review.add_argument("--timeout", type=int, default=600)
    review.add_argument("--jobs", type=int, default=1)
    review.add_argument("--repeat-id", type=int, default=0)
    review.add_argument("--task-id", action="append")
    review.set_defaults(func=command_run_codex)

    merge_predictions = sub.add_parser("merge-predictions")
    merge_predictions.add_argument("--predictions", required=True, nargs="+")
    merge_predictions.add_argument("--output", required=True)
    merge_predictions.set_defaults(func=command_merge_predictions)

    judge = sub.add_parser("judge")
    judge.add_argument("--manifest", required=True); judge.add_argument("--predictions", required=True, nargs="+")
    judge.add_argument("--output", required=True)
    judge.add_argument("--model", default="gpt-5.6-sol"); judge.add_argument("--line-tolerance", type=int, default=1)
    judge.add_argument("--jobs", type=int, default=4); judge.add_argument("--timeout", type=int, default=600)
    judge.add_argument("--schema", default="ace_review/schemas/semantic_match.schema.json")
    judge.add_argument("--semantic-anywhere", action="store_true")
    judge.set_defaults(func=command_judge)

    adaptive = sub.add_parser("compose-adaptive")
    adaptive.add_argument("--manifest", required=True); adaptive.add_argument("--labels", required=True)
    adaptive.add_argument("--output", required=True); adaptive.set_defaults(func=command_compose_adaptive)

    nested = sub.add_parser("nested-adaptive")
    nested.add_argument("--manifest", required=True); nested.add_argument("--outcomes", required=True)
    nested.add_argument("--labels", required=True); nested.add_argument("--output", required=True)
    nested.add_argument("--neighbor-candidates", type=int, nargs="+", default=[1, 3, 5, 7])
    nested.set_defaults(func=command_nested_adaptive)

    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--manifest", required=True); bootstrap.add_argument("--labels", required=True)
    bootstrap.add_argument("--requests"); bootstrap.add_argument("--baseline", default="diff")
    bootstrap.add_argument("--samples", type=int, default=2000); bootstrap.add_argument("--seed", type=int, default=20260902)
    bootstrap.add_argument("--match-mode", choices=["strict", "semantic_anywhere"], default="strict")
    bootstrap.add_argument("--resample-unit", choices=["task", "repository"], default="task")
    bootstrap.add_argument("--output", required=True); bootstrap.set_defaults(func=command_bootstrap)

    batch_judge = sub.add_parser("judge-batch")
    batch_judge.add_argument("--manifest", required=True); batch_judge.add_argument("--predictions", required=True, nargs="+")
    batch_judge.add_argument("--output", required=True); batch_judge.add_argument("--model", default="gpt-5.4")
    batch_judge.add_argument("--line-tolerance", type=int, default=1); batch_judge.add_argument("--jobs", type=int, default=8)
    batch_judge.add_argument("--timeout", type=int, default=600)
    batch_judge.add_argument("--schema", default="ace_review/schemas/batch_semantic_match.schema.json")
    batch_judge.set_defaults(func=command_judge_batch)

    stratify = sub.add_parser("stratify")
    stratify.add_argument("--manifest", required=True); stratify.add_argument("--labels", required=True)
    stratify.add_argument("--requests"); stratify.add_argument("--output", required=True)
    stratify.set_defaults(func=command_stratify)

    ablation = sub.add_parser("ablation")
    ablation.add_argument("--manifest", required=True); ablation.add_argument("--labels", required=True)
    ablation.add_argument("--requests"); ablation.add_argument("--samples", type=int, default=5000)
    ablation.add_argument("--seed", type=int, default=20260902)
    ablation.add_argument("--match-mode", choices=("strict", "semantic_anywhere"), default="strict")
    ablation.add_argument("--resample-unit", choices=("task", "repository"), default="task")
    ablation.add_argument("--output", required=True); ablation.set_defaults(func=command_ablation)

    oracle = sub.add_parser("oracle-candidates")
    oracle.add_argument("--manifest", required=True)
    oracle.add_argument("--requests", required=True, nargs="+")
    oracle.add_argument("--context-level")
    oracle.add_argument("--output", required=True)
    oracle.set_defaults(func=command_oracle_candidates)

    placebo = sub.add_parser("make-placebo")
    placebo.add_argument("--requests", required=True)
    placebo.add_argument("--source-conditions", nargs="+", default=["testsource", "history"])
    placebo.add_argument("--seed", type=int, default=20260902)
    placebo.add_argument("--output", required=True)
    placebo.set_defaults(func=command_make_placebo)

    analyze = sub.add_parser("analyze")
    analyze.add_argument("--manifest", required=True); analyze.add_argument("--labels", required=True)
    analyze.add_argument("--baseline", default="diff"); analyze.add_argument("--output", required=True)
    analyze.set_defaults(func=command_analyze)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
