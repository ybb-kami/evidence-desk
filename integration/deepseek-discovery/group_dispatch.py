"""Single audited dispatch boundary; no credential lookup, no retries, no ledger provisioning."""
import hashlib,http.client,json,ssl
from group_budget import ENDPOINT,GroupBudget,Denied
class ApprovedGroupCaller:
    def __init__(self,ledger,transport):
        if not isinstance(ledger,GroupBudget) or not (callable(transport) or callable(getattr(transport, 'send_reserved', None))):raise Denied('no approved ledger or transport')
        if ledger.opening_required and not callable(getattr(transport, "send_reserved", None)):
            raise Denied("opening ledger requires the existing private broker")
        self.ledger,self.transport=ledger,transport
    def call(self,seat,gid,op,rid,body):
        endpoint=ENDPOINT[op]
        wire=json.dumps(body,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
        send_reserved = getattr(self.transport, 'send_reserved', None)
        reservation = self.ledger.reserve(seat=seat, gid=gid, op=op, rid=rid, endpoint=endpoint,
                                          wire=wire, private_dispatch=send_reserved is not None)
        if reservation['request_sha256']!=hashlib.sha256(wire).hexdigest():raise Denied('wire changed')
        if send_reserved is not None:
            return send_reserved(endpoint, wire, rid=rid)
        return self.transport(endpoint,wire)
def tls_transport(key):
    if not isinstance(key,str) or not key or not key.isascii() or not key.isprintable() or any(x.isspace() for x in key):raise Denied('invalid secret')
    def send(endpoint,wire):
        if endpoint not in ENDPOINT.values() or not isinstance(wire,bytes):raise Denied('wrong destination')
        con=http.client.HTTPSConnection('api.deepseek.com',443,timeout=40,context=ssl.create_default_context())
        try:
            con.request('POST',endpoint.removeprefix('https://api.deepseek.com'),body=wire,headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
            r=con.getresponse()
            if r.status!=200:raise RuntimeError('upstream HTTP '+str(r.status))
            raw=r.read(512001)
            if len(raw)>512000:raise RuntimeError('oversized response')
            return json.loads(raw)
        finally:con.close()
    return send
