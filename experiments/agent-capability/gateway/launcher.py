"""Run an isolated-per-slot, Linux-only, supervised loopback demonstration."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import ctypes
import http.client
import json
import os
from pathlib import Path
import re
import resource
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
DEFAULT_APP = HERE.parent / "evidence-desk" / "app.py"
MAX_JUDGES = 4
READY_SECONDS = 14
MAX_BANNER = 2048
BANNER = re.compile(rb"http://127\.0\.0\.1:([1-9][0-9]{0,4})(?![0-9])")
LIMITS = {
    resource.RLIMIT_AS: 2 * 1024**3,
    resource.RLIMIT_FSIZE: 192 * 1024**2,
    resource.RLIMIT_NOFILE: 128,
    resource.RLIMIT_CORE: 0,
    resource.RLIMIT_CPU: 900,
}


def _limits(parent):
    # Linux parent-death SIGTERM protects the app on abnormal launcher exit.
    # Compare PPID after setting it to cover the small fork/registration race.
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(1,signal.SIGTERM,0,0,0)!=0 or os.getppid()!=parent:
        raise SystemExit(125)
    os.umask(0o077)
    for kind,wanted in LIMITS.items():
        _,hard=resource.getrlimit(kind)
        capped=wanted if hard==resource.RLIM_INFINITY else min(hard,wanted)
        resource.setrlimit(kind,(capped,capped))


def _child_environment(folder):
    # No parent environment, auth sockets, API keys, proxies or real HOME.
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "HOME": str(folder/"home"), "TMPDIR": str(folder/"tmp"),
        "XDG_CONFIG_HOME": str(folder/"home"/".config"),
        "XDG_CACHE_HOME": str(folder/"home"/".cache"),
        "XDG_DATA_HOME": str(folder/"home"/".local"/"share"),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED":"1",
        "OPENBLAS_NUM_THREADS":"1","OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1",
    }


def _ready(process):
    buffer=b""
    with selectors.DefaultSelector() as watcher:
        watcher.register(process.stdout,selectors.EVENT_READ)
        expires=time.monotonic()+READY_SECONDS
        while time.monotonic()<expires:
            if not watcher.select(max(0,expires-time.monotonic())):
                break
            data=os.read(process.stdout.fileno(),MAX_BANNER+1-len(buffer))
            if not data:
                break
            buffer+=data
            if len(buffer)>MAX_BANNER:
                break
            banner=buffer.partition(b"\n")[0]
            found=BANNER.search(banner) if b"\n" in buffer else None
            if found:
                port=int(found[1])
                if 1<=port<=65535 and process.poll() is None:
                    connection=http.client.HTTPConnection("127.0.0.1",port,timeout=2)
                    try:
                        connection.request("GET","/api/library")
                        response=connection.getresponse()
                        if response.status==200:
                            manifest=json.loads(response.read(4096))
                            if manifest.get("docs")==[] and manifest.get("deepseek_enabled") is False:
                                return port
                    except (OSError,ValueError,http.client.HTTPException,AttributeError):
                        pass
                    finally:
                        connection.close()
                break
    raise RuntimeError("A local judging workbench did not become ready.")


def _signal_group(process, sig):
    try:
        os.killpg(process.pid,sig)
    except ProcessLookupError:
        pass


def _stop(processes):
    for process in processes:
        _signal_group(process,signal.SIGTERM)
    expires=time.monotonic()+10
    for process in processes:
        try:
            process.wait(timeout=max(0,expires-time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for process in processes:
        _signal_group(process,signal.SIGKILL)
    for process in processes:
        process.wait()
        if process.stdout:
            process.stdout.close()


def _write_credentials(path, origin, entries):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as destination:
        json.dump({"url":origin,"invites":entries},destination,ensure_ascii=False,indent=2)
        destination.write("\n")
        destination.flush()
        os.fsync(destination.fileno())


def _loopback(port):
    if type(port) is not int or not 1024<=port<=65535:
        raise ValueError("Choose a local gateway port between 1024 and 65535.")
    return f"http://127.0.0.1:{port}"


class _SlotPool(dict):
    """All app spawns occur in one stable thread (Linux PDEATHSIG parent)."""
    def __init__(self, app, directory):
        super().__init__()
        self.app,self.directory=app,directory
        self.workers={}
        self.spawner=ThreadPoolExecutor(max_workers=1,thread_name_prefix="judge-app-spawner")

    def _new(self,index,folder,old_port=None):
        folder.mkdir(mode=0o700)
        process=None
        try:
            for sub in ("home","tmp","data"):
                (folder/sub).mkdir(mode=0o700)
            # Do not use preexec_fn from a multithreaded gateway process.
            process=subprocess.Popen([sys.executable,str(HERE/"launcher.py"),"--internal-worker",
                                      str(os.getpid()),str(self.app),str(folder/"data")],
                                     cwd=self.app.parent,env=_child_environment(folder),stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
            port=_ready(process)
            if port==old_port or port in self.workers:
                raise RuntimeError("Judging workbenches did not obtain separate loopback ports.")
            self.workers[port]=(index,folder,process)
            return port
        except BaseException:
            if process is not None:
                _stop([process])
            shutil.rmtree(folder)
            raise

    def add(self,index):
        return self.spawner.submit(self._new,index,self.directory/f"judge-{index}").result()

    def _rotate(self,old_port):
        previous=self.workers.pop(old_port,None)
        if previous is None:
            raise RuntimeError("The old judging slot was already removed.")
        index,folder,process=previous
        try:
            _stop([process])
        finally:
            shutil.rmtree(folder)
        # Do not recycle a path or retain old documents in a new session.
        return self._new(index,self.directory/f"judge-{index}-{secrets.token_hex(8)}",old_port)

    def rotate_slot(self, old_port):
        return self.spawner.submit(self._rotate,old_port).result()

    def _close(self):
        active=list(self.workers.values())
        self.workers.clear()
        _stop([process for _,_,process in active])

    def close(self):
        try:
            self.spawner.submit(self._close).result()
        finally:
            self.spawner.shutdown()


@contextmanager
def demo_slots(app, private_dir, judges, origin):
    if sys.platform!="linux":
        raise ValueError("The supervised launcher requires Linux.")
    if type(judges) is not int or not 1<=judges<=MAX_JUDGES:
        raise ValueError("Choose between 1 and 4 judging slots.")
    local=re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{3,4})",origin) if isinstance(origin,str) else None
    if not local or _loopback(int(local[1]))!=origin:
        raise ValueError("Only http://127.0.0.1 on a local port is permitted.")
    app=Path(app).resolve()
    if not app.is_file():
        raise ValueError("Workbench app.py was not found.")
    path=Path(private_dir)
    directory=path.parent.resolve()/path.name
    directory.mkdir(mode=0o700)
    slots=None
    try:
        slots=_SlotPool(app,directory)
        records=[]
        for i in range(1,judges+1):
            port=slots.add(i)
            code=secrets.token_urlsafe(32)
            while code in slots:
                code=secrets.token_urlsafe(32)
            slots[code]=port
            records.append({"judge":i,"code":code})
        _write_credentials(directory/"invites.json",origin,records)
        yield slots
    finally:
        try:
            if slots is not None:
                slots.close()
        finally:
            shutil.rmtree(directory)


def run(app, private_dir, judges, port, *, gateway_serve=None, anonymous=True, ttl=3600, public_origin=None):
    origin=_loopback(port)
    if public_origin is not None:
        # Only static operator input. Never reconstruct public origin from headers.
        from urllib.parse import urlsplit
        u=urlsplit(public_origin)
        if (not re.fullmatch(r"https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[1-9][0-9]{0,4})?",public_origin)
                or not u.hostname or "." not in u.hostname or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",x) for x in u.hostname.split("."))
                or (u.port is not None and not 1<=u.port<=65535)):
            raise ValueError("Expected a single exact HTTPS origin, without path, query or credentials.")
    if type(ttl) is not int or not 2<=ttl<=3600:
        raise ValueError("Choose a session lifetime from 2 to 3600 seconds.")
    if gateway_serve is None:
        from gateway import serve
        from synthesis_bridge import SynthesisBridge
        from functools import partial
        gateway_serve=partial(serve,synthesis_bridge=SynthesisBridge())
    with demo_slots(app,private_dir,judges,origin) as slots:
        print(f"本机演示：{origin}（仅回环；结束后移除临时文库）。",flush=True)
        gateway_serve(slots,port,public_origin or origin,public_origin is None,anonymous=anonymous,rotate_slot=slots.rotate_slot,ttl=ttl,asset_root=Path(app).resolve().parent)


def main():
    if len(sys.argv)==5 and sys.argv[1]=="--internal-worker":
        _,_,parent,app,data=sys.argv
        _limits(int(parent))
        os.execve(sys.executable,[sys.executable,app,"--port","0","--data",data],os.environ)
    parser=argparse.ArgumentParser(description=__doc__,
        epilog="Use only original or public PDFs. No public hosting, tunneling, forwarding or existing private library.")
    parser.add_argument("--app",type=Path,default=DEFAULT_APP)
    parser.add_argument("--private-dir",type=Path,required=True,help="New directory; removed on shutdown")
    parser.add_argument("--judges",type=int,default=2)
    parser.add_argument("--port",type=int,default=8818)
    parser.add_argument("--invitation-only",action="store_true")
    parser.add_argument("--public-origin",help="Fixed externally terminated HTTPS origin; listener remains loopback")
    parser.add_argument("--ttl",type=int,default=3600,help="Local session lifetime in seconds")
    args=parser.parse_args()
    previous=signal.getsignal(signal.SIGTERM)
    def on_term(*_):
        # Ignore repeated shutdown signals while reaping workers.
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,on_term)
    try:
        run(args.app,args.private_dir,args.judges,args.port,anonymous=not args.invitation_only,ttl=args.ttl,public_origin=args.public_origin)
    except KeyboardInterrupt:
        return 0
    except (OSError,RuntimeError,ValueError) as exc:
        parser.exit(1,"Local demo could not start: "+str(exc)+"\n")
    finally:
        signal.signal(signal.SIGTERM,previous)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
