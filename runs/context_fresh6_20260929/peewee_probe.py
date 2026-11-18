"""Confirm the query boundary with the repository-pinned Peewee and in-memory SQLite."""
import ast,json,sys
from pathlib import Path
from types import SimpleNamespace
import peewee
R=Path(sys.argv[1])
db=peewee.SqliteDatabase(':memory:')
class Document(peewee.Model):
 id=peewee.CharField(primary_key=True)
 name=peewee.CharField()
 class Meta:database=db
db.create_tables([Document]);Document.create(id='doc-1',name='existing')
records=[]
for world in ('safe','unsafe'):
 p=R/'pairs/F03/snapshots'/f'{world}_head/api/db/services/document_service.py'
 nodes=[n for n in ast.walk(ast.parse(p.read_text())) if isinstance(n,ast.FunctionDef) and n.name=='get_doc_ids_by_doc_names'];n=nodes[0];n.decorator_list=[]
 env={};exec(compile(ast.Module(body=[n],type_ignores=[]),str(p),'exec'),env)
 fn=env[n.name]
 for names in ([],['missing'],['existing']):
  result=fn(SimpleNamespace(model=Document),names)
  expected=['doc-1'] if names==['existing'] else ([] if world=='safe' else None)
  assert result==expected,(world,names,result)
  records.append({'world':world,'names':names,'result':result,'valid':True})
print(json.dumps({'peewee_version':peewee.__version__,'sqlite_version':__import__('sqlite3').sqlite_version,'valid':True,'records':records,'boundary':'actual source helper with Peewee 3.17.1 and real SQLite :memory:; not application DB integration'}))
