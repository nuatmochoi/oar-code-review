"""Protected existing context, actual AST/LSP queries, no gold-dependent pruning."""
import ast,copy,json,time
from collections import defaultdict,deque
from common import ROOT,PROJECT,BUDGETS,load,sha,digest,write_new,source_tokens
from symbol_context import ContextCollector
from context48 import Limit
from controller_frontier import acquire,compact

ARMS=('BASE','BROAD','DEF','REF','GUARDED')
LABELS={'BASE':'기존 AST+LSP 수집 본문만','BROAD':'기존 본문 보존+기존 controller 확장','DEF':'기존 본문 보존+statement 심볼 정의 조회','REF':'기존 본문 보존+정의·참조 본문 조회','GUARDED':'기존 본문 보존+핵심 우선·일반 탐색 병행'}
PRIOR=PROJECT/'runs/lsp_followups_20261104'

def intervals(data,changed):
    spans=[];guards=[];calls=[]
    if isinstance(data['tree'],ast.AST):
        for n in ast.walk(data['tree']):
            if not hasattr(n,'lineno')or not hasattr(n,'end_lineno'):continue
            touched=any(n.lineno<=line<=n.end_lineno for line in changed)
            if isinstance(n,ast.stmt)and not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))and touched:
                # Compound statement's condition/header and changed body are
                # candidates, never a claim that every descendant is essential.
                end=(n.test.end_lineno if hasattr(n,'test')else n.iter.end_lineno if hasattr(n,'iter')else n.lineno)if isinstance(n,(ast.If,ast.While,ast.For,ast.AsyncFor,ast.With,ast.AsyncWith,ast.Try))else n.end_lineno
                spans.append((n.lineno,end))
                if hasattr(n,'test'):guards.append((n.test.lineno,n.test.end_lineno))
            if isinstance(n,ast.Call)and touched:calls.append((n.lineno,n.end_lineno))
    else:
        for call in data['calls']:
            a=call.get('start_line',call['line']);b=call.get('end_line',max([call['line']]+[v.get('end_line',call['line'])for v in call.get('args',[])]))
            if any(a<=n<=b for n in changed):spans.append((a,b));calls.append((a,b))
        for unit in data['tree'].get('units',[]):
            if any(unit['start_line']<=n<=unit['end_line']for n in changed):
                for cond in unit.get('conditions',[]):guards.append((cond['start_line'],cond['end_line']))
        spans.extend((n,n)for n in changed)
    return spans,guards,calls

def inventory(tool):
    values=[];errors=[]
    for snap in ['HEAD','BASE']:
        for path,changed in sorted(tool.changed[snap].items()):
            if path not in tool.snapshots[snap]['files']or not tool.language_for(path):continue
            tool.query_started=time.monotonic()
            try:data=tool._parse(snap,path)
            except (ValueError,OSError,SyntaxError,Limit)as exc:errors.append(dict(snapshot=snap,path=path,error=str(exc)));continue
            spans,guards,calls=intervals(data,changed)
            for item in data['_identifiers']:
                line=item['line'];on_statement=any(a<=line<=b for a,b in spans)
                near_guard=any(a<=line<=b for a,b in guards)
                if not(on_statement or near_guard):continue
                is_call=any(c['name']==item['name']and c.get('line',c.get('start_line'))==line for c in data['calls'])
                role='changed_callee'if is_call else'guard'if near_guard else'changed_statement_identifier'
                if len(item['name'])<2:role='short_value_identifier'
                score=5 if is_call else 4 if near_guard else 2 if item.get('role')=='attribute_access'else 1
                q=dict(id='Q-'+digest([snap,path,line,item['column'],item['name']])[:16],claim_id='exploration',snapshot=snap,file=path,line=line,column=item['column'],symbol=item['name'],query='definition',kind='implementation_body',reason='Resolve an AST identifier from a changed statement/related guard; unchanged callee tokens are included; no defect or runtime binding is presumed.')
                values.append(dict(question=q,role=role,priority=score,changed_line=line in changed))
    unique={digest({k:v['question'][k]for k in ['snapshot','file','line','column','symbol']}):v for v in values}
    return list(unique.values()),errors

def round_robin(values):
    groups=defaultdict(deque)
    for v in values:groups[v['question']['snapshot'],v['question']['file']].append(v)
    result=[]
    while any(groups.values()):
        for k in sorted(groups,key=lambda k:(k[0]!='HEAD',k[1])):
            if groups[k]:result.append(groups[k].popleft())
    return result

def queue(values,arm):
    broad=round_robin(sorted(values,key=lambda v:(v['question']['line'],v['question']['column'])))
    ranked=round_robin(sorted(values,key=lambda v:(-v['priority'],v['question']['line'])))
    if arm=='GUARDED':
        # Two priority slots then one breadth slot; never delete the inventory.
        a=deque(ranked);b=deque(broad);ordered=[];seen=set()
        while a or b:
            for src in [a,a,b]:
                while src and src[0]['question']['id']in seen:src.popleft()
                if src:
                    v=src.popleft();seen.add(v['question']['id']);ordered.append(v)
    else:ordered=broad
    queries=[]
    for row in ordered:
        q=copy.deepcopy(row['question']);queries.append(dict(question=q,selection_role=row['role'],priority=row['priority'],selection='priority_with_breadth_reserve'if arm=='GUARDED'else'changed_statement_all_identifiers'))
        if arm in ['REF','GUARDED']:
            ref=copy.deepcopy(q);ref.update(id=q['id']+'-R',query='references',kind='value_path',reason='Read usages/consumers around this actual static symbol; references are not execution or dataflow proof.')
            queries.append(dict(question=ref,selection_role=row['role'],priority=row['priority']))
    return queries

def one_arm(case,arm):
    folder=ROOT/'contexts'/case['id']/arm;dest=ROOT/'contexts'/case['id']/f'{arm}.json'
    if dest.exists():return load(dest)
    source=PRIOR/'contexts'/case['id']/'L6.json';base=load(source);prefix={e['id']for e in base['evidence']}
    if arm=='BASE':
        base.update(cap=12000,original_source_reference=dict(path=str(source),sha256=sha(source)),note='Reference baseline only; no extra query. Same 12k upper bound, actual original<=6k.')
        write_new(dest,base);return base
    tool=ContextCollector(case,folder/'native',BUDGETS,initial_state=base);records=[];unattempted=[];inv=[];errors=[]
    started=time.monotonic()
    try:
        if arm=='BROAD':
            report=acquire(tool,[],phase='before_final',include_changed=True,claims=[]);records=report['records'];write_new(folder/'controller_frontier.json',report)
        else:
            inv,errors=inventory(tool);requests=queue(inv,arm)
            for index,row in enumerate(requests):
                cost=tool.stats
                if cost['native_calls']>=46 or cost['file_reads']>=248 or source_tokens(tool.evidence)>=11936 or time.monotonic()-started>=880:
                    unattempted=requests[index:];break
                answer=tool.query(row['question']);records.append(dict(**row,answer=answer))
        tool.query_started=time.monotonic()
    finally:tool.close()
    context=tool.export();assert prefix<={e['id']for e in context['evidence']},'Initial evidence must not be filtered or evicted'
    context.update(cap=12000,source_tokens=source_tokens(context['evidence']),original_source_reference=dict(path=str(source),sha256=sha(source)),protected_initial_ids=sorted(prefix),protected_initial_ids_retained=True,seed_inventory=inv,seed_inventory_errors=errors,request_records=records,deferred_requests=unattempted,priority_is_not_a_filter=True,seconds=time.monotonic()-started)
    write_new(dest,context);print(json.dumps(dict(case=case['id'],arm=arm,queries=len(records),deferred=len(unattempted),protected=True,source=context['source_tokens'],native=context['cost']['native_calls'])),flush=True);return context

def one(case):
    for arm in ARMS:one_arm(case,arm)
    return dict(case_id=case['id'],complete=True)
