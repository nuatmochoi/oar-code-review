"""One collector/LSP session across both acquisitions, common to all arms."""
from step_recovery import ContextCollector as Base
from context48 import source_tokens
class ContextCollector(Base):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.set_phase('before_replan')
    def set_phase(self,phase):
        self.phase=phase;self.budgets['max_native_calls']=34 if phase=='before_replan'else 48;self.budgets['max_file_reads']=212 if phase=='before_replan'else 256
        self.budgets['max_native_seconds']=120 if phase=='before_replan'else 180
        self.active_source_cap=10000 if phase=='before_replan'else 12000
    def query(self,q,*,keep_stage_cap=False):return super().query(q,keep_stage_cap=True)

def release_budget_cached_answers(frontier):
    removed=[]
    for key,value in list(frontier.cache.items()):
        raw=value.get('answer')or{};issues=raw.get('unresolved',[])
        if any(any(t in str(v)for t in('native_call_budget','native_time_budget','file_read_budget','whole_body_source_token_cap','context_query_time_budget'))for v in issues):
            removed.append(list(key));del frontier.cache[key]
    return removed
