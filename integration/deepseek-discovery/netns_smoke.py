"""Linux-only offline acceptance across two real network namespaces (unprivileged userns)."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from tempfile import TemporaryDirectory

from group_budget import GroupBudget
from group_dispatch import ApprovedGroupCaller
from official_input_bound import submitted_count
from private_transport import UnixProviderBroker, UnixProviderTransport


def isolated_client(folder):
    config = json.loads((folder / 'fixture.json').read_bytes())
    ledger = GroupBudget(**config, input_bound=submitted_count)
    sender = ApprovedGroupCaller(ledger, UnixProviderTransport(folder/'provider.sock', broker_uid=os.geteuid()))
    interfaces = [name for _, name in socket.if_nameindex()]
    if interfaces != ['lo']:
        raise RuntimeError('expected a new loopback-only namespace')
    content = json.dumps({'question':'Which marker?', 'documents':[[{'ref':'E1','excerpt':'The marker is amber.'}]]})
    payload = {'model':'deepseek-flash', 'messages':[{'role':'system','content':'Return JSON.'},
               {'role':'user','content':content}], 'max_tokens':512, 'stream':False,
               'thinking':{'type':'disabled'}, 'response_format':{'type':'json_object'}}
    result = sender.call('a'*64, 'b'*64, 'model', 'c'*64, payload)
    assert result['offline_fixture'] and 'amber' in result['choices'][0]['message']['content']
    print(json.dumps({'netns':os.readlink('/proc/self/ns/net'), 'interfaces':interfaces,
                      'provider_mode':'offline_fixture'}))


def run():
    with TemporaryDirectory(prefix='judge-isolated-') as directory:
        folder = Path(directory)
        now = int(time.time())
        config = dict(path=str(folder/'scratch.sqlite'), approval_sha='a'*64, price_sha='b'*64,
                      seat_groups=4, verified_epoch=now, expiry_epoch=now+300,
                      cap_policy=dict(cny_per_usd_upper='8.00', fx_source_sha256='d'*64,
                                      fx_verified_epoch=now, fx_expiry_epoch=now+300))
        (folder/'fixture.json').write_text(json.dumps(config))
        ledger = GroupBudget(**config)
        ledger.provision()  # sole disposable fixture established for this test
        with UnixProviderBroker(folder/'provider.sock', ledger=ledger, allow_uids=[os.geteuid()]) as broker:
            worker = threading.Thread(target=broker.serve_forever, daemon=True)
            worker.start()
            env = {k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','PYTHONPATH'}}
            result = subprocess.run(['unshare','--user','--map-root-user','--net','--',
                                     sys.executable, str(Path(__file__).resolve()), '--child', directory],
                                    env=env, capture_output=True, timeout=30, check=False, text=True)
            if result.returncode:
                raise RuntimeError('isolated client could not run: '+result.stderr.strip()[-1000:])
            client = json.loads(result.stdout)
            assert client['netns'] != os.readlink('/proc/self/ns/net')
            with ledger._connect() as db:
                assert db.execute('SELECT count(*),sum(nano) FROM spend').fetchone() == (1,315187200)
                assert db.execute('SELECT count(*) FROM private_pending').fetchone() == (0,)
        worker.join(3)
        print(json.dumps({'distinct_network_namespaces':True,'client_interfaces':client['interfaces'],
                          'transport':'pathname AF_UNIX','provider_mode':'offline_fixture',
                          'model_dispatches':1,'actual_paid_calls':0}))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--child':
        isolated_client(Path(sys.argv[2]))
    else:
        run()
