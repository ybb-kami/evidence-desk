"""Two step, one-use, seat-bound consent. Offline unless explicitly supplied a budgeted transport."""
import hashlib,json,re,secrets,threading,time
import synthesis

class SynthesisBridge:
    def __init__(self,gate=None,transport=None,*,clock=time.monotonic):
        if (gate is None)!=(transport is None):raise ValueError('Incomplete runtime configuration')
        self.gate,self.transport,self.clock=gate,transport,clock
        self.pending={};self.lock=threading.Lock()
    @staticmethod
    def _anchor_digest(item):
        return hashlib.sha256(json.dumps(item["sources"],sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    def _prepare(self,query,ids,backend):
        if not isinstance(query,str) or not 1<=len(query.strip())<=500 or not isinstance(ids,list) or not 2<=len(ids)<=6 or not all(isinstance(i,str) and re.fullmatch(r'[a-f0-9]{64}',i) for i in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid scope')
        if not set(ids)<=set(d['id'] for d in backend('/api/library')['docs']):raise ValueError('Inactive or absent selections')
        wf=backend('/api/workflow',{'query':query,'ids':ids})
        readers={i:backend('/api/reader/'+i) for i in ids}
        return synthesis.prepare(query,ids,synthesis.annotate(wf,readers))
    def handle(self,action,seat,req,backend):
        if not isinstance(req,dict):return 400,{'error':'invalid request'}
        if action=='preview' and set(req)=={'query','ids'}:
            item=self._prepare(req['query'],req['ids'],backend)
            if item.get('status')!='ready':return 200,{'status':'insufficient_evidence','online':False}
            token=secrets.token_urlsafe(32);digest=hashlib.sha256(item['wire']).hexdigest()
            with self.lock:
                now=self.clock();self.pending={k:v for k,v in self.pending.items() if v[0]>now}
                if len(self.pending)>=12 and seat not in self.pending:return 429,{'error':'preview capacity reached'}
                self.pending[seat]=(now+180,token,req['query'],req['ids'][:],digest,self._anchor_digest(item))
            return 200,{'status':'ready','wire_text':item['wire'].decode(),'sources':item['sources'],'wire_sha256':digest,'token':token,'online':self.gate is not None}
        if action!='confirm' or set(req)!={'token','consent'} or req['consent'] is not True or not isinstance(req['token'],str):return 400,{'error':'explicit consent required'}
        if self.gate is None:return 503,{'error':'paid draft generation is disabled'}
        with self.lock:
            row=self.pending.get(seat)
            if not row or row[0]<=self.clock() or not secrets.compare_digest(row[1].encode(),req['token'].encode()):return 409,{'error':'expired or invalid preview'}
            del self.pending[seat]
        now,token,query,ids,expected,anchor_expected=row
        item=self._prepare(query,ids,backend)
        if item.get('status')!='ready' or hashlib.sha256(item['wire']).hexdigest()!=expected or self._anchor_digest(item)!=anchor_expected:return 409,{'error':'source changed, preview again'}
        def send(wire):
            if wire!=item['wire']:raise RuntimeError('request changed')
            rid=hashlib.sha256(token.encode()).hexdigest()
            self.gate.reserve(request_id=rid,session_id=seat,request_body=wire)
            return self.transport(wire)
        try:return 200,synthesis.generate(item,send=send)
        except Exception:return 503,{"error":"generation unavailable; no automatic retry"}
