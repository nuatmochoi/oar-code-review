"""Prepare public-only packets and a private post-hoc diagnostic; no API calls."""
from pathlib import Path
import collections,hashlib,json,os,re,shutil,subprocess,sys
ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'reviewbench_official25_lsp_20261105'
GUARD=ROOT.parent/'reviewbench_official25_guarded12k_20261105'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:json.dump(v,f,ensure_ascii=False,indent=2)
def git(repo,*args):return subprocess.check_output(['git','-C',str(repo),*args],env={**os.environ,'GIT_NO_LAZY_FETCH':'1'},stderr=subprocess.PIPE).decode()
def hunks(diff):
 out=[];path=None;oldpath=None;head=base=0;current=None
 for row in diff.splitlines():
  if row.startswith('--- '):oldpath=row[4:].removeprefix('a/')
  elif row.startswith('+++ '):path=row[4:].removeprefix('b/');path=oldpath if path=='/dev/null'else path
  elif row.startswith('@@ '):
   m=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)',row)
   if not m:continue
   base,head=int(m[1]),int(m[3]);current=dict(id=f'U{len(out)+1:04d}',path=path,base_start=base,head_start=head,header=row,changed_head=[],changed_base=[],sample=[]);out.append(current)
  elif current is not None:
   if row.startswith('+'):
    current['changed_head'].append(head);head+=1
    if len(current['sample'])<4:current['sample'].append(row[:220])
   elif row.startswith('-'):
    current['changed_base'].append(base);base+=1
    if len(current['sample'])<4:current['sample'].append(row[:220])
   elif row.startswith(' '):head+=1;base+=1
   current['head_end']=head-1;current['base_end']=base-1
 return out
def main():
 ROOT.mkdir(exist_ok=True);refs={};manifest=json.loads((OLD/'upstream/corpus/test/test.json').read_text());native={r['index']:r for r in json.loads((OLD/'audit/native-coverage.json').read_text())['rows']};packets=[];diagnostics=[]
 for name in ['vendor']:
  shutil.copytree(OLD/'adapter'/name,ROOT/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 shutil.copy2(OLD/'adapter/review_prompt.py',ROOT/'review_prompt.py')
 for i,e in enumerate(manifest):
  folder=Path(native[i]['folder']);case=json.loads((folder/'case.json').read_text());ctx=json.loads((folder/'lsp.context.json').read_text());repo=folder.parents[2]/'repos'/e['nwo'].replace('/','_');units=hunks(case['diff']);commits=[];history_errors=[]
  for commit in git(repo,'rev-list','--reverse',case['snapshots']['BASE']['commit']+'..'+e['head']).splitlines():
   try:
    # Only ancestors of frozen HEAD are read; no remote, other refs, or post-review commits.
    assert subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',commit,e['head']],env={**os.environ,'GIT_NO_LAZY_FETCH':'1'},capture_output=True).returncode==0
    text=git(repo,'show','-s','--format=%H%n%P%n%B',commit);lines=text.splitlines();paths=git(repo,'diff-tree','--no-commit-id','--name-only','-r','--root','-m',commit).splitlines()
    paths=sorted(set(paths)&set(case['changed_files']))
    commits.append(dict(sha=commit,parents=lines[1].split(),message='\n'.join(lines[2:]),changed_final_paths=paths))
   except Exception as exc:history_errors.append(dict(commit=commit,error=str(exc)[:300]))
  # A hunk is a coverage unit, not a proven semantic dependency or runtime path.
  physical={(q['snapshot'],q['path'],row['line'])for q in ctx['evidence']for row in q.get('physical_rows',[])}
  qs=[]
  for p in sorted((folder/'context').glob('query-*.json')):
   q=json.loads(p.read_text()).get('question',{});qs.append(q);refs[str(p)]=sha(p)
  coverage=[]
  for u in units:
   local=[v for v in ctx['evidence']if v.get('path')==u['path']and v.get('snapshot')=='HEAD'and v.get('mechanical_checks',{}).get('complete_unit')and any(v['start_line']<=n<=v['end_line']for n in u['changed_head'])]
   seeds=[q for q in qs if q.get('file')==u['path']and q.get('snapshot')=='HEAD'and u['head_start']<=q.get('line',0)<=u.get('head_end',u['head_start'])]
   coverage.append(dict(unit_id=u['id'],path=u['path'],full_diff_delivered=True,head_complete_unit_evidence_ids=[v['id']for v in local],native_question_ids=[q.get('id')for q in seeds]))
  public=dict(index=i,key=native[i]['key'],pr=e,case=case,prefix=ctx,units=units,commits=commits,history_errors=history_errors,original_repo=str(repo),coverage=coverage)
  path=ROOT/'public'/f'{i:02d}.json';save(path,public);packets.append(dict(index=i,path=str(path),sha256=sha(path)))
  diagnostics.append(dict(index=i,nwo=e['nwo'],pr=e['pr_number'],units=len(units),changed_files=len(case['changed_files']),commits=len(commits),units_with_complete_head_body=sum(bool(c['head_complete_unit_evidence_ids'])for c in coverage),units_with_native_question=sum(bool(c['native_question_ids'])for c in coverage),full_diff_delivered_units=len(units),history_errors=history_errors))
  for p in [folder/'case.json',folder/'lsp.context.json',folder/'review.request.json',folder/'review.response.json',folder/'review.decoded.json']:refs[str(p)]=sha(p)
 for p in [OLD/'freeze.json',OLD/'completion.json',OLD/'scoring/results.json',OLD/'scoring/results.details.json',GUARD/'freeze.json',GUARD/'completion.json',GUARD/'scoring/results.json',GUARD/'scoring/results.details.json']:refs[str(p)]=sha(p)
 shutil.copytree(OLD/'upstream',ROOT/'upstream',ignore=shutil.ignore_patterns('.git','node_modules'))
 (ROOT/'upstream/node_modules').symlink_to(OLD/'upstream/node_modules')
 # Judge inputs and native worktrees are cloned, never shared writable with old runs.
 subprocess.run(['cp','-cR',str(OLD/'judge-repos'),str(ROOT/'judge-repos')],check=True)
 for i,e in enumerate(manifest):
  src=Path(native[i]['folder']).parents[2]/'repos'/e['nwo'].replace('/','_');dest=ROOT/'native-repos'/e['nwo'].replace('/','_');dest.parent.mkdir(exist_ok=True)
  subprocess.run(['cp','-cR',str(src),str(dest)],check=True)
 save(ROOT/'public/manifest.json',packets);save(ROOT/'audit/preservation.json',dict(files=refs,old_results_read_only=True))
 save(ROOT/'audit/change_unit_diagnostic.json',dict(unit_definition='unified-diff hunk; not a semantic task or proven execution edge',model_attention_observable=False,rows=diagnostics,units=sum(r['units']for r in diagnostics),complete_head_body_units=sum(r['units_with_complete_head_body']for r in diagnostics),native_question_units=sum(r['units_with_native_question']for r in diagnostics),multi_commit_PRs=sum(r['commits']>1 for r in diagnostics),paid_calls=0))
 cfg=json.loads((OLD/'judge-config/models.json').read_text());cfg['providers']['openai']['baseUrl']='http://127.0.0.1:8793/judge/v1';save(ROOT/'judge-config/models.json',cfg)
 print(json.dumps(dict(prepared=len(packets),units=sum(r['units']for r in diagnostics),multi_commit_PRs=sum(r['commits']>1 for r in diagnostics),old_files_preserved=len(refs))))
if __name__=='__main__':main()
