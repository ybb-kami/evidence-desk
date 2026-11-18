"""Opt-in transport factory, no reads of environment, no redirects or retries."""
import http.client,json
from collections.abc import Mapping

def build_transport(key):
    if not isinstance(key,str) or not key or not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
        raise ValueError('invalid credential')
    def send(endpoint,body):
        base='https://api.deepseek.com/v1/tools/'
        if endpoint not in (base+'search',base+'fetch'): raise ValueError('unsupported endpoint')
        if not isinstance(body,Mapping): raise ValueError('invalid request')
        con=http.client.HTTPSConnection('api.deepseek.com',443,timeout=12)
        try:
            con.request('POST','/v1/tools/'+endpoint.rsplit('/',1)[1],json.dumps(body).encode(),{'Authorization':'Bearer '+key,'Content-Type':'application/json'})
            r=con.getresponse()
            if r.status!=200:raise RuntimeError('upstream returned HTTP '+str(r.status))
            limit=128000; raw=r.read(limit+1)
            if len(raw)>limit:raise RuntimeError('response too large')
            result=json.loads(raw)
            if not isinstance(result,dict):raise ValueError('bad result')
            return result
        finally:con.close()
    return send
