"""Two step, one-use, seat-bound consent. Offline unless explicitly supplied a budgeted transport."""
import hashlib,json,re,secrets,threading,time
if __package__:
    from . import synthesis
else:
    import synthesis

_HARDWARE_LOOKUP='training hardware'
_TRAINING_PURPOSE=r'(?:for|during) (?:model )?training|to train (?:the|their) models?'
_HARDWARE_QUESTION=re.compile(
    r'(?:what|which) (?:(?:kind|type) of )?(?:hardware|comput(?:e|ing) (?:equipment|setup)) (?:'
    r'(?:was|were) (?:used|employed) (?:'+_TRAINING_PURPOSE+r')|'
    r'did (?:they|the (?:authors|researchers|paper|study)) (?:use|employ) (?:'+_TRAINING_PURPOSE+r')|'
    r'did (?:they|the (?:authors|researchers)) train on)|'
    r'(?:这篇|该篇)?(?:论文|研究|作者)?(?:在)?训练(?:模型)?(?:时|期间)?(?:用|使用|采用)(?:了)?(?:什么|哪种)(?:硬件|计算设备)',re.I)
_DEVICE=re.compile(r'\b(?:GPUs?|TPUs?|CPUs?|accelerators?|graphics processing units?)\b|显卡|加速器',re.I)
_AFFIRMATIVE_TRAINING=re.compile(
    r'\b(?:we trained|models? (?:was|were|is|are) trained|training (?:used|ran|was (?:run|conducted))|'
    r'we used .{0,100} (?:for training|to train))\b|(?:我们|本文)(?:使用|用|采用).{0,65}训练',re.I)
_AMBIGUOUS_TRAINING=re.compile(
    r'\b(?:no|not|never|neither|without|instead of|rather than|would|could|might|should|proposed|prior work|et al|'
    r'inference|evaluation|testing|fine[- ]tun\w*|benchmarked)\b|并未|而不是|推理|测试|既有研究',re.I)
_RECEIPT_MODEL = re.compile(r'deepseek-(?:flash|v4\.1-flash(?:-1017)?)', re.ASCII | re.I)
_RECEIPT_ID = re.compile(r'[!-~]{1,256}')
_TOKEN_FIELDS = ('prompt_tokens', 'completion_tokens', 'total_tokens')
_MAX_JSON_INTEGER = (1 << 53) - 1


def _training_hardware(text):
    return any(_DEVICE.search(line) and _AFFIRMATIVE_TRAINING.search(line) and not _AMBIGUOUS_TRAINING.search(line)
               for line in re.split(r'(?<=[.!?])\s+(?=[A-Z])|(?<=[。！？])',' '.join(text.split())))


def _generation_receipt(response):
    """Report only recognized metadata from this provider response body."""
    if isinstance(response, bytes) and len(response) <= 32768:
        try:
            response = json.loads(response)
        except (ValueError, RecursionError):
            response = None

    model = usage = request_digest = None
    if (isinstance(response, dict) and isinstance(response.get('choices'), list)
            and 'error' not in response and response.get('offline_fixture') is not True):
        observed_model = response.get('model')
        if isinstance(observed_model, str) and _RECEIPT_MODEL.fullmatch(observed_model):
            model = observed_model
        observed_id = response.get('id')
        if isinstance(observed_id, str) and _RECEIPT_ID.fullmatch(observed_id):
            request_digest = hashlib.sha256(observed_id.encode('ascii')).hexdigest()
        observed_usage = response.get('usage')
        if isinstance(observed_usage, dict):
            prompt, completion, total = (observed_usage.get(key) for key in _TOKEN_FIELDS)
            if (all(type(value) is int and 0 <= value <= _MAX_JSON_INTEGER for value in (prompt, completion, total))
                    and prompt + completion == total):
                usage = dict(zip(_TOKEN_FIELDS, (prompt, completion, total)))

    fields = {'model': model, 'usage': usage, 'provider_request_id_sha256': request_digest}
    present = sum(value is not None for value in fields.values())
    status = 'available' if present == len(fields) else 'partial' if present else 'unavailable'
    return {'status': status, **fields}


class SynthesisBridge:
    def __init__(self,gate=None,transport=None,*,clock=time.monotonic):
        if (gate is None)!=(transport is None):raise ValueError('Incomplete runtime configuration')
        self.gate,self.transport,self.clock=gate,transport,clock
        self.pending={};self.lock=threading.Lock();self.discovery=None
        self.provider_mode=None
    @classmethod
    def grouped(cls,discovery):
        if discovery.caller is None:raise ValueError("missing approved dispatcher")
        o=cls();o.discovery=discovery;return o
    @staticmethod
    def _anchor_digest(item):
        return hashlib.sha256(json.dumps(item["sources"],sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    def _prepare(self,query,ids,backend):
        if not isinstance(query,str) or not 1<=len(query.strip())<=500 or not isinstance(ids,list) or not 1<=len(ids)<=6 or not all(isinstance(i,str) and re.fullmatch(r'[a-f0-9]{64}',i) for i in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid scope')
        if not set(ids)<=set(d['id'] for d in backend('/api/library')['docs']):raise ValueError('Inactive or absent selections')
        wf=backend('/api/workflow',{'query':query,'ids':ids})
        readers={i:backend('/api/reader/'+i) for i in ids}
        single=len(ids)==1
        item=synthesis.prepare(query,ids,synthesis.annotate(wf,readers,single=single))
        if (not single or item['status']=='ready' or wf.get('schema')!='evidence-desk-workflow-v1'
                or wf.get('query')!=query.strip() or [p.get('id') for p in wf.get('papers',[])]!=ids
                or wf['papers'][0].get('status')!='no_match'
                or not _HARDWARE_QUESTION.fullmatch(' '.join(query.split()).rstrip('?？.。'))):
            return item
        # Retry just this unqualified single-paper intent. The original question
        # remains in the exact reviewable model request and bound preview token.
        alternate=backend('/api/workflow',{'query':_HARDWARE_LOOKUP,'ids':ids})
        for paper in alternate.get('papers',[]):
            paper['evidence']=[e for e in paper.get('evidence',[]) if _training_hardware(e.get('text',''))]
        return synthesis.prepare(query,ids,synthesis.annotate(alternate,readers,single=True),retrieval_query=_HARDWARE_LOOKUP)
    def handle(self,action,seat,req,backend):
        provider={'provider_mode':self.provider_mode} if self.provider_mode in ('offline_fixture','approved_unix') else {}
        if not isinstance(req,dict):return 400,{'error':'invalid request'}
        if action=='preview' and set(req)=={'query','ids'}:
            item=self._prepare(req['query'],req['ids'],backend)
            mode={'mode':'single'} if item.get('mode')=='single' else {}
            if item.get('status')!='ready':return 200,{'status':'insufficient_evidence','online':False,**mode,**provider}
            token=secrets.token_urlsafe(32);digest=hashlib.sha256(item['wire']).hexdigest()
            with self.lock:
                now=self.clock();self.pending={k:v for k,v in self.pending.items() if v[0]>now}
                if len(self.pending)>=12 and seat not in self.pending:return 429,{'error':'preview capacity reached'}
                self.pending[seat]=(now+180,token,req['query'],req['ids'][:],digest,self._anchor_digest(item),self.discovery.group_for(seat,'model') if self.discovery else None)
            return 200,{'status':'ready','wire_text':item['wire'].decode(),'sources':item['sources'],'wire_sha256':digest,'token':token,'online':self.gate is not None or self.discovery is not None,**mode,**provider}
        if action!='confirm' or set(req)!={'token','consent'} or req['consent'] is not True or not isinstance(req['token'],str):return 400,{'error':'explicit consent required'}
        if self.gate is None and self.discovery is None:return 503,{'error':'paid draft generation is disabled'}
        with self.lock:
            row=self.pending.get(seat)
            if not row or row[0]<=self.clock() or not secrets.compare_digest(row[1].encode(),req['token'].encode()):return 409,{'error':'expired or invalid preview'}
            del self.pending[seat]
        now,token,query,ids,expected,anchor_expected,gid=row
        item=self._prepare(query,ids,backend)
        if item.get('status')!='ready' or hashlib.sha256(item['wire']).hexdigest()!=expected or self._anchor_digest(item)!=anchor_expected:return 409,{'error':'source changed, preview again'}
        response = None

        def send(wire):
            nonlocal response
            if wire!=item['wire']:raise RuntimeError('request changed')
            rid=hashlib.sha256(token.encode()).hexdigest()
            if self.discovery:
                response = self.discovery.caller.call(seat,gid,'model',rid,json.loads(wire))
                return json.dumps(response).encode()
            self.gate.reserve(request_id=rid,session_id=seat,request_body=wire)
            response = self.transport(wire)
            return response

        try:
            result = synthesis.generate(item, send=send)
            if provider.get('provider_mode') == 'approved_unix':
                result['generation_receipt'] = _generation_receipt(response)
            return 200, {**result, **provider}
        except Exception:return 503,{"error":"generation unavailable; no automatic retry",**provider}
