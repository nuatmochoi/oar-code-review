import json
from pathlib import Path
from lsp_client import Client
R=Path(__file__).resolve().parent;root=R/'smoke_workspace';root.mkdir(exist_ok=True)
src='class Service:\n    def execute(self):\n        return 1\n\ndef entry(service: Service):\n    return service.execute()\n'
(root/'sample.py').write_text(src)
c=Client(root)
try:
 result=c.definition('sample.py',5,19);assert result,result
 (R/'smoke_trace.json').write_text(json.dumps({'capabilities':c.initialization['capabilities'],'definition':result,'trace':c.trace,'stderr':c.errors},indent=2)+'\n')
 print(json.dumps({'actual_lsp_definition':result,'startup_seconds':c.startup_seconds}))
finally:c.close()
