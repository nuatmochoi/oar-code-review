"""Actual public-seed controller->LSP reads, no reviewer/gold/model calls."""
import copy,json,time
from pathlib import Path
from common import ROOT,BUDGETS,load,write_new
from diff48 import diff_evidence
from persistent_context import ContextCollector
from loop_policy import Frontier,execute
from causal_loop import cases

def run():
    records=[]
    for lang in ['Python','Go']:
        case=next(c for c in cases()if c['language']==lang);base=load(ROOT/'contexts'/case['id']/'context_LSP.json');es=diff_evidence(case['diff'])+base['evidence']
        tool=ContextCollector(case,ROOT/'audit/native_smoke'/lang,BUDGETS,initial_state=base)
        try:
            q=copy.deepcopy(tool.seed_questions()[0]);snap=q['snapshot'];q.update(id='Q-smoke',claim_id='C-smoke',condition_ids=['P-smoke'],challenge_ids=[],exploration_id='')
            condition=dict(id='P-smoke',predicate='The actual implementation of the public changed-call seed is not yet examined by this model-free check.',kind=q['kind'],snapshot=snap,status='unresolved',evidence_ids=[],reason='Exercise actual read plumbing only; no defect or API semantic judgment.',effect_if_true='Further causal interpretation would still require a reviewer.',effect_if_false='Keep the unknown explicit.')
            claim=dict(id='C-smoke',claim='Public changed code may require implementation context; this is not a defect claim.',trigger='Public diff symbol.',action='new',change_basis='none',reason='Native integration smoke, not a semantic assessment.',conditions=[condition],challenges=[],route_assignment=dict(current_condition_ids=['P-smoke'],historical=[]),termination='continue')
            f=Frontier({'claims':[claim],'questions':[]},es)
            plan=dict(claims=[claim],questions=[q],coverage=[dict(claim_id='C-smoke',target_kind='condition',target_id='P-smoke',mode='query',question_ids=['Q-smoke'],evidence_ids=[],reason='Fetch the actual public symbol implementation.')],explorations=[],diff_facts=[],reasoning='Native integration only.',termination_reason='Read public implementation candidate.')
            out=execute(f,plan,tool,enabled=True);assert out['core']['backend_query_attempts']==1
        finally:tool.close()
        paths=list((ROOT/'audit/native_smoke'/lang/'native').glob('**/rpc-*.json'));rpc=[load(p)for p in paths]
        methods=[v['method']for v in rpc];assert any(m in ['textDocument/definition','textDocument/references']for m in methods)
        capabilities=[v.get('result',{}).get('capabilities',{})for v in rpc if v['method']=='initialize']
        record=dict(language=lang,case_id=case['id'],question=q,controller_execution=out,actual_rpc_methods=methods,advertised_server_capabilities=capabilities,closed_context=tool.export(),source_only=True,model_calls=0)
        write_new(ROOT/'audit'/f'native_smoke_{lang}.json',record);records.append(dict(language=lang,backend_attempts=1,actual_rpc_methods=methods))
    write_new(ROOT/'audit/native_smoke.json',dict(passed=True,records=records,model_calls=0,no_gold_used=True,causal_accuracy_verified=False))
    return records

if __name__=='__main__':print(json.dumps(run()))
