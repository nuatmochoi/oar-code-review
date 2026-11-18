"""Actual RPC plus manifest-only neutral read, with original route still rejected.

Model-free permission/transport fixture: the controller starts without a source
view while the collector retains its immutable bootstrap cache. This is not a
performance trial or proof that a newly necessary body was found.
"""
import copy,json
from common import ROOT,BUDGETS,load,write_new
from protected_run import cases
from persistent_context import ContextCollector
from loop_policy import Frontier,execute

def run():
    records=[]
    for lang in('Python','Go'):
        case=next(c for c in cases()if c['language']==lang);base=load(ROOT/'contexts'/case['id']/'context_LSP.json');folder=ROOT/'audit/native_recovery'/lang;tool=ContextCollector(case,folder,BUDGETS,initial_state=base)
        try:
            q=copy.deepcopy(tool.seed_questions()[0]);q.update(id='Q-test',claim_id='C-test',condition_ids=['intentionally-missing-test-target'],challenge_ids=[],exploration_id='')
            condition=dict(id='P-test',predicate='A source read can be performed without asserting a defect.',kind=q['kind'],snapshot=q['snapshot'],status='unresolved',evidence_ids=[],reason='Permission smoke only.',effect_if_true='Requires independent semantic interpretation.',effect_if_false='Remain unresolved.')
            claim=dict(id='C-test',claim='Model-free permission fixture, not a defect assertion.',trigger='Known public seed.',action='new',change_basis='none',reason='Exercise rejected-route source reading.',conditions=[condition],challenges=[],route_assignment=dict(current_condition_ids=['P-test'],historical=[]),termination='continue')
            f=Frontier({'claims':[claim],'questions':[]},[])
            plan=dict(claims=[claim],questions=[q],coverage=[],explorations=[],diff_facts=[],reasoning='No causal judgment.',termination_reason='Permission smoke only.')
            out=execute(f,plan,tool,enabled=True);assert out['core']['answers'][0]['execution']=='question_rejected';assert len(out['independent_reads'])==1
            read=out['independent_reads'][0];assert read['read_authority']['basis']=='frozen_repository_or_environment_source_manifest';assert not read['semantic_status_promoted'];assert f.raw_claims[0]['conditions'][0]['status']=='unresolved'
        finally:tool.close()
        methods=[load(p)['method']for p in(folder/'native').glob('**/rpc-*.json')];assert'textDocument/definition'in methods or'textDocument/references'in methods
        write_new(ROOT/'audit'/f'native_recovery_{lang}.json',dict(language=lang,question=q,record=out,rpc_methods=methods,model_calls=0,causal_accuracy_verified=False))
        records.append(dict(language=lang,original_route_still_rejected=True,manifest_only_authority=True,actual_rpc_methods=methods,semantic_status_promoted=False))
    write_new(ROOT/'audit/native_recovery_smoke.json',dict(passed=True,records=records,actual_model_calls=0,scope='Real source/native read plumbing from a controlled empty controller view; not evidence acquisition effectiveness.'))
    return dict(passed=True,actual_model_calls=0)
if __name__=='__main__':print(json.dumps(run()))
