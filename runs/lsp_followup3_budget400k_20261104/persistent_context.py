"""One collector/LSP session across both acquisitions, common to all arms."""
from symbol_context import ContextCollector as Base
from context48 import source_tokens
class ContextCollector(Base):
    def set_phase(self,phase):
        self.phase=phase;self.budgets['max_native_calls']=34 if phase=='before_replan'else 48;self.budgets['max_file_reads']=212 if phase=='before_replan'else 256
        left=max(0,12000-source_tokens(self.evidence));self.active_source_cap=12000-min(2000,left//2)if phase=='before_replan'else 12000
    def query(self,q,*,keep_stage_cap=False):return super().query(q,keep_stage_cap=True)

def release_budget_cached_answers(frontier):
    removed=[]
    for key,value in list(frontier.cache.items()):
        raw=value.get('answer')or{};issues=raw.get('unresolved',[])
        if any(any(t in str(v)for t in('native_call_budget','file_read_budget','whole_body_source_token_cap','context_query_time_budget'))for v in issues):
            removed.append(list(key));del frontier.cache[key]
    return removed
