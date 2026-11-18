"""Budget-only variants of two sealed handoff policies. No source/model calls."""
import copy
import json
import re

from bootstrap import PARENT, REPAIR
import causal_adapter as original_adapter
import oar_product_adapter as original_digest
import critic_handoff as repaired
import oar_core as oc

ARMS = ('legacy_1600', 'repaired_1600', 'legacy_full', 'repaired_full')
CASES = ('N03','N06','N07','H05','H10')
STATE = re.compile(r'<causal_review_state>(.*?)</causal_review_state>',re.S)

def load(path):
    return json.loads(path.read_text())

def context(group):
    low_legacy = load(REPAIR/'prepared/inputs'/f'{group}_legacy.json')
    low_repaired = load(REPAIR/'prepared/inputs'/f'{group}_repaired.json')
    refs = low_legacy['inherited_references']
    initial = load(PARENT/'results/reviews'/f'{group}_debate/initial.result.json')
    critic = load(PARENT/'results/reviews'/f'{group}_debate/update_1.result.json')
    review = load(PARENT/'results/reviews'/f'{group}_debate/review.result.json')
    state = json.loads(STATE.search(low_legacy['raw']['suffix']).group(1))
    runtime = {k:v for k,v in review['causal_policy']['probe_runtime'].items() if k!='python_executable'}
    provenance = {k:v['provenance'] for k,v in low_repaired['handoff_manifest']['sources'].items()
                  if v.get('provenance')}
    return dict(group=group,low_legacy=low_legacy,low_repaired=low_repaired,refs=refs,
                initial=initial,critic=critic,review=review,runtime=runtime,
                role=state['role'],instruction=state['instruction'],provenance=provenance)

def old_state(c, cap, digest_cap):
    digest, _, omitted = original_digest.digest_previous(json.loads(c['critic']['raw_response']),digest_cap)
    sections=dict(initial_raw_response=c['initial']['raw_response'],previous_response_digest=digest,
                  claims=c['review']['causal_audit']['verification']['claims'],
                  semantic_probe_results=original_adapter.probe_digest(c['review']['causal_audit']['probes']),
                  experimental_runtime=c['runtime'])
    overhead=oc.count_reference_tokens(repaired.OPEN+repaired.CLOSE)+4
    text,_,truncated=original_adapter.bounded_state(c['role'],c['instruction'],sections,cap=cap-overhead)
    state=repaired.OPEN+text+repaired.CLOSE
    return dict(state_text=state,tokens=oc.count_reference_tokens(state),
                manifest=dict(policy='sealed_legacy_selection',cap=cap,digest_cap=digest_cap,
                              digest_omitted=omitted,section_truncated=truncated,
                              budget_complete=not any(truncated.values()) and not any(
                                  v for k,v in omitted.items() if k!='duplicates_removed'),
                              structural_exclusions=['critic.review.summary','critic.review.observations',
                                                     'critic.review.context_assessment','other unprojected digest fields']))

def repaired_state(c,cap):
    built=repaired.build_handoff_state(role=c['role'],instruction=c['instruction'],
          initial_payload=json.loads(c['initial']['raw_response']),
          critic_payload=json.loads(c['critic']['raw_response']),
          claims=c['review']['causal_audit']['verification']['claims'],
          probe_results=original_adapter.probe_digest(c['review']['causal_audit']['probes']),
          experimental_runtime=c['runtime'],source_provenance=c['provenance'],cap=cap)
    m=built['manifest']
    m['budget_complete']=not m['blocked'] and all(r['complete'] or r['reason']=='redundant_initial_prose_policy' for r in m['records'])
    return built

def sufficient_cap(contexts):
    # Full text length is only a lower bound for a greedy allocator because its
    # intermediate omission list can be longer. Validate all allowed leaves at
    # each candidate cap, increasing deterministically before any model output.
    maximum=max(max(old_state(c,1000000,1000000)['tokens'],repaired_state(c,1000000)['tokens']) for c in contexts)
    cap=((maximum+1023)//1024)*1024
    while cap<=65536:
        if all(old_state(c,cap,cap)['manifest']['budget_complete'] and
               repaired_state(c,cap)['manifest']['budget_complete'] for c in contexts):
            return cap,maximum
        cap+=1024
    raise ValueError('No sufficiently complete common cap at or below65536; never dispatch')

def replace_state(raw,text):
    out=copy.deepcopy(raw);matches=list(STATE.finditer(raw['suffix']));assert len(matches)==1
    m=matches[0];out['suffix']=out['suffix'][:m.start()]+text+out['suffix'][m.end():]
    assert outside(out)==outside(raw)
    return out

def outside(raw):
    return {k:STATE.sub('<STATE>',v) if k=='suffix' else v for k,v in raw.items()}

def variants(c,cap):
    # Reconstruction is checked against actual sealed low-budget inputs.
    oldlow=old_state(c,1600,600)
    assert STATE.search(c['low_legacy']['raw']['suffix']).group(0)==oldlow['state_text']
    newlow=repaired_state(c,1600)
    assert STATE.search(c['low_repaired']['raw']['suffix']).group(0)==newlow['state_text']
    oldfull=old_state(c,cap,cap);newfull=repaired_state(c,cap)
    assert oldfull['manifest']['budget_complete'] and newfull['manifest']['budget_complete']
    states={'legacy_1600':oldlow,'repaired_1600':newlow,'legacy_full':oldfull,'repaired_full':newfull}
    out={}
    for arm,s in states.items():
        raw=replace_state(c['low_legacy']['raw'],s['state_text'])
        assert outside(raw)==outside(c['low_legacy']['raw'])==outside(c['low_repaired']['raw'])
        out[arm]=dict(raw=raw,state=s)
    assert out['legacy_1600']['raw']==c['low_legacy']['raw']
    assert out['repaired_1600']['raw']==c['low_repaired']['raw']
    return out
