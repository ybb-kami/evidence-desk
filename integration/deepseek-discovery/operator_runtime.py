"""Deployment-side factory. Never provisions/repairs a database, opens a port or reads secrets.
Pass returned bridges to the SAME gateway. Do not enable legacy verify/model_bridge.
"""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'candidate/gateway'))
from synthesis_bridge import SynthesisBridge
from official_input_bound import submitted_count,_load
from group_budget import GroupBudget,Denied
from group_dispatch import ApprovedGroupCaller,tls_transport
from discovery_tickets import Tickets
def runtime(config,key):
    if set(config)!={'path','approval_sha','price_sha','verified_epoch','expiry_epoch','seat_groups'}:raise Denied('operator metadata')
    _load()
    g=GroupBudget(**config,input_bound=submitted_count);g._time()
    with g._connect() as c:
        if c.execute('SELECT hash FROM conf').fetchall()!=[(g.config,)]:raise Denied('ledger mismatch; no automatic migration/provision')
    discovery=Tickets.grouped(ApprovedGroupCaller(g,tls_transport(key)))
    return dict(discovery_bridge=discovery,synthesis_bridge=SynthesisBridge.grouped(discovery),model_bridge=None)
