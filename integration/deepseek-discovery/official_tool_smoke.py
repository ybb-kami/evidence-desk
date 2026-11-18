"""Owner-side smoke on the one deployment host. Does not create or replace any ledger.
Config is the exact GroupBudget constructor metadata used by the operator to provision
the unique ledger: path, approval_sha, price_sha, verified_epoch, expiry_epoch,
seat_groups. No credentials in this file or in the config. Run in a private terminal.
"""
import argparse, getpass, hashlib, json, os
from pathlib import Path
from discovery_tickets import Tickets
from group_budget import GroupBudget
from group_dispatch import ApprovedGroupCaller,tls_transport
def main():
 p=argparse.ArgumentParser();p.add_argument('config',type=Path);a=p.parse_args()
 cfg=json.loads(a.config.read_text())
 if set(cfg)!={'path','approval_sha','price_sha','verified_epoch','expiry_epoch','seat_groups'}:raise SystemExit('wrong operator metadata')
 g=GroupBudget(**cfg)
 import fcntl
 lock_fd=os.open(str(g.path)+".smoke-lock",os.O_RDONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
 fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 g._time()
 with g._connect() as c:
  if c.execute('SELECT hash FROM conf').fetchall()!=[(g.config,)]:raise SystemExit('wrong existing ledger')
 # Stable per-smoke identity; conservatively refuse to start again after any reservation.
 seat=hashlib.sha256((g.config+'/official-smoke').encode()).hexdigest()
 with g._connect() as c:
  if c.execute('SELECT COUNT(*) FROM groups_ WHERE seat=?',(seat,)).fetchone()[0]:raise SystemExit('smoke already reserved; audit instead of retrying')
 key=os.environ.get('DEEPSEEK_API_KEY') or getpass.getpass('DeepSeek API key (hidden): ')
 t=Tickets.grouped(ApprovedGroupCaller(g,tls_transport(key)))
 def action(op,val):
  pre=t.preview(seat,op,val)
  print(json.dumps({'endpoint':'api.deepseek.com/v1/tools/'+op,'outbound':pre['body'],'max_usd':0.00075},ensure_ascii=False))
  if input('Confirm sending this to DeepSeek? Type YES: ')!='YES':raise SystemExit('cancelled')
  return t.confirm(seat,pre['token'],True)
 rows=action('search','Attention Is All You Need site:arxiv.org')
 print(json.dumps([{'index':i,'url':r['url']} for i,r in enumerate(rows)],ensure_ascii=False))
 if rows:
  idx=input('Choose a listed research page by index or press enter to finish: ').strip()
  if idx:
   out=action('fetch',int(idx)); print(json.dumps({'status':'returned','url':out['url'],'nonempty_content':bool(out['content']),'source_type':out['kind']}))
if __name__=='__main__':main()
