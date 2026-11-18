"""Count submitted text using pinned official Flash-1017 assets, not provider hidden tokens.
No internet, no model weights, no usage/credentials. External spend safety is separate.
"""
import json,hashlib
from pathlib import Path
from functools import lru_cache
PIN='08c8657f02291e11b6f6b63e0e16014f8e748518'
HASHES={'encoding.py':'13231c592e958fb39e2af6001dae69d90e7a36b265702b9b390236fb387599e2','tokenizer.json':'c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b'}
@lru_cache(maxsize=1)
def _load():
    from tokenizers import Tokenizer,__version__
    if __version__!='0.22.1':raise ValueError('unvalidated tokenizer engine')
    root=Path(__file__).resolve().parent/'official1017'
    blobs={k:(root/k).read_bytes() for k in HASHES}
    if any(hashlib.sha256(blobs[k]).hexdigest()!=v for k,v in HASHES.items()):raise ValueError('official asset digest mismatch')
    ns={};exec(compile(blobs['encoding.py'],'official1017/encoding.py','exec'),ns)
    data=json.loads(blobs['tokenizer.json'])
    tok=Tokenizer.from_str(blobs['tokenizer.json'].decode())
    return ns['encode_messages'],tok,[x['content'] for x in data['added_tokens']]+['<image>','</image>','｜DSML｜']
def submitted_count(wire):
    b=json.loads(wire);ms=b.get('messages',[])
    if set(b)!={'model','messages','max_tokens','stream','thinking','response_format'} or b['model']!='deepseek-flash' or b['thinking']!={'type':'disabled'} or b['response_format']!={'type':'json_object'} or b['stream'] is not False or type(b['max_tokens']) is not int or not 1<=b['max_tokens']<=512:raise ValueError('unsupported input')
    if not isinstance(ms,list) or [m.get('role') for m in ms] != ['system','user'] or not all(set(m)=={'role','content'} and isinstance(m['content'],str) and m['content'] for m in ms):raise ValueError('exactly two text messages only')
    render,tok,reserved=_load()
    if any(s in m['content'] for s in reserved for m in ms):raise ValueError('reserved special token in source')
    # Public reference describes per-system response format. Include its schema
    # explicitly, do not silently count only the two visible content strings.
    msgs=[dict(ms[0],response_format=b['response_format']),dict(ms[1])]
    rendered=render(msgs,thinking_mode='chat')
    count=len(tok.encode(rendered,add_special_tokens=False).ids)
    if not 1<=count<=16384:raise ValueError('submitted prompt exceeds 16384')
    return count
