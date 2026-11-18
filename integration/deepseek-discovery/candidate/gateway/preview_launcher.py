"""Offline-only development entrypoint; no service credentials or transport accepted."""
import argparse
from pathlib import Path
from functools import partial
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from launcher import run
from gateway import serve
from synthesis_bridge import SynthesisBridge
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--app',required=True,type=Path);p.add_argument('--private-dir',required=True,type=Path);p.add_argument('--port',type=int,default=8819);a=p.parse_args()
    run(a.app,a.private_dir,2,a.port,gateway_serve=partial(serve,synthesis_bridge=SynthesisBridge()))
