"""One bounded local parser subprocess; stdin raw PDF, stdout result or abstention."""
import sys,resource
for key,value in [(resource.RLIMIT_AS,768*1024**2),(resource.RLIMIT_CPU,6),(resource.RLIMIT_FSIZE,0),(resource.RLIMIT_NOFILE,64),(resource.RLIMIT_CORE,0)]:
 _,old=resource.getrlimit(key); bound=value if old==resource.RLIM_INFINITY else min(value,old)
 resource.setrlimit(key,(bound,bound))
from native_table_probe import inspect
import json
try:
 b=sys.stdin.buffer.read(24*1024**2+1);obj=inspect(b,int(sys.argv[1]),sys.argv[2])
 wire=json.dumps(obj,ensure_ascii=True).encode()
 if len(wire)>512*1024:raise ValueError()
 sys.stdout.buffer.write(wire)
except Exception:
 sys.stdout.buffer.write(b'{"status":"parse_unavailable","cells":[]}')
