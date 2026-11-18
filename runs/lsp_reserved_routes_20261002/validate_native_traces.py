import json
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
checks=[]
for path in sorted((R/'lsp_traces').glob('*.json')):
 stem,arm=path.stem.rsplit('_',1);p=read(R/'packets'/f'{stem}.json');queries=[q for q in read(path)['trace'] if q.get('method') in ['textDocument/definition','textDocument/references']]
 checks.append({'stem':path.stem,'actual_semantic_queries':len(queries),'logged_jumps':len(p[arm+'_audit']['jumps']),'valid':len(queries)==len(p[arm+'_audit']['jumps'])<=12,'methods':[q['method'] for q in queries]})
result={'rows':checks,'actual_comparison_queries':sum(x['actual_semantic_queries'] for x in checks),'all_counts_match':len(checks)==8 and all(x['valid'] for x in checks)}
(R/'native_trace_validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='rows'}));assert result['all_counts_match']
