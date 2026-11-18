from pathlib import Path
import json,urllib.request,tarfile,zipfile,io,hashlib
out=Path('/opt/language-servers');out.mkdir(exist_ok=True)
urls={
 'kotlin':'https://github.com/fwcd/kotlin-language-server/releases/download/1.3.13/server.zip',
 'jdtls':'https://download.eclipse.org/jdtls/milestones/1.50.0/jdt-language-server-1.50.0-202509041425.tar.gz'
}
receipts={}
for name,url in urls.items():
 try:
  raw=urllib.request.urlopen(url,timeout=120).read();dest=out/name;dest.mkdir(exist_ok=True)
  if url.endswith('.zip'):zipfile.ZipFile(io.BytesIO(raw)).extractall(dest)
  else:tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz').extractall(dest,filter='data')
  receipts[name]={'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'installed':True}
  if name=='kotlin':
   path=next(dest.rglob('kotlin-language-server'));path.chmod(0o755);Path('/usr/local/bin/kotlin-language-server').symlink_to(path)
  else:
   path=next(dest.rglob('jdtls'));path.chmod(0o755);Path('/usr/local/bin/jdtls').symlink_to(path)
 except Exception as e:receipts[name]={'url':url,'installed':False,'error':type(e).__name__+': '+str(e)}
(out/'install-receipts.json').write_text(json.dumps(receipts,indent=2))
