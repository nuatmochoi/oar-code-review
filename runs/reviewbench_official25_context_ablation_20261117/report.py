#!/usr/bin/env python3
"""Final-only official-score report; read gold only after all reviews finish.

No API calls.  ``--validate-historical`` checks the reader against saved scorer
outputs without writing a report or treating those results as this experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from config import ARMS, LABELS, MODEL, OLD, ORIGINAL, ROOT, SOURCE_CAP, TOKEN_CAP, TOTAL_USD
from runtime_cost import summarize as summarize_cost

COUNT_KEYS = (
    "candidate_count", "golden_tp_count", "matched_count", "matched_tp_count",
    "unmatched_count", "unmatched_tp_count", "unmatched_fp_count", "golden_covered_count",
)
STATUS_COUNT_KEYS = {
    "matched_tp": "matched_tp_count", "novel_tp": "unmatched_tp_count",
    "novel_fp": "unmatched_fp_count",
}
STRATA = (
    ("new_body", "새 본문·물리행 추가", "PR_indices_with_new_LSP_physical_rows", 15),
    ("metadata_only", "위치·관계 메타데이터만 추가", "PR_indices_with_metadata_only_LSP_additions", 4),
    ("identical", "AST와 LSP 입력 근거 동일", "PR_indices_with_identical_AST_LSP_evidence", 6),
)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def divide(numerator, denominator):
    return numerator / denominator if denominator else None


def harmonic(precision, recall):
    if precision is None or recall is None:
        return None
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def metrics(counts):
    """Official scorer.ts formulas, aggregated counts first (micro, not macro)."""
    c = {key: counts[key] for key in COUNT_KEYS}
    for key, value in c.items():
        require(type(value) is int and value >= 0, f"Invalid count {key}: {value}")
    require(c["matched_count"] + c["unmatched_count"] == c["candidate_count"], "Candidate partition mismatch")
    require(c["unmatched_tp_count"] + c["unmatched_fp_count"] == c["unmatched_count"], "Unmatched partition mismatch")
    require(c["matched_tp_count"] <= c["matched_count"], "Matched TP exceeds matched count")
    require(c["golden_covered_count"] <= c["golden_tp_count"], "Covered gold exceeds TP gold")
    c["grounded_precision"] = divide(c["matched_tp_count"], c["matched_count"])
    c["grounded_recall"] = divide(c["golden_covered_count"], c["golden_tp_count"])
    c["augmented_precision"] = divide(c["matched_tp_count"] + c["unmatched_tp_count"], c["candidate_count"])
    c["augmented_recall"] = divide(c["golden_covered_count"] + c["unmatched_tp_count"], c["golden_tp_count"] + c["unmatched_tp_count"])
    c["grounded_F1"] = harmonic(c["grounded_precision"], c["grounded_recall"])
    c["augmented_F1"] = harmonic(c["augmented_precision"], c["augmented_recall"])
    c["matched_fp_count"] = c["matched_count"] - c["matched_tp_count"]
    c["total_FP"] = c["matched_fp_count"] + c["unmatched_fp_count"]
    c["reports"] = c["candidate_count"]
    return c


def assert_metrics(actual, expected, label):
    for key in COUNT_KEYS + ("grounded_precision", "grounded_recall", "augmented_precision", "augmented_recall"):
        left, right = actual[key], expected[key]
        require(left is None and right is None or left is not None and right is not None and math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12), f"{label}: {key} mismatch: {left} != {right}")


def aggregate(rows):
    return metrics({key: sum(row[key] for row in rows) for key in COUNT_KEYS})


def gold_directory():
    for folder in (ROOT / "arms/B0/upstream/golden", ROOT / "upstream/golden", OLD / "upstream/golden", ORIGINAL / "upstream/golden"):
        if folder.is_dir():
            return folder
    raise FileNotFoundError("No frozen official gold directory is available for evaluation")


def scoring_exit_path(arm):
    folder = ROOT / "arms" / arm / "scoring"
    numbered = [path for path in folder.glob("exit-*.json") if path.stem.removeprefix("exit-").isdigit()]
    if numbered:
        return max(numbered, key=lambda path: int(path.stem.removeprefix("exit-")))
    return folder / "exit-1.json"


def read_scoring(folder, expected_keys, gold_dir):
    score, details = load(folder / "results.json"), load(folder / "results.details.json")
    require(score["pr_count"] == len(expected_keys), f"{folder}: incomplete pr_count")
    require(len(details) == len(expected_keys), f"{folder}: incomplete details")
    require(len(score["per_pr"]) == len(expected_keys), f"{folder}: incomplete per_pr")
    require({row["pr_key"] for row in details} == expected_keys, f"{folder}: details PR identity mismatch")
    require({row["pr_key"] for row in score["per_pr"]} == expected_keys, f"{folder}: scores PR identity mismatch")
    per_pr = {row["pr_key"]: metrics(row["metrics"]["overall"]) for row in score["per_pr"]}
    covered = set()
    statuses = Counter()
    gold_hashes = {}
    for row in details:
        key = row["pr_key"]
        gold_path = gold_dir / f"{key}.json"
        gold = load(gold_path)["findings"]
        gold_hashes[str(gold_path)] = sha(gold_path)
        require(sum(item["tp_fp"] == "tp" for item in gold) == per_pr[key]["golden_tp_count"], f"{key}: gold TP count mismatch")
        local_status = Counter(finding["status"] for finding in row["candidate_findings"])
        require(set(local_status) <= {"matched_tp", "matched_fp", "novel_tp", "novel_fp"}, f"{key}: unresolved/unknown judge status {local_status}")
        require(len(row["candidate_findings"]) == per_pr[key]["candidate_count"], f"{key}: report count mismatch")
        for status, metric in STATUS_COUNT_KEYS.items():
            require(local_status[status] == per_pr[key][metric], f"{key}: {status} count mismatch")
        require(local_status["matched_fp"] == per_pr[key]["matched_fp_count"], f"{key}: matched FP mismatch")
        statuses.update(local_status)
        local_covered = set()
        for finding in row["candidate_findings"]:
            for number in finding["covered_golden_indices"]:
                require(type(number) is int and 0 <= number < len(gold), f"{key}: invalid gold index {number}")
                if gold[number]["tp_fp"] == "tp":
                    local_covered.add((key, number))
        require(len(local_covered) == per_pr[key]["golden_covered_count"], f"{key}: covered gold mismatch")
        covered.update(local_covered)
    overall = aggregate(list(per_pr.values()))
    assert_metrics(overall, score["micro"]["overall"], str(folder))
    require(len(covered) == overall["golden_covered_count"], "Covered gold micro mismatch")
    return {"score": score, "metrics": overall, "per_pr": per_pr, "covered": covered,
            "statuses": dict(statuses), "gold_hashes": gold_hashes}


def paired(before, after):
    retained, lost, added = before & after, before - after, after - before
    return {"before_gold_TP": len(before), "after_gold_TP": len(after),
            "retained": len(retained), "lost": len(lost), "added": len(added),
            "retention_rate": divide(len(retained), len(before)), "net_gold_TP": len(added) - len(lost),
            "retained_gold_ids": sorted(retained), "lost_gold_ids": sorted(lost), "added_gold_ids": sorted(added)}


def delta(left, right):
    return None if left is None or right is None else right - left


def pct(value):
    return "—" if value is None else f"{100 * value:.2f}%"


def pp(value):
    return "—" if value is None else f"{100 * value:+.2f}pp"


def usd(value):
    return f"${value:.4f}"


def write_once(path, value):
    path = Path(path)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        require(path.read_text(encoding="utf-8") == text, f"Refusing to replace an existing different report: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)


def build():
    manifest = load(ROOT / "public/manifest.json")
    provenance = load(ROOT / "audit/source_provenance.json")
    require(len(manifest) == 25 and {row["index"] for row in manifest} == set(range(25)), "Expected fixed 25-PR manifest")
    keys = {row["key"] for row in manifest}
    require(len(keys) == 25, "Duplicate PR keys")
    expected_paths = [ROOT / "arms" / arm / "scoring" / name for arm in ARMS for name in ("results.json", "results.details.json")]
    expected_paths += [scoring_exit_path(arm) for arm in ARMS]
    expected_paths += [ROOT / "arms" / arm / "review" / f'{row["index"]:02d}' / "complete.json" for arm in ARMS for row in manifest]
    missing = [str(path.relative_to(ROOT)) for path in expected_paths if not path.exists()]
    require(not missing, "All 25 reviews and official scores must finish in all three arms before reporting; missing: " + ", ".join(missing))
    for arm in ARMS:
        require(load(scoring_exit_path(arm))["returncode"] == 0, f"{arm}: official scoring process failed")

    # Reviewer completion is established before evaluator-only gold is opened.
    gold_dir = gold_directory()
    scored = {arm: read_scoring(ROOT / "arms" / arm / "scoring", keys, gold_dir) for arm in ARMS}
    base_eval = scored["B0"]["score"]["eval_config"]
    for arm in ARMS:
        require(scored[arm]["score"]["eval_config"] == base_eval, f"{arm}: different judge/model/prompt/gold/evaluated PR configuration")
        require(scored[arm]["metrics"]["golden_tp_count"] == 352, f"{arm}: expected original 352 TP gold items")
    cost = summarize_cost(ROOT)
    src_by_index = {row["index"]: row for row in provenance["rows"]}
    arms = {}
    per_pr = []
    all_response_models = set()
    for arm in ARMS:
        completes, conversions, response_models = [], [], set()
        for ref in manifest:
            folder = ROOT / "arms" / arm / "review" / f'{ref["index"]:02d}'
            complete, conversion = load(folder / "complete.json"), load(folder / "conversion.json")
            require(complete["index"] == ref["index"] and complete["key"] == ref["key"] and complete["arm"] == arm, f"{arm}: completed review identity mismatch")
            require(complete["logical_model_calls"] == 2, f"{arm}: expected two logical model calls per PR")
            require(complete["source_tokens"] == src_by_index[ref["index"]]["arms"][arm]["source_tokens"] <= SOURCE_CAP, f"{arm}: source tokens changed")
            require(conversion["official"] == complete["findings"] == scored[arm]["per_pr"][ref["key"]]["candidate_count"], f"{arm}/{ref['key']}: published/scored report count mismatch")
            require(conversion["raw"] == complete["raw_findings"], "Raw report conversion mismatch")
            completes.append(complete)
            conversions.append(conversion)
            for phase in ("plan", "final"):
                response = load(folder / phase / "response.json")
                require(response.get("status") == "completed", f"{arm}/{ref['key']}: incomplete model response")
                response_models.add(response.get("model", "not_returned"))
        all_response_models.update(response_models)
        c = cost["arms"][arm]
        source = provenance["summary"]["arm_totals"][arm]
        arms[arm] = {
            "label": LABELS[arm], "PRs": 25, **scored[arm]["metrics"],
            "judge_status_counts": scored[arm]["statuses"],
            "raw_reports": sum(row["raw"] for row in conversions),
            "unmapped_reports": sum(len(row["unmapped"]) for row in conversions),
            "logical_review_calls": sum(row["logical_model_calls"] for row in completes),
            "logical_review_model_tokens": sum(row["model_tokens"] for row in completes),
            "review_token_cap_per_PR": TOKEN_CAP,
            "all_review_token_caps_respected": all(row["token_cap_respected"] and row["model_tokens"] <= TOKEN_CAP for row in completes),
            "review_active_seconds_sum": sum(row["seconds"] for row in completes),
            "review_active_seconds_max": max(row["seconds"] for row in completes),
            "returned_model_names": sorted(response_models),
            "cost": c,
            "usd_review_judge_usage_estimate": c["review"]["usd_usage_estimate"] + c["judge"]["usd_usage_estimate"],
            "usd_upper_including_unknown": c["review"]["usd_upper_including_unknown"] + c["judge"]["usd_upper_including_unknown"],
            "historical_source": source,
            "historical_native_seconds": source["historical_collection_cost"].get("native_seconds", 0),
            "new_native_collection_calls": 0,
        }

    comparisons = {}
    for before, after in (("B0", "B1"), ("B1", "B2"), ("B0", "B2")):
        comparisons[f"{before}_to_{after}"] = {
            "before": before, "after": after, **paired(scored[before]["covered"], scored[after]["covered"]),
            "grounded_F1_delta": delta(arms[before]["grounded_F1"], arms[after]["grounded_F1"]),
            "augmented_F1_delta": delta(arms[before]["augmented_F1"], arms[after]["augmented_F1"]),
            "total_FP_delta": arms[after]["total_FP"] - arms[before]["total_FP"],
        }
    strata, seen_indices = [], set()
    for key, label, source_key, expected_n in STRATA:
        indices = provenance["summary"][source_key]
        require(len(indices) == expected_n and not seen_indices.intersection(indices), "Source strata changed or overlap")
        seen_indices.update(indices)
        stratum_keys = {ref["key"] for ref in manifest if ref["index"] in indices}
        values = {arm: aggregate([scored[arm]["per_pr"][k] for k in stratum_keys]) for arm in ARMS}
        p = paired({item for item in scored["B1"]["covered"] if item[0] in stratum_keys}, {item for item in scored["B2"]["covered"] if item[0] in stratum_keys})
        strata.append({"key": key, "label": label, "PRs": len(indices), "indices": indices,
                       "arms": values, "B1_to_B2": p, "grounded_F1_delta": delta(values["B1"]["grounded_F1"], values["B2"]["grounded_F1"])})
    require(seen_indices == set(range(25)), "Source strata do not cover the cohort")
    for ref in manifest:
        src = src_by_index[ref["index"]]
        key = ref["key"]
        p = paired({item for item in scored["B1"]["covered"] if item[0] == key}, {item for item in scored["B2"]["covered"] if item[0] == key})
        per_pr.append({"index": ref["index"], "key": key, "nwo": ref["nwo"], "pr_number": ref["pr_number"],
                       "language": src["language"], "added_physical_rows": src["incremental_LSP"]["physical_source_rows"],
                       "added_metadata_records": src["incremental_LSP"]["typed_metadata_records"],
                       "arms": {arm: scored[arm]["per_pr"][key] for arm in ARMS}, "B1_to_B2": p})
    inputs = [ROOT / "public/manifest.json", ROOT / "audit/source_provenance.json", ROOT / "audit/historical_source_hashes.json"]
    inputs += [ROOT / "arms" / arm / "scoring" / name for arm in ARMS for name in ("results.json", "results.details.json")]
    inputs += [scoring_exit_path(arm) for arm in ARMS]
    return {
        "status": "complete_all_75_reviews_and_scores", "PRs_per_arm": 25, "gold_TP_count": 352,
        "arms": arms, "paired_gold_comparisons": comparisons, "source_strata": strata, "per_pr": per_pr,
        "cost": cost, "budget_upper_within_total": cost["usd_upper_including_unknown"] <= TOTAL_USD + 1e-6,
        "design": {"requested_model_alias": MODEL, "returned_model_names": sorted(all_response_models),
                   "judge_eval_config": base_eval, "logical_calls_per_PR_per_arm": 2, "repetitions": 1,
                   "review_tokens_per_PR_ceiling": TOKEN_CAP, "judge_tokens_separate": True,
                   "common_source_reference_tokens_ceiling": SOURCE_CAP,
                   "original_AST_collection_cap": 3000, "original_LSP_cumulative_collection_cap": 6000,
                   "source_collection_replay": True, "same_public_PR_title_body_diff": True,
                   "new_native_collection_calls": 0, "runtime_probe_calls": 0, "SCIP_calls": 0,
                   "development_cohort_not_independent_generalization": True, "human_validation_performed": False,
                   "old_22_60_percent_result_used_as_baseline": False},
        "input_hashes": {str(path): sha(path) for path in inputs},
        "evaluator_only_gold_file_hashes": scored["B0"]["gold_hashes"],
    }


def markdown(result):
    arms, comp, cost = result["arms"], result["paired_gold_comparisons"], result["cost"]
    lines = ["# 공식 ReviewBench 25개 PR: diff → AST → LSP 비교", "",
             f"세 조건의 새 리뷰·공식 채점 75건이 모두 완료됐다. 같은 25개 PR·BASE/HEAD와 정답표의 실제 결함 항목 352개를 비교했다. AST 추가의 Grounded F1 차이는 {pp(comp['B0_to_B1']['grounded_F1_delta'])}, AST 위에 LSP를 추가한 차이는 {pp(comp['B1_to_B2']['grounded_F1_delta'])}다.", "",
             "## 같은 조건에서 무엇을 추가했는가", "",
             "| 조건 | 제공한 정보 | 동일한 모델 처리 |", "|---|---|---|",
             "| B0 · 공개 PR·diff만 | PR 제목·설명, 변경 목록, 최종 리뷰의 전체 diff | 계획 1회 → 최종 리뷰 1회 |",
             "| B1 · AST 주변 본문 추가 | B0 + 변경 위치를 감싸는 함수·클래스의 실제 BASE/HEAD 본문 | 계획 1회 → 최종 리뷰 1회 |",
             "| B2 · LSP 근거 추가 | B1을 그대로 보존 + 정의·참조 등의 위치/관계 후보와 연결해 확보한 실제 본문 | 계획 1회 → 최종 리뷰 1회 |", "",
             f"모든 조건은 `{MODEL}`, reasoning low, 동일 보고 schema·최종 reviewer·공식 scorer다. 계획은 각 조건의 입력으로 새로 생성했다. 계획 단계에는 변경 목록·hunk 표본과 해당 조건의 근거, 최종 단계에는 전체 diff를 전달한다. 계획에서 만든 질문을 실제 도구로 재조회하는 agentic 실험은 아니다. PR당 reviewer 총 {TOKEN_CAP:,} 토큰 상한과 6,000 source reference-token 상한을 적용했으며 judge 비용·토큰은 별도다.", "",
             "## 공식 평가 결과: micro 집계", "",
             "| 조건 | gold 탐지/352 | gold 재현율 | Grounded F1 | Augmented F1 | 보고 수 | 오탐 합계 | 리뷰+채점 비용 추정 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for arm in ARMS:
        row = arms[arm]
        lines.append(f"| {arm} · {row['label']} | {row['golden_covered_count']} | {pct(row['grounded_recall'])} | {pct(row['grounded_F1'])} | {pct(row['augmented_F1'])} | {row['reports']} | {row['total_FP']} | {usd(row['usd_review_judge_usage_estimate'])} |")
    lines += ["", "F1은 전체 PR의 계수를 먼저 합친 micro 정밀도·재현율의 조화평균이다. 보고 하나가 여러 gold 항목과 대응할 수 있어 ‘정탐 보고 수’와 ‘gold 탐지 수’는 다르다. 오탐 합계는 정답표의 오탐 항목에 대응한 경고(matched FP)와 정답표 밖에서 채점 AI가 오탐으로 판정한 경고(unmatched FP)의 합이다.", "",
              "Grounded 정밀도는 **정답표와 대응된 보고만** 분모에 넣고, Grounded 재현율은 정답표의 실제 결함 중 찾은 비율이다. 정답표 밖 보고는 Grounded 정밀도에 들어가지 않는다. Augmented는 정답표 밖 보고도 공식 채점 AI가 추가 판단하여 정밀도·재현율에 반영한다. AI가 인정한 새 결함은 실행·인간 검증으로 확정한 결함이 아니다. 두 지표는 자체 원인 F1/유효 보고 F1과 동일한 지표가 아니다.", "",
              "| 조건 | Grounded 정밀도 | Augmented 정밀도 | Augmented 재현율 | 대응 정탐 보고 | AI 인정 정답표 밖 보고 | matched FP + unmatched FP |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for arm in ARMS:
        row = arms[arm]
        lines.append(f"| {arm} | {pct(row['grounded_precision'])} | {pct(row['augmented_precision'])} | {pct(row['augmented_recall'])} | {row['matched_tp_count']} | {row['unmatched_tp_count']} | {row['matched_fp_count']} + {row['unmatched_fp_count']} |")
    lines += ["", "## 단계별 정탐 보존·추가·손실", "",
              "| 새 결과끼리 비교 | 이전 gold 정탐 | 보존 | 추가 | 손실 | 보존율 | 순증 | Grounded F1 차이 | Augmented F1 차이 | 오탐 차이 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key in ("B0_to_B1", "B1_to_B2", "B0_to_B2"):
        row = comp[key]
        lines.append(f"| {row['before']} → {row['after']} | {row['before_gold_TP']} | {row['retained']} | {row['added']} | {row['lost']} | {pct(row['retention_rate'])} | {row['net_gold_TP']:+d} | {pp(row['grounded_F1_delta'])} | {pp(row['augmented_F1_delta'])} | {row['total_FP_delta']:+d} |")
    lines += ["", "보존·추가·손실은 같은 `(PR, gold index)` 집합의 교집합·차집합이다. 정답표 밖 AI 인정 보고는 이 paired 집합에 넣지 않았다. 과거 22.60% 결과는 호출·입력 등이 다른 이전 실험이며 이번 B0나 paired 기준으로 사용하지 않았다.", "",
              "## LSP가 실제로 추가한 정보에 따른 진단", "",
              "| LSP 추가 정보 | PR 수 | B0 Grounded F1 | B1 Grounded F1 | B2 Grounded F1 | B1→B2 차이 | gold 보존/추가/손실 | 오탐 B1→B2 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in result["source_strata"]:
        values, p = row["arms"], row["B1_to_B2"]
        lines.append(f"| {row['label']} | {row['PRs']} | {pct(values['B0']['grounded_F1'])} | {pct(values['B1']['grounded_F1'])} | {pct(values['B2']['grounded_F1'])} | {pp(row['grounded_F1_delta'])} | {p['retained']}/{p['added']}/{p['lost']} | {values['B1']['total_FP']} → {values['B2']['total_FP']} |")
    lines += ["", "새 본문 15개 PR에서 추가된 물리행은 총 1,757행이다. LSP 추가 근거 177개는 본문 57개와 위치·관계 메타데이터 120개로 구성된다. 새 본문 층의 정답 증가도 그 본문이 직접 원인이었다는 증명은 아니다. 근거가 같은 6개 PR에서의 차이는 새 소스 확보 효과로 해석할 수 없으며, 독립 계획/최종 생성과 AI 채점의 변동을 포함한다. 메타데이터는 정적 후보 관계이며 실행 경로를 확정하지 않는다.", "",
              "## 비용·실패·입력 가용성", "",
              "| 조건 | 리뷰 API 비용 추정 | 채점 API 비용 추정 | usage 미확인 시도 | 미확인 예약 포함 상한 | 과거 native 시간 | 이번 native 재수집 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for arm in ARMS:
        row, c = arms[arm], arms[arm]["cost"]
        lines.append(f"| {arm} | {usd(c['review']['usd_usage_estimate'])} | {usd(c['judge']['usd_usage_estimate'])} | {c['review']['usage_unknown'] + c['judge']['usage_unknown']} | {usd(row['usd_upper_including_unknown'])} | {row['historical_native_seconds']:.3f}초 | 0회 |")
    lines += ["", f"실제 API usage에 동결한 과거 단가를 적용한 전체 추정은 **{usd(cost['usd_usage_estimate'])}**, 미확인 호출의 예약까지 포함한 상한은 **{usd(cost['usd_upper_including_unknown'])} / ${TOTAL_USD:.2f}**다. 청구서 확정 금액이 아니다. 캐시 읽기·쓰기와 실패/usage 미확인 시도는 `runtime_cost.summarize` 원장을 따른다. API usage 미확인 {cost['usage_unknown']}회는 비용 0으로 확정하지 않는다.", "",
              "| 조건 | 논리 리뷰 호출 | 실제 리뷰 모델 토큰 합계 | PR별 토큰 상한 준수 | raw→공식 보고 | anchor 매핑 제외 | 리뷰 active 시간 합계 |",
              "|---|---:|---:|---|---:|---:|---:|"]
    for arm in ARMS:
        row = arms[arm]
        lines.append(f"| {arm} | {row['logical_review_calls']} | {row['logical_review_model_tokens']:,} | {'예' if row['all_review_token_caps_respected'] else '아니오'} | {row['raw_reports']} → {row['reports']} | {row['unmapped_reports']} | {row['review_active_seconds_sum']:.1f}초 |")
    lines += ["", "active 시간 합계는 병렬 작업들의 소요시간 합이며 전체 실험의 벽시계 시간이 아니다. 과거 native 시간에는 Go AST 외부 프로세스도 포함되므로 전부 LSP 서버 시간으로 해석하지 않는다. B2의 과거 비용은 AST+LSP 누적값이며 B1 비용을 다시 더하지 않는다. 지원하지 않는 언어·미해결 위치는 입력 가용성 한계로 보존했고 해당 PR를 평가에서 제외하지 않았다.", "",
              "## 해석 범위", "",
              "- 기존 수집은 AST 3,000, AST+LSP 누적 6,000 reference-token cap으로 동결돼 있다. 이번 6,000 공통 상한은 최대치이며 실제 본문량을 동일하게 맞춘 비교가 아니다. AST 탐색에도 새로 6,000을 사용했을 때의 결과나 동일 사실을 다른 도구로 제공했을 때의 효과는 측정하지 않았다.",
              "- 이 25개 PR는 앞선 실험에서 반복 사용한 개발 cohort다. 독립 미사용 PR 일반화, 통계적 유의성, 리더보드 성능을 주장하지 않는다. 조건당 1회 생성·AI 채점이며 인간 추가 검토나 runtime probe는 수행하지 않았다.",
              f"- 요청한 모델명은 alias `{MODEL}`다. 응답에 기록된 모델명은 {', '.join('`' + name + '`' for name in result['design']['returned_model_names'])}다. alias는 장래 동일 가중치/동일 출력을 보장하지 않는다.",
              "- 이번 검증은 같은 reviewer에 제공한 diff/AST/LSP 근거의 효과다. 질문별 재탐색·원인 검증/철회·SCIP·다중 에이전트 debate의 효과로 확대 해석하지 않는다.",
              "- 최종 보고의 anchor·인용이 기계적으로 결속됐다는 사실만으로 원인 설명의 의미적 정확성을 보장하지 않는다. 이번 F1은 공식 채점기 판정이며 자체 유효 보고 F1을 대신하지 않는다.", "",
              "## PR별 결과", "",
              "각 셀의 세 숫자는 B0/B1/B2 순서다. 보존/추가/손실은 B1→B2다.", "",
              "| PR | 언어 | LSP 새 물리행 | gold 탐지 B0/B1/B2 | 보고 수 B0/B1/B2 | 오탐 B0/B1/B2 | gold 보존/추가/손실 |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for row in result["per_pr"]:
        p = row["B1_to_B2"]
        cells = ["/".join(str(row["arms"][arm][metric]) for arm in ARMS) for metric in ("golden_covered_count", "reports", "total_FP")]
        url = f"https://github.com/{row['nwo']}/pull/{row['pr_number']}"
        lines.append(f"| [{row['nwo']}#{row['pr_number']}]({url}) | {row['language']} | {row['added_physical_rows']} | {' | '.join(cells)} | {p['retained']}/{p['added']}/{p['lost']} |")
    lines += ["", "## 재현 자료", "",
              f"[동결 명세]({ROOT}/PROTOCOL_KO.md) · [수치·paired 항목·PR별 결과]({ROOT}/summary.json) · [입력·수집 provenance]({ROOT}/audit/source_provenance.json) · [기존 파일 보존 해시]({ROOT}/audit/historical_source_hashes.json)", "",
              "공식 scorer의 `results.json`·`results.details.json`은 각 `arms/B0`, `arms/B1`, `arms/B2`의 `scoring`에 보존된다. raw request/response, 계획, 실제 전달 근거, 위치 변환, API usage·실패 원장도 각각 보존한다. gold는 이 사후 보고 집계에만 사용하며 reviewer 입력으로 전달하지 않는다.", ""]
    if not result["budget_upper_within_total"]:
        lines += ["예산 검증 경고: 미확인 예약 포함 합계가 동결한 전체 예산 상한을 초과했다. 실제 원장과 원인을 확인해야 한다.", ""]
    return "\n".join(lines)


def update():
    result = build()
    report = markdown(result)
    write_once(ROOT / "summary.json", result)
    write_once(ROOT / "RESULTS_KO.md", report)
    write_once(ROOT.parents[1] / "paper/REVIEWBENCH_OFFICIAL25_CONTEXT_ABLATION_20261117_KO.md", report)
    return result


def validate_historical():
    """Read-only format/formula check, not an experiment result or output file."""
    keys = {row["key"] for row in load(ROOT / "public/manifest.json")}
    gold = OLD / "upstream/golden"
    checked = {}
    for arm in ("A", "B", "C"):
        value = read_scoring(OLD / "arms" / arm / "scoring", keys, gold)
        checked[arm] = {"PRs": len(value["per_pr"]), "covered_gold": len(value["covered"]), "FP": value["metrics"]["total_FP"], "micro_formulas_match": True}
    require(paired({("p", 0), ("p", 1)}, {("p", 1), ("p", 2)})["net_gold_TP"] == 0, "Paired set fixture failed")
    require(harmonic(0, 0) == 0 and harmonic(None, 0) is None, "Undefined F1 fixture failed")
    try:
        build()
    except (ValueError, FileNotFoundError) as error:
        incomplete_guard = str(error).startswith("All 25 reviews and official scores must finish")
        require(incomplete_guard, f"Unexpected current-run validation error: {error}")
    else:
        incomplete_guard = "all_results_already_complete"
    return {"historical_structure_checks_only": checked, "incomplete_report_guard": incomplete_guard, "written_files": [], "paid_API_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-historical", action="store_true")
    args = parser.parse_args()
    if args.validate_historical:
        print(json.dumps(validate_historical(), ensure_ascii=False, indent=2))
    else:
        result = update()
        print(json.dumps({"status": result["status"], "cost": result["cost"],
                          "metrics": {arm: {key: result["arms"][arm][key] for key in ("grounded_F1", "augmented_F1", "total_FP")} for arm in ARMS}}, ensure_ascii=False, indent=2))
