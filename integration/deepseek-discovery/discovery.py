"""Request builder for independent DeepSeek discovery; never reads PDF/workflow data."""
import re
from urllib.parse import urlsplit

BASE = 'https://api.deepseek.com/v1/tools/'
def academic_url(raw):
    if not isinstance(raw,str) or not re.fullmatch(r'https://[A-Za-z0-9./_-]{1,380}',raw): return False
    u=urlsplit(raw)
    return bool((u.netloc=='arxiv.org' and re.fullmatch(r'/abs/[0-9]{4}\.[0-9]{4,5}(v[1-9][0-9]*)?',u.path)) or
            (u.netloc=='aclanthology.org' and re.fullmatch(r'/[A-Za-z0-9][A-Za-z0-9.-]{2,90}/',u.path)))

def run(action, value, *, confirmed, allowed_results=(), reserve, send):
    if confirmed is not True: raise ValueError('explicit consent required')
    if action=='search':
        if not isinstance(value,str) or not 1<=len(value)<=128 or not value.strip() or not value.isprintable(): raise ValueError('bad public query')
        body={'query':value,'count':5,'need_content':False}
    elif action=='fetch' and value in allowed_results and academic_url(value):
        body={'url':value}
    else: raise ValueError('not a selected supported result')
    reserve(750) # micro-USD; caller supplies persistent atomic per-seat and global reservation
    result=send(BASE+action,body) # no retries
    if action=='search':
        return [{'url':x['url'],'title':str(x.get('title',''))[:240],'kind':'discovery_lead'}
                for x in result['results'][:5] if isinstance(x,dict) and academic_url(x.get('url'))]
    content=result.get('content')
    return {'kind':'web_text_not_pdf_evidence','url':value,'content':content[:8000] if isinstance(content,str) else ''}
