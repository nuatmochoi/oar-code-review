"""Sparse physical DIFF handles. Full PR text appears once in reviewer stdin."""
import re
from common import digest,source_lines

def diff_evidence(diff):
 sections=[];current=[]
 for row in source_lines(diff):
  if row.startswith('diff --git ')and current:sections.append('\n'.join(current));current=[]
  current.append(row)
 if current:sections.append('\n'.join(current))
 out=[]
 for section in sections:
  paths={};old=new=None;a=b=0
  def add(path,s,n,text):
   if path is not None and n>0:paths.setdefault((path,s),{})[n]=text
  for t in source_lines(section):
   if t.startswith('--- '):
    old=t[4:].split('\t')[0].removeprefix('a/');old=None if old=='/dev/null'else old
   elif t.startswith('+++ '):
    new=t[4:].split('\t')[0].removeprefix('b/');new=None if new=='/dev/null'else new
   elif t.startswith('@@'):
    m=re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)',t)
    if m:a,b=map(int,m.groups())
   elif t.startswith(' ')and a and b:add(old,'BASE',a,t[1:]);add(new,'HEAD',b,t[1:]);a+=1;b+=1
   elif t.startswith('-')and not t.startswith('---'):add(old,'BASE',a,t[1:]);a+=1
   elif t.startswith('+')and not t.startswith('+++'):add(new,'HEAD',b,t[1:]);b+=1
  for(path,s),rows in sorted(paths.items()):
   v=dict(snapshot=s,path=path,start_line=min(rows),end_line=max(rows),kind='diff_hunk',
    content=section,physical_rows={str(n):t for n,t in rows.items()},
    provenance='public_diff',mechanical_checks={'public_diff_rows':True,'semantic_truth_guaranteed':False},binding={})
   v['id']='D-'+digest([s,path,section])[:16];out.append(v)
 return out

def query_summary(result):
 """Keep actual query meaning/result IDs; omit source copies and raw RPC blobs."""
 return dict(question=result.get('question'),evidence_ids=[e['id']for e in result.get('evidence',[])],
  unresolved=result.get('unresolved',[]),cost=result.get('cost',{}),source_tokens_after=result.get('source_tokens_after'),
  requested_callee_substituted_by_caller=result.get('requested_callee_substituted_by_caller',False))
