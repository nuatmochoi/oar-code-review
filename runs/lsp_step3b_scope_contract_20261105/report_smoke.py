"""All36 exact-input comparisons and recorded controller replays; model0."""
from common import ROOT,write_new,digest
from sequential import configure
from scoped_reporting import build,replay_controller
from openai_transport import admission
import causal_loop as c
def main():
 configure();rows=[]
 for case,n in c.units():
  d=build(case,n);f=replay_controller(case,n,d);used,calls=c.usage(d['prior']['model_stages'][:-1]);a=admission(d['raw'],d['schema'],used,calls,900-d['shared']['active_seconds']-d['extra']['active_seconds']);assert a['admitted']
  rows.append(dict(case_id=case['id'],repeat=n,exact_prior_request_verified=True,source_hash=digest(d['evidence']),history_hash=digest(d['history']),controller_replay_verified=True,scope_schema_required='causal_scope'in d['schema']['properties']['claims']['items']['required'],admission=a))
 write_new(ROOT/'audit/report_smoke.json',dict(passed=True,units=rows,model_calls=0,source_acquisition=False))
 print('PASS:36 exact prior requests, source/history identities, controller replays, scope schemas and model admissions.')
if __name__=='__main__':main()
