"""Resumable paired-SCIP preparation. No gold information enters retrieval.

Run: python3 -m ace_review.scip_batch --help
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .conditions import make_request
from .delta_scip import SemanticIndex, build_candidates, select_evidence
from .io import load_tasks, read_jsonl, write_json
from .repository import RepositoryCache, revision_worktree


def attach_sources(raw: dict, snapshot: Path) -> tuple[list[str], list[str]]:
    """Exclude generated test mains outside the checkout; never read them."""
    kept,missing,excluded=[],[],[]
    for document in raw.get("documents",[]):
        relative=Path(document["relativePath"])
        source=snapshot/relative
        if relative.is_absolute() or ".." in relative.parts or not source.resolve().is_relative_to(snapshot.resolve()):
            excluded.append(str(relative))
            continue
        if source.is_file():
            document["text"]=source.read_text(encoding="utf-8",errors="replace")
        else:
            missing.append(str(relative))
        kept.append(document)
    raw["documents"]=kept
    return missing,excluded


def cache_key(commit: str, indexer: str, exporter: str, environment: dict, module_root: str = ".") -> str:
    value={"commit":commit,"indexer_sha256":hashlib.sha256(Path(indexer).read_bytes()).hexdigest(),
           "exporter_sha256":hashlib.sha256(Path(exporter).read_bytes()).hexdigest(),
           "environment":environment,"index_args":["index","--module-version=ace-review","./..."],
           "schema":1}
    if module_root != ".":
        value["module_root"]=module_root
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:24]


def index_revision(repo: Path, commit: str, cache_root: Path, indexer: str,
                   exporter: str, timeout: int = 600, module_root: str = ".") -> tuple[dict | None, dict]:
    relative=Path(module_root)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe module root")
    module_root=relative.as_posix()
    indexer,exporter=str(Path(indexer).resolve()),str(Path(exporter).resolve())
    env=os.environ | {"GOTOOLCHAIN":"local","GOWORK":"off","GOMAXPROCS":"4"}
    started=time.monotonic()
    status={"available":False,"commit":commit,"repo":str(repo)}
    output=None
    try:
        with revision_worktree(repo,commit) as snapshot:
            checkout=snapshot
            snapshot=checkout/relative
            if not snapshot.resolve().is_relative_to(checkout.resolve()):
                raise ValueError("Module root escapes checkout")
            environment=json.loads(subprocess.check_output(
                ["go","env","-json","GOVERSION","GOOS","GOARCH","CGO_ENABLED","GOFLAGS","GOTOOLCHAIN"],
                cwd=snapshot,env=env,text=True,timeout=30))
            output=cache_root/repo.name/cache_key(commit,indexer,exporter,environment,module_root)
            ready=output/"status.json";payload=output/"index.json.gz"
            if ready.exists() and payload.exists():
                prior=json.loads(ready.read_text())
                if prior.get("available"):
                    with gzip.open(payload,"rt",encoding="utf-8") as stream:
                        return json.load(stream),prior | {"cache_hit":True,"module_root":module_root,
                                                         "lookup_seconds":time.monotonic()-started}
            output.mkdir(parents=True,exist_ok=True)
            module_file=snapshot/"go.mod"
            if not module_file.exists():
                raise ValueError("No root go.mod; nested-module indexing is not implemented in this pilot")
            import re
            module_match=re.search(r"(?m)^module\s+([^\s]+)",module_file.read_text())
            if not module_match:
                raise ValueError("Cannot determine root module")
            module=module_match.group(1).strip('"')
            command=[indexer,"index","--module-version=ace-review","--output",str((output/"index.scip").resolve()),"./..."]
            status.update({"module":module,"module_root":module_root,"environment":environment,"command":command,
                           "cache_path":str(output),"indexer_version":subprocess.check_output([indexer,"--version"],text=True,timeout=10).strip()})
            result=subprocess.run(command,cwd=snapshot,env=env,capture_output=True,text=True,timeout=timeout)
            (output/"index.log").write_text(result.stdout+"\n"+result.stderr)
            status.update({"returncode":result.returncode,"stderr_tail":result.stderr[-6000:]})
            if result.returncode != 0 or not (output/"index.scip").exists():
                raise RuntimeError("SCIP indexing failed; see index.log")
            exported=subprocess.run([exporter,str(output/"index.scip")],capture_output=True,text=True,timeout=120,check=True)
            raw=json.loads(exported.stdout)
            missing,excluded=attach_sources(raw,snapshot)
            documents=raw.get("documents",[])
            if not documents:
                # scip-go can return exit 0 after go/packages loads zero packages.
                # Preserve the underlying Go diagnostic instead of treating this
                # as a valid empty project. Never repair the benchmark go.mod.
                probe=subprocess.run(["go","list","-e","-json=ImportPath,Error,DepsErrors","./..."],
                                     cwd=snapshot,env=env,capture_output=True,text=True,timeout=60)
                status["package_loading_probe"]={"returncode":probe.returncode,
                                                  "stderr":probe.stderr[-6000:],"stdout":probe.stdout[-6000:]}
            tracked=subprocess.check_output(["git","ls-files","-z"],cwd=snapshot).decode().split("\0")
            go_files={f for f in tracked if f.endswith(".go")}
            indexed={d["relativePath"] for d in documents}
            diagnostic_counts={}
            for d in documents:
                for o in d.get("occurrences",[]):
                    for diag in o.get("diagnostics",[]):
                        label=str(diag.get("severity","unspecified"))
                        diagnostic_counts[label]=diagnostic_counts.get(label,0)+1
            status.update({"documents":len(documents),"tracked_go_files":len(go_files),
                           "index_scope":f"module {module_root} ./... under recorded host build configuration",
                           "nested_module_files":[f for f in tracked if f.endswith("/go.mod")],
                           "indexed_tracked_go_files":len(indexed & go_files),
                           "tracked_file_coverage":len(indexed & go_files)/len(go_files) if go_files else None,
                           "missing_source_documents":missing,"excluded_outside_checkout_documents":excluded,
                           "diagnostics_by_severity":diagnostic_counts,
                           "coverage_note":"Tracked-file coverage is not compile-target or semantic completeness; build-tag and nested-module exclusions remain possible."})
            if not documents or missing:
                raise ValueError("Empty index or unavailable source documents")
            if module_root != ".":
                for document in documents:
                    document["relativePath"]=(relative/document["relativePath"]).as_posix()
                    document["indexModule"]=module
            with gzip.open(payload,"wt",encoding="utf-8") as stream:
                json.dump(raw,stream)
            status["available"]=True
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        status["error"]=f"{type(error).__name__}: {error}"
    status["duration_seconds"]=time.monotonic()-started
    if output is not None:
        write_json(output/"status.json",status)
    return (raw if status["available"] else None),status


def prepare_task(task, args) -> dict:
    cache=RepositoryCache(args.repo_cache)
    repo,status=cache.ensure(task)
    if repo is None:
        return {"task_id":task.id,"available":False,"repository_status":status}
    base_commit=task.metadata.get("merge_base_commit") or task.base_commit
    b,bs=index_revision(repo,base_commit,Path(args.index_cache),args.indexer,args.exporter,args.timeout)
    h,hs=index_revision(repo,task.head_commit,Path(args.index_cache),args.indexer,args.exporter,args.timeout)
    record={"task_id":task.id,"repository":task.repo,"base":bs,"head":hs,
            "available":b is not None and h is not None,"requests":[],"audits":{},
            "base_is_recorded_merge_base":bool(task.metadata.get("merge_base_commit"))}
    if not record["available"]:
        return record
    base=SemanticIndex(b,bs["module"],"base");head=SemanticIndex(h,hs["module"],"head")
    all_candidates={}
    for use_delta in (False,True):
        candidates,audit=build_candidates(base,head,task.diff,use_delta=use_delta,max_hops=args.max_hops)
        key="delta" if use_delta else "head"
        all_candidates[key]=[c.to_dict() for c in candidates]
        record["audits"][key]=audit
        for ranked in ((False,True) if use_delta else (True,)):
            condition="scip_delta" if use_delta and ranked else "scip_delta_unranked" if use_delta else "scip_head"
            selected=select_evidence(candidates,args.token_budget,ranked=ranked)
            request=make_request(task,condition,selected)
            request["context_status"]={"requested":[condition],"present":[condition] if selected else [],
                                       "unavailable":[] if candidates else [condition],
                                       "token_budget":args.token_budget,"context_tokens":sum(x.token_cost for x in selected),
                                       "candidate_count":len(candidates),"selector":"coverage_density_v1" if ranked else "path_order",
                                       "token_accounting":"UTF8 bytes/4 estimate; not tokenizer-exact",
                                       "base_commit":base_commit,"head_commit":task.head_commit}
            record["requests"].append(request)
    folder=Path(args.output_dir)/"candidates";folder.mkdir(parents=True,exist_ok=True)
    with gzip.open(folder/f"{task.id}.json.gz","wt",encoding="utf-8") as stream:
        json.dump(all_candidates,stream)
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",default="data/manifests/aacr_go_pure_17_hydrated.jsonl")
    parser.add_argument("--repo-cache",default="data/repos")
    parser.add_argument("--index-cache",default="runs/scip-index-cache")
    parser.add_argument("--indexer",default="tools/bin/scip-go")
    parser.add_argument("--exporter",default="tools/bin/scip-export")
    parser.add_argument("--output-dir",default="runs/delta_scip_pilot")
    parser.add_argument("--task-id",action="append")
    parser.add_argument("--timeout",type=int,default=600)
    parser.add_argument("--token-budget",type=int,default=4096)
    parser.add_argument("--max-hops",type=int,default=2)
    parser.add_argument("--jobs",type=int,default=2)
    parser.add_argument("--baseline-requests",default="runs/aacr_go_pure_17_requests.jsonl")
    args=parser.parse_args()
    output=Path(args.output_dir);output.mkdir(parents=True,exist_ok=True)
    tasks=load_tasks(args.manifest)
    if args.task_id:
        tasks=[t for t in tasks if t.id in args.task_id]
    # Version selector/config separately from immutable index caches.
    source_hashes={}
    for name in ("delta_scip.py","scip_batch.py","repository.py","schema.py","conditions.py","io.py"):
        source=Path(__file__).with_name(name)
        destination=output/"source"/"ace_review"/name
        destination.parent.mkdir(parents=True,exist_ok=True)
        payload=source.read_bytes();destination.write_bytes(payload)
        source_hashes[name]=hashlib.sha256(payload).hexdigest()
    config=vars(args)|{"method":"DeltaSCIP v1 exploratory","source_sha256":source_hashes,
                      "code_hash":hashlib.sha256(json.dumps(source_hashes,sort_keys=True).encode()).hexdigest(),
                      "source_snapshot":"source/ace_review","oracle_access_in_retriever":False}
    write_json(output/"config.json",config)
    records=[]
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        future_tasks={executor.submit(prepare_task,t,args):t for t in tasks}
        for future in as_completed(future_tasks):
            task=future_tasks[future]
            try:
                record=future.result()
            except Exception as error:
                record={"task_id":task.id,"available":False,"error":str(error),"requests":[]}
            records.append(record)
            write_json(output/"tasks"/f"{task.id}.json",record)
            print(json.dumps({"task_id":task.id,"available":record["available"],
                              "audit":record.get("audits",{}).get("delta",{})}),flush=True)
    records.sort(key=lambda r:r["task_id"])
    task_by_id={t.id:t for t in tasks}
    baselines=[]
    if args.baseline_requests and Path(args.baseline_requests).exists():
        for row in read_jsonl(args.baseline_requests):
            if row["task_id"] in task_by_id and row["condition"] in {"diff","bm25","lsp"}:
                if row["diff"] != task_by_id[row["task_id"]].diff:
                    raise ValueError("Baseline diff mismatch")
                baselines.append(row)
    requests=baselines+[row for rec in records for row in rec.get("requests",[])]
    with (output/"requests.jsonl").open("w") as stream:
        for row in requests:
            stream.write(json.dumps(row,ensure_ascii=False)+"\n")
    audits=[r["audits"]["delta"] for r in records if r["available"]]
    mapped=sum(a["mapped_changed_go_lines"] for a in audits)
    changed=sum(a["changed_go_lines"] for a in audits)
    summary={"tasks":len(tasks),"paired_index_success":sum(r["available"] for r in records),
             "requests":len(requests),"changed_go_lines_successful_pairs":changed,
             "mapped_changed_go_lines":mapped,"anchor_coverage_successful_pairs":mapped/changed if changed else None,
             "oracle_evidence_recall":None,"oracle_status":"Requires independent endpoint annotation; existing notes are provisional, not ground truth.",
             "failed_tasks":[r["task_id"] for r in records if not r["available"]],
             "candidate_cap_tasks":[r["task_id"] for r in records if r.get("audits",{}).get("delta",{}).get("candidate_cap_reached")],
             "status":"retrieval_preparation_only_no_review_claim"}
    write_json(output/"summary.json",summary)
    print(json.dumps(summary),flush=True)


if __name__ == "__main__":
    main()
