"""Before any reviewer call, declare a shared request path and execute source."""
import ast,copy,asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import digest
read=lambda p:json.loads(p.read_text())
def save(p,x):
 with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2)
assert not list((R/'raw').glob('*.json'))
case=read(ROOT/'runs/context_fresh6_20260929/experiment_v1/cases/F08.json');labels=read(R/'labels.json');labels['P08']=labels['F08'];witnesses=[]
class Web:
 @staticmethod
 def Response(status,headers):return SimpleNamespace(status=status,headers=headers)
for world,spec in case['worlds'].items():
 for version,sources in spec['versions'].items():
  for repeat in range(2):
   env={'web':Web};nodes=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)];tree=ast.parse(sources['middleware/cache_middleware.py'])
   for n in tree.body:
    if isinstance(n,(ast.Assign,ast.AnnAssign)):nodes.append(copy.deepcopy(n))
    if isinstance(n,ast.AsyncFunctionDef) and n.name=='cache_control':
     n=copy.deepcopy(n);n.decorator_list=[];nodes.append(n)
   dep=ast.parse(sources['deployment.py']);nodes.extend(copy.deepcopy(n) for n in dep.body if isinstance(n,ast.AsyncFunctionDef))
   m=ast.Module(body=nodes,type_ignores=[]);ast.fix_missing_locations(m);exec(compile(m,'controlled_source','exec'),env)
   response=asyncio.run(env['poll_image'](SimpleNamespace(path='/generated.png')));actual=response.headers['Cache-Control'];expected='no-cache';fail=actual!=expected;assert fail==(labels['F08']['worlds'][world]=='unsafe' and version=='head')
   witnesses.append({'world':world,'revision':version,'repeat':repeat,'request_path':'/generated.png','actual':actual,'expected':expected,'target_failure':fail,'note':'Original middleware and producer async bodies; local response boundary; no browser/network.'})
 forarm=read(R/'packets'/f'F08_{world}.json');forarm['case_id']='P08';forarm['stage']='controlled_path';forarm['common']=forarm['common'].replace('\n[DIFF]',"\nDeclared fixed request input for this controlled test: request.path='/generated.png' in both base and head.\n[DIFF]");forarm['source_case']='F08';save(R/'packets'/f'P08_{world}.json',forarm)
(R/'labels.json').write_text(json.dumps(labels,indent=2));save(R/'controlled_witnesses.json',witnesses)
p=R/'PROTOCOL_KO.md';p.write_text(p.read_text()+'\n모델 호출 전 보조 P08 한 쌍을 추가한다. F08과 같은 source/evidence를 쓰되 양쪽 arm 모두 request.path=/generated.png를 공개 scope 입력으로 명시한다. 원래 F08을 변경하지 않고 별도 결과를 보고한다. 원본 middleware·producer AST 본문을 base/head × world ×2회 실행해 고정 요청 경로에서 관측값을 확인했다. witness/labels는 모델 입력에 넣지 않는다. 총60회 호출이며 독립 새 holdout이 아닌 정보 완성 진단이다.\n')
p=R/'freeze.json';f=read(p)
for q in [R/'PROTOCOL_KO.md',R/'labels.json',R/'add_controlled.py',R/'controlled_witnesses.json',R/'packets/P08_a.json',R/'packets/P08_b.json']:f['files'][str(q.relative_to(ROOT))]=digest(q.read_text())
f['planned_calls']=60;f['controlled_diagnostic']='P08: same F08 sources, shared explicitly declared .png request path; prepared before reviewer calls.';p.write_text(json.dumps(f,indent=2));print('Prepared P08; witnessed 8 executions; planned calls60.')
