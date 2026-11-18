"""Lossless JSON history interning; no source/candidate ranking or truncation.

Aliases record exactly which paths are placeholders. Literal look-alike data
without an alias entry remains data, including adversarial repository strings.
"""
import copy,json,hashlib
def encoded(v):return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def join(path,key):return path+'/'+str(key).replace('~','~0').replace('/','~1')
def pack(value,min_chars=500):
    seen={};aliases={};savings=0
    def visit(v,path):
        nonlocal savings
        text=encoded(v);eligible=isinstance(v,(dict,list,str))and len(text)>=min_chars
        key=hashlib.sha256(text.encode()).hexdigest()
        if eligible and key in seen:
            target=seen[key];aliases[path]=target;replacement={'same_value_as':target};savings+=len(text)-len(encoded(replacement));return replacement
        if eligible:seen[key]=path
        if isinstance(v,dict):return{k:visit(x,join(path,k))for k,x in v.items()}
        if isinstance(v,list):return[visit(x,join(path,i))for i,x in enumerate(v)]
        return v
    out=dict(format='lossless-json-path-aliases-v1',value=visit(value,''),aliases=aliases,scope='Only repeated identical values use references. Every original value, ordering, source/candidate/condition/challenge/reason is recoverable. No semantic filtering.')
    assert encoded(unpack(out))==encoded(value)
    return out
def unpack(packet):
    raw=packet['value'];aliases=packet['aliases'];memo={}
    def get(path):
        v=raw
        if path:
            for key in path.split('/')[1:]:
                key=key.replace('~1','/').replace('~0','~');v=v[int(key)]if isinstance(v,list)else v[key]
        return v
    def expand(v,path,active):
        if path in memo:return copy.deepcopy(memo[path])
        assert path not in active,'History reference cycle'
        active=active|{path}
        if path in aliases:
            target=aliases[path];assert v=={'same_value_as':target};out=expand(get(target),target,active)
        elif isinstance(v,dict):out={k:expand(x,join(path,k),active)for k,x in v.items()}
        elif isinstance(v,list):out=[expand(x,join(path,i),active)for i,x in enumerate(v)]
        else:out=v
        memo[path]=copy.deepcopy(out);return out
    return expand(raw,'',set())
