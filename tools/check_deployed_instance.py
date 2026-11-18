"""Read-only deployed instance probe by default. Does not upload any PDF or call models.

Use on the server with http://127.0.0.1:8765 or against an explicitly authorized
HTTPS origin. The independently reported official hashes are supplied by the
operator. A matching existing library item does not prove official provenance.
"""
import argparse,hashlib,json,re,time
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import build_opener,HTTPRedirectHandler,Request
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):return None

def origin(value):
    u=urlsplit(value)
    if u.username or u.password or u.path not in ('','/') or u.query or u.fragment or not u.hostname:
        raise ValueError('请输入不含路径、登录名、密码或参数的服务根地址。')
    if u.scheme!='https' and not(u.scheme=='http' and u.hostname in ('localhost','127.0.0.1','::1')):
        raise ValueError('非本机实例须使用 HTTPS。')
    return value.rstrip('/')
def probe(base,hashes,opener=None):
    base=origin(base);client=opener or build_opener(NoRedirect())
    def get(path,limit=4_000_000):
        start=time.monotonic()
        with client.open(Request(base+path,headers={'Accept':'application/json,text/html,*/*'}),timeout=25) as r:
            data=r.read(limit+1)
        if len(data)>limit:raise ValueError('响应超出探针限额。')
        return data,round(1000*(time.monotonic()-start),1)
    html,h_ms=get('/');library,l_ms=get('/api/library')
    if b'Evidence Desk' not in html and '证据台'.encode() not in html:raise ValueError('未识别证据台网页。')
    ids={d.get('id'):d for d in json.loads(library)['docs']}
    output={'origin':base,'scope':'read_only_deployed_probe','http_ms':{'homepage':h_ms,'library':l_ms},'expected_files':[],'complete_official_validation':False}
    for label,digest in hashes.items():
        if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('输入校验和无效。')
        d=ids.get(digest);row={'label':label,'expected_sha256':digest,'present_in_active_library':bool(d)};output['expected_files'].append(row)
        if not d:continue
        raw,ms=get('/pdf/'+digest,24*1024**2);row.update(pages=d['pages'],source_sha256_matches=(hashlib.sha256(raw).hexdigest()==digest),original_fetch_ms=ms)
        page=d['pages'];page_data,ms=get(f'/api/reader/{digest}?view=page&page={page}');row.update(last_page_read=json.loads(page_data)['page']==page,last_page_read_ms=ms)
    return output
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('base');p.add_argument('--expected-hashes',type=argparse.FileType('r'),help='JSON object: operator labels to SHA256, not PDFs')
    a=p.parse_args()
    try:report=probe(a.base,json.load(a.expected_hashes) if a.expected_hashes else {})
    except (OSError,HTTPError,URLError,ValueError,KeyError) as e:
        print(json.dumps({'passed':False,'error_type':type(e).__name__,'message':'实例探针未完成，请核对地址、访问权限和响应格式。'},ensure_ascii=False));raise SystemExit(2)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(int(any(not(r.get('source_sha256_matches') and r.get('last_page_read')) for r in report['expected_files'])))
