"""Optional paid broker. Never constructed or enabled by the default launcher."""
import json,secrets
class ModelBridge:
    def __init__(self, provider, gate, transport):
        self.provider,self.gate,self.transport=provider,gate,transport
    def verify(self,seat,raw,library,search):
        """library/search are bound by the gateway to this authenticated seat."""
        req=json.loads(raw)
        ids=req.get('ids') if isinstance(req,dict) else None
        if (not isinstance(ids,list) or not 1<=len(ids)<=8 or not all(isinstance(i,str) for i in ids)
            or len(set(ids))!=len(ids) or set(req)!={'query','consent','ids'} or req['consent'] is not True
            or not isinstance(req['query'],str) or not 1<=len(req['query'].strip())<=500):
            return 400,{'error':'请选择文献并确认发送所选摘录和问题。'}
        active={d['id'] for d in library()['docs']}
        if not set(ids)<=active:return 404,{'error':'本席位中找不到所选文献。'}
        hits=search({'ids':ids,'query':req['query']})['hits']
        if any(h.get('id') not in ids for h in hits):return 502,{'error':'文献范围不符。'}
        def send(body,key):
            msg=json.loads(body);msg['max_tokens']=min(msg['max_tokens'],512)
            wire=json.dumps(msg,separators=(',',':')).encode()
            self.gate.reserve(request_id=secrets.token_hex(24),session_id=seat,request_body=wire)
            return self.transport(wire,key) # exactly once; no retries/refunds on an unknown result
        try:return 200,self.provider.summarize(req['query'],hits,transport=send)
        except RuntimeError:return 503,{'error':'本次未能提供在线回答（可能未获额度或上游未完成）。请使用离线查证；不会自动重试。'}
