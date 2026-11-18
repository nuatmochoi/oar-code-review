"""Native synthetic mechanism check, no model, gold, or benchmark labels."""
import concurrent.futures,dataclasses,hashlib,json,os,sys,time,traceback
from pathlib import Path
OUT=Path(os.environ.get('REVIEW_RUN_ROOT','/work/out/agent')).resolve();OUT.mkdir(parents=True,exist_ok=True)
os.environ['REVIEW_RUN_ROOT']=str(OUT)
from multilingual import ContextCollector
from common import write_new
SAMPLES={
 'TypeScript':('a.ts','function helper(x: number) { return x + 1; }\nexport function use() { return helper(2); }\n'),
 'JavaScript':('a.js','function helper(x) { return x + 1; }\nfunction use() { return helper(2); }\n'),
 'CSharp':('A.cs','class A {\n static int Helper(int x) { return x + 1; }\n static int Use() { return Helper(2); }\n}\n'),
 'Java':('A.java','class A {\n static int helper(int x) { return x + 1; }\n static int use() { return helper(2); }\n}\n'),
 'Kotlin':('a.kt','fun helper(x: Int): Int { return x + 1 }\nfun use(): Int { return helper(2) }\n'),
 'Rust':('a.rs','fn helper(x: i32) -> i32 { x + 1 }\nfn use_it() -> i32 { helper(2) }\n'),
 'Swift':('a.swift','func helper(_ x: Int) -> Int { return x + 1 }\nfunc useIt() -> Int { return helper(2) }\n'),
 'PHP':('a.php','<?php\nfunction helper($x) { return $x + 1; }\nfunction use_it() { return helper(2); }\n'),
 'Ruby':('a.rb','def helper(x)\n x + 1\nend\ndef use_it\n helper(2)\nend\n'),
 'Shell':('a.sh','helper() { echo "$1"; }\nuse_it() { helper 2; }\n'),
 'Python':('a.py','def helper(x):\n return x + 1\ndef use_it():\n return helper(2)\n'),
 'Go':('a.go','package toy\nfunc helper(x int) int { return x + 1 }\nfunc useIt() int { return helper(2) }\n'),
}
def one(lang):
 start=time.monotonic();name,source=SAMPLES[lang];dest=OUT/lang;repo=dest/'repo';repo.mkdir(parents=True,exist_ok=True);(repo/name).write_text(source)
 if lang=='Go':(repo/'go.mod').write_text('module example.com/toy\n\ngo 1.25.0\n')
 if lang=='Rust':(repo/'Cargo.toml').write_text('[package]\nname="toy"\nversion="0.1.0"\nedition="2021"\n[lib]\npath="a.rs"\n')
 if lang=='CSharp':(repo/'A.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>')
 if lang=='Swift':(repo/'Package.swift').write_text('// swift-tools-version: 6.0\nimport PackageDescription\nlet package = Package(name: "toy", targets: [.target(name: "toy",path: ".",sources: ["a.swift"])])\n')
 if lang in('TypeScript','JavaScript'):(repo/'tsconfig.json').write_text('{"compilerOptions":{"allowJs":true},"include":["*.ts","*.js"]}')
 from entrypoint import snapshot
 # snapshot() consumes a tracked git tree; for native fixtures construct the
 # equivalent declared analysis profile without invoking benchmark repositories.
 files={str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest()for p in repo.iterdir()if p.is_file()};profile={'native_env':{},'allowed_external_roots':[]}
 if lang=='Python':profile['python_executable']=sys.executable
 if lang=='Go':
  import subprocess
  env=json.loads(subprocess.check_output(['go','env','-json','GOROOT','GOVERSION','GOOS','GOARCH','CGO_ENABLED']));profile['pinned_toolchains']={'.':{**env,'go_binary':str(Path(env['GOROOT'])/'bin/go')}}
 spec=dict(root=str(repo),commit='synthetic_fixture',files=files,native_profiles={lang:profile})
 case=dict(id=lang,language=lang,snapshots={'BASE':spec,'HEAD':spec},diff='');tool=ContextCollector(case,dest/'context')
 row=dict(language=lang)
 try:
  parsed=tool._parse('HEAD',name);calls=[c for c in parsed['calls']if c['name'].lower()=='helper'];assert calls,'Missing actual AST call token';call=calls[-1]
  q=dict(snapshot='HEAD',file=name,line=call['line'],column=call['column'],symbol=call['name'],kind='implementation_body',query='definition',reason='Synthetic native lookup test',id='Q-toy',claim_id='exploration')
  response=tool.query(q);row.update(receipt=response,context=tool.export(),native_definition_completed=any(e.get('kind')=='implementation_body'and e['start_line']<=2 for e in response.get('evidence',[])))
 except Exception as exc:row.update(error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc())
 finally:
  for key,client in tool.clients.items():
   row.setdefault('server_state',[]).append(dict(language=key[-1],capabilities=client.server_capabilities,events=[dataclasses.asdict(e)for e in client.events],stderr=client.stderr_lines))
  try:tool.close()
  except Exception as exc:row['close_error']=str(exc)
 row['seconds']=time.monotonic()-start;write_new(dest/'result.json',row)
 print(json.dumps(dict(language=lang,error=row.get('error'),source_tokens=(row.get('context')or{}).get('source_tokens'),seconds=row['seconds'])),flush=True);return row
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=2)as p:rows=list(p.map(one,SAMPLES))
 write_new(OUT/'results.json',rows)
