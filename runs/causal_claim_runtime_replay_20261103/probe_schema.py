from schemas import obj,arr,S,enum
BINDING=obj(dict(module=S,symbol=S))
PROBE=obj(dict(id=S,question_ids=arr(S),claim_ids=arr(S),code=S,bindings=arr(BINDING),reason=S,scope=enum('analysis_profile'),fixture_origin=enum('analysis_probe_fixture')))
PLAN=obj(dict(assessment=enum('propose','no_admissible_probe'),probes=arr(PROBE),unresolved_reasons=arr(S)))
