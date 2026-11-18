"""Focused synthetic checks of the DIFF-only compatibility gate; no models."""
import copy
import json
from pathlib import Path

import audit

packet = {"source": "", "plain": "", "audit": {"source_lines": {}, "base_lines": {}}}
common = "[DIFF]\n--- a/example.py\n+++ b/example.py\n@@ -10,3 +10,2 @@\n def f():\n-    check()\n     return 1\n"
response = {
    "checks": [{"evidence_ids": ["DIFF"], "issue_indices": [0]}],
    "review": {"status": "reported_issues", "issues": [{"file": "example.py", "line": 11, "evidence_ids": ["DIFF"]}]},
    "context_assessment": {"used_evidence": [], "dismissed_evidence": [], "missing_connections": []},
}
errors, warnings, coverage = audit.response_checks(response, packet, common)
assert not errors and warnings == ["issue_line_diff_check_0"]
assert coverage["issue_diff_locations"] == [{"issue_index": 0, "file_in_diff": True, "head_hunk_line": True, "base_hunk_line": True}]
assert audit.response_checks(response, packet, common, allow_diff_files=False)[0] == ["issue_file_0"]
bad_file = copy.deepcopy(response)
bad_file["review"]["issues"][0]["file"] = "unseen.py"
assert "issue_file_0" in audit.response_checks(bad_file, packet, common)[0]
bad_evidence = copy.deepcopy(response)
bad_evidence["review"]["issues"][0]["evidence_ids"] = ["E-head-unseen"]
assert "issue_evidence_0" in audit.response_checks(bad_evidence, packet, common)[0]
aux = copy.deepcopy(response)
aux["context_assessment"]["used_evidence"] = [{"evidence_id": "E-head-unseen", "conclusion": "unsupported self-report"}]
assert not audit.response_checks(aux, packet, common)[0]
assert audit.response_checks(aux, packet, common)[2]["invalid_assessment_ids"] == ["E-head-unseen"]
files, positions = audit.diff_locations("[DIFF]\n--- a/deleted.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x = 1\n-y = 2\n")
assert files == {"deleted.py"} and positions["base"]["deleted.py"] == {1, 2} and not positions["head"]
result = {"status": "passed", "tests": ["DIFF-only issue accepted", "historical line warning retained", "unseen file rejected", "unseen primary citation rejected", "auxiliary citation errors separated", "deleted-file coordinates"], "model_calls": 0, "native_calls": 0}
(Path(__file__).resolve().parent / "audit_selfcheck_results.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
