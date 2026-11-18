"""Post-retrieval diagnostics. Provisional block footprints are NOT Oracle gold.

This module may read annotations; the retriever must never import it. Existing
whole-block coordinates are retained without gold-conditioned span refinement.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path

from .io import load_tasks, read_jsonl, write_json


def footprint_metrics(blocks: list[dict], probe: dict) -> dict:
    required=set(range(probe["start_line"],probe["end_line"]+1))
    spans=[span for b in blocks for span in b.get('metadata',{}).get('source_spans',[
        {'file':b.get('file'),'start_line':b.get('start_line'),'end_line':b.get('end_line'),
         'version':b.get('metadata',{}).get('version','head')}])]
    covered={line for b in spans if b.get("file")==probe["file"]
             and b.get('version')==probe["version"]
             and b.get("start_line") is not None and b.get("end_line") is not None
             for line in range(max(probe["start_line"],b["start_line"]),min(probe["end_line"],b["end_line"])+1)}
    target=probe.get("target_line")
    return {"required_lines":len(required),"covered_lines":len(covered),
            "line_coverage":len(covered)/len(required),"full_footprint_covered":required<=covered,
            "target_line_covered":target in covered if target is not None else None}


def identical_revision_duplicates(blocks: list[dict]) -> dict:
    """Conservative exact source-text duplication; ignore displayed line numbers."""
    groups=defaultdict(dict)
    for block in blocks:
        version=block.get("metadata",{}).get("version")
        marker=f"Source ({version}):\n"
        if version not in {"base","head"} or marker not in block.get("content",""):
            continue
        source=block["content"].split(marker,1)[1]
        source=re.sub(r"(?m)^\d+: ","",source)
        groups[(block.get("file"),source)][version]=block.get("token_cost",0)
    pairs=[v for v in groups.values() if {"base","head"}<=v.keys()]
    return {"identical_cross_revision_pairs":len(pairs),
            "duplicated_packet_tokens_estimate":sum(min(v.values()) for v in pairs)}


def build_provisional_probes(annotations: list[dict], candidates: list[dict]) -> tuple[list, list]:
    by_key={(r["task_id"],r["gold_id"]):r for r in candidates}
    probes,skipped=[],[]
    for row in annotations:
        source=by_key.get((row["task_id"],row["gold_id"]),{})
        blocks={b["id"]:b for b in source.get("candidate_evidence",[])}
        for evidence_id in row.get("selected_evidence_ids",[]):
            block=blocks.get(evidence_id,{})
            common={"task_id":row["task_id"],"gold_id":row["gold_id"],"evidence_id":evidence_id,
                    "gold_validity_provisional":row.get("gold_validity")}
            if block.get("kind") not in {"lsp","bm25","testsource"} or not block.get("start_line") or not block.get("end_line"):
                skipped.append(common|{"reason":"No reliable head source-span footprint (e.g. historical diff)"})
                continue
            probes.append(common|{"file":block["file"],"version":"head","start_line":block["start_line"],
                                  "end_line":block["end_line"],"target_line":block.get("metadata",{}).get("target_line"),
                                  "source_content":block.get("content",""),
                                  "annotation_status":"provisional_block_footprint_not_minimal_oracle"})
    return probes,skipped


def audit_run(run_dir: Path, manifest: Path, annotations: Path, candidate_notes: Path, repo_cache: Path) -> dict:
    tasks={t.id:t for t in load_tasks(manifest)}
    records={r["task_id"]:r for p in sorted((run_dir/"tasks").glob("*.json")) for r in [json.loads(p.read_text())]}
    requests=list(read_jsonl(run_dir/"requests.jsonl"))
    by_request={(r["task_id"],r["condition"]):r for r in requests}
    probes,skipped=build_provisional_probes(list(read_jsonl(annotations)),list(read_jsonl(candidate_notes)))
    probes=[p for p in probes if p["task_id"] in records]
    rows=[]
    for probe in probes:
        task=tasks[probe["task_id"]]
        source=subprocess.check_output(["git","show",f"{task.head_commit}:{probe['file']}"],
                                       cwd=repo_cache/task.repo.replace('/','__'),text=True)
        actual="\n".join(source.splitlines()[probe["start_line"]-1:probe["end_line"]]).strip()
        verified=actual==probe.pop("source_content").strip()
        row=probe|{"source_span_verified_against_head":verified,"conditions":{},"pools":{}}
        path=run_dir/"candidates"/f"{probe['task_id']}.json.gz"
        if path.exists():
            with gzip.open(path,"rt") as f: pools=json.load(f)
            row["pools"]={k:footprint_metrics(v,probe) for k,v in pools.items()}
        for (task_id,condition),request in by_request.items():
            if task_id==probe["task_id"]:
                row["conditions"][condition]=footprint_metrics(request.get("contexts",[]),probe)
        rows.append(row)
    conditions=defaultdict(list)
    for request in requests:
        blocks=request.get("contexts",[])
        conditions[request["condition"]].append({"blocks":len(blocks),
            "tokens_estimate":sum(b.get("token_cost",0) for b in blocks),
            "base_blocks":sum(b.get("metadata",{}).get("version")=="base" for b in blocks),
            "test_blocks":sum(b.get("file","").endswith("_test.go") for b in blocks),
            **identical_revision_duplicates(blocks)})
    aggregate={condition:{"requests":len(items),**{
        "mean_"+key:statistics.mean(x[key] for x in items) for key in items[0]}}
        for condition,items in sorted(conditions.items())}
    summary=json.loads((run_dir/"summary.json").read_text())
    summary.update({"paired_index_success_rate":summary["paired_index_success"]/summary["tasks"],
                    "paired_indices_with_changed_anchor":sum(r.get("available",False) and r.get("audits",{}).get("delta",{}).get("seeds",0)>0 for r in records.values()),
                    "probes":rows,"unmapped_provisional_blocks":skipped,"condition_statistics":aggregate,
                    "diagnostic_warning":"Whole-block/target-line overlap is not minimal evidence recall, semantic sufficiency, or reviewer accuracy. Equivalent alternative evidence or unchanged base source is not credited by this revision-specific proxy.",
                    "quality_gates":{"paired_index_90pct":summary["paired_index_success"]/summary["tasks"]>=.9,
                                     "paired_scope_complete_90pct":summary.get('paired_scope_complete',summary['paired_index_success'])/summary['tasks']>=.9,
                                     "anchor_mapping_80pct":(summary["anchor_coverage_successful_pairs"] or 0)>=.8,
                                     "independent_oracle_candidate_recall_70pct":None},
                    "input_sha256":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                     (manifest,annotations,candidate_notes,run_dir/"requests.jsonl")}})
    summary['review_stage_ready']=all(value is True for value in summary['quality_gates'].values())
    summary['review_stage_blockers']=[name for name,value in summary['quality_gates'].items() if value is not True]
    write_json(run_dir/"retrieval_audit.json",summary)
    lines=["# DeltaSCIP 실행·검색 진단", "", "이 파일은 검색 단계 결과다. LLM review 성능이나 검증된 Oracle recall을 뜻하지 않는다.","",
           f"- paired index: {summary['paired_index_success']}/{summary['tasks']}",
           f"- 변경 Go 줄 매핑률(성공한 pair만): {summary['anchor_coverage_successful_pairs']:.1%}",
           f"- 후보 상한 도달 PR: {len(summary['candidate_cap_tasks'])}",
           "- 독립 Oracle 주석: 미완료. 기존 주석의 블록 전체 범위/참조 지점을 보존한 진단만 수행.","",
           "## 조건별 선택", "", "| 조건 | 요청 수 | 평균 블록 | 평균 token 추정 | 평균 base 블록 |",
           "|---|---:|---:|---:|---:|"]
    for name,m in aggregate.items():
        lines.append(f"| {name} | {m['requests']} | {m['mean_blocks']:.1f} | {m['mean_tokens_estimate']:.0f} | {m['mean_base_blocks']:.1f} |")
    lines.extend(["","## 잠정 블록 footprint 진단", "", "아래 값은 기존 선택 블록의 전체 줄 범위 중 포함된 비율이다. 최소 증거나 정답 회수율이 아니다. Diff 원문 자체의 증거는 이 표에서 세지 않는다.","",
                  "| PR / 기존 증거 | head pool | delta pool | LSP 선택 | head 선택 | delta 선택 |",
                  "|---|---:|---:|---:|---:|---:|"])
    for row in rows:
        def val(container,key):
            m=row[container].get(key)
            return f"{m['line_coverage']:.0%}" if m else "N/A"
        lines.append(f"| {row['task_id']} / {row['evidence_id']} | {val('pools','head')} | {val('pools','delta')} | {val('conditions','lsp')} | {val('conditions','scip_head')} | {val('conditions','scip_delta')} |")
    if 'scip_modules' in aggregate:
        lines.extend(['','## v2 선택 조건 비교','','모두 기존 잠정 블록의 head-source footprint 포함률이다. 앞 표의 head pool은 v2에서 재생성하지 않았다.','',
            '| PR | v1 Delta | 모듈 확장 | 중복 제거 | Witness flat | Bundle density | Bundle diversity |',
            '|---|---:|---:|---:|---:|---:|---:|'])
        for row in rows:
            values=[f"{row['conditions'][c]['line_coverage']:.0%}" if c in row['conditions'] else 'N/A'
                    for c in ('scip_delta','scip_modules','scip_modules_dedup','scip_bundle_flat','scip_bundles_density','scip_bundles')]
            lines.append('| '+row['task_id']+' | '+' | '.join(values)+' |')
    lines.extend(["", "동등한 대체 증거나 동일한 base source는 이 revision-specific proxy에서 정답으로 세지 않는다. 따라서 0%도 충분한 증거가 전혀 없다는 뜻은 아니다.", "",
                  "## Revision 중복 진단", "", "동일 file에서 줄 번호를 제외한 source text가 완전히 같은 base/head packet 쌍이다. 중복 packet token은 두 packet 중 작은 비용을 센 보수적 진단이다.", "",
                  "| 조건 | 요청당 평균 동일 source 쌍 | 요청당 평균 중복 packet token 추정 |", "|---|---:|---:|"])
    for name,m in aggregate.items():
        if name.startswith('scip_'):
            lines.append(f"| {name} | {m['mean_identical_cross_revision_pairs']:.1f} | {m['mean_duplicated_packet_tokens_estimate']:.0f} |")
    lines.extend(["", "실패 task를 성공한 SCIP 조건과 섞어 전체 평균을 직접 비교하면 안 된다. 후보 상한, host OS/build tags, root module만 인덱싱한 범위를 함께 보고한다.", ""])
    (run_dir/"RETRIEVAL_AUDIT_KO.md").write_text("\n".join(lines),encoding="utf-8")
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir",required=True,type=Path)
    parser.add_argument("--manifest",default="data/manifests/aacr_go_pure_17_hydrated.jsonl",type=Path)
    parser.add_argument("--annotations",default="runs/aacr_go_pure_17_oracle_repo_level_annotations_provisional.jsonl",type=Path)
    parser.add_argument("--candidate-notes",default="runs/aacr_go_pure_17_oracle_repo_level_candidates.jsonl",type=Path)
    parser.add_argument("--repo-cache",default="data/repos",type=Path)
    args=parser.parse_args()
    result=audit_run(args.run_dir,args.manifest,args.annotations,args.candidate_notes,args.repo_cache)
    print(json.dumps({"quality_gates":result["quality_gates"],"probes":len(result["probes"])}))


if __name__=="__main__":
    main()
