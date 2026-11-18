"""One live provider request through the actual local app, for authorized testing only.
Use the user's own private DEEPSEEK_API_KEY in the parent environment.
Outputs only a redacted JSON result for the packaged, original fictional fixture.
Never put credentials in arguments, source files, logs or issue reports.
"""
import argparse, hashlib,json,os,re,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import build_opener,ProxyHandler,Request
ROOT=Path(__file__).resolve().parents[1]
Q='Where was the amber lantern recorded?'
SOURCE='The amber lantern was recorded on the northern shore.'
class Failed(Exception):pass
def run(*,live=False):
    if not live:raise Failed('not_confirmed')
    if not os.environ.get('DEEPSEEK_API_KEY','').strip():raise Failed('key_not_configured')
    raw=(ROOT/'demo'/'A-lantern.pdf').read_bytes()
    if hashlib.sha256(raw).hexdigest()!='b5b2e136ca4a8405cb9783032885374e90a2683edb5592c34bb2517491618dbe':raise Failed('fixture_integrity_failed')
    transport=build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='ed-live-smoke-') as tmp:
        p=subprocess.Popen([sys.executable,str(ROOT/'app.py'),'--data',tmp,'--port','0','--enable-deepseek'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf8',cwd=ROOT)
        try:
            line=p.stdout.readline();m=re.search(r'http://127\.0\.0\.1:\d+',line)
            if not m:raise Failed('local_server_start_failed')
            base=m.group(0)
            def request(path,data=None,mime=None):
                req=Request(base+path,data=data,headers={'Content-Type':mime} if mime else {})
                with transport.open(req,timeout=30) as resp:return json.loads(resp.read())
            if request('/api/library')!={'docs':[],'deepseek_enabled':True}:raise Failed('unexpected_library_or_provider_state')
            d=request('/api/import?name=original-fictional-smoke.pdf',raw,'application/pdf')['doc']
            def post(path,obj):return request(path,json.dumps(obj).encode(),'application/json')
            scope={'query':Q,'ids':[d['id']]}
            hits=post('/api/search',scope)['hits']
            if len(hits)!=1 or hits[0]['text']!=SOURCE or hits[0]['page']!=1:raise Failed('fixture_search_mismatch')
            start=time.monotonic()
            try:r=post('/api/verify',{**scope,'consent':True})
            except HTTPError as e:
                code=e.code;e.close();raise Failed('local_verify_http_'+str(code)) from None
            if r.get('status')=='insufficient_evidence':return {'result':'provider_returned_no_usable_extract','model':'deepseek-flash','verified_quotes':0}
            quotes=r.get('quotes',[])
            if r.get('status')!='supported' or not quotes or any(x.get('id')!=d['id'] or x.get('page')!=1 or x.get('block')!=hits[0]['block'] or not x.get('text') or x['text'] not in SOURCE for x in quotes):raise Failed('quote_validation_failed')
            return {'result':'app_provider_roundtrip_with_verified_excerpt','model':'deepseek-flash','verified_quotes':len(quotes),'physical_page':1,'fixture_sha256':d['id'],'seconds':round(time.monotonic()-start,2),'public_deployment_verified':False}
        finally:
            if p.poll() is None:p.terminate()
            try:p.wait(4)
            except subprocess.TimeoutExpired:p.kill();p.wait(4)
            p.stdout.close()
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--confirm-one-live-request',action='store_true',help='Send one original fictional query and matching excerpt to official DeepSeek; may incur API fees.')
    args=ap.parse_args()
    try: print(json.dumps(run(live=args.confirm_one_live_request)))
    except Failed as e:print(json.dumps({'result':'not_verified','reason':str(e)}));sys.exit(2)
    except Exception as e:print(json.dumps({'result':'not_verified','error_type':type(e).__name__}));sys.exit(2)
