"""Project-scoped OpenAI credentials; never include secrets in diagnostics."""
from pathlib import Path
import json,os,stat,tempfile

DEFAULT_PATH=Path.home()/'.config/paper-research/openai.json'

def save_credentials(path,key,project=None,organization=None,*,replace=False):
    path=Path(path).expanduser()
    if not isinstance(key,str) or len(key)<20 or any(c.isspace()for c in key):
        raise ValueError('API 키가 비어 있거나 공백이 포함되어 있습니다.')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.is_symlink():raise ValueError('심볼릭 링크에는 키를 저장하지 않습니다.')
    if path.exists() and not replace:raise FileExistsError('이미 설정 파일이 있습니다. 교체하려면 --replace를 사용하세요.')
    data={'api_key':key}
    if project:data['project']=project
    if organization:data['organization']=organization
    fd,name=tempfile.mkstemp(prefix='.openai-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'w',encoding='utf-8')as f:
            json.dump(data,f,ensure_ascii=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.chmod(temporary,0o600)
        if replace:os.replace(temporary,path)
        else:os.link(temporary,path)
    finally:temporary.unlink(missing_ok=True)

def load_credentials(path=None,*,environ=None):
    env=os.environ if environ is None else environ
    if env.get('OPENAI_API_KEY'):
        return dict(api_key=env['OPENAI_API_KEY'],project=env.get('OPENAI_PROJECT_ID'),organization=env.get('OPENAI_ORG_ID'),source='environment')
    path=Path(path or env.get('PAPER_RESEARCH_OPENAI_CONFIG')or DEFAULT_PATH).expanduser()
    if path.is_symlink():raise ValueError('키 설정 파일은 일반 파일이어야 합니다.')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:
        raise ValueError('키 설정 파일 권한을 600으로 설정하세요.')
    if info.st_uid!=os.getuid():raise ValueError('현재 사용자가 소유한 키 설정 파일이 필요합니다.')
    try:data=json.loads(path.read_text())
    except (ValueError,UnicodeError):raise ValueError('키 설정 파일의 JSON 형식을 확인하세요.')from None
    if not isinstance(data,dict)or not isinstance(data.get('api_key'),str)or not data['api_key']:
        raise ValueError('키 설정 파일에 api_key가 필요합니다.')
    return dict(api_key=data['api_key'],project=data.get('project'),organization=data.get('organization'),source=str(path))

def headers(credentials):
    result={'Authorization':'Bearer '+credentials['api_key'],'Content-Type':'application/json'}
    if credentials.get('project'):result['OpenAI-Project']=credentials['project']
    if credentials.get('organization'):result['OpenAI-Organization']=credentials['organization']
    return result
