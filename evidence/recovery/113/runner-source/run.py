import json,os,sys,sqlite3,shutil
from pathlib import Path
from decimal import Decimal
from datetime import datetime,timezone
from urllib.parse import urlsplit

root=Path.cwd();runtime=root/'runs/recovery113/runtime'
sys.path.insert(0,str(runtime))
data=json.load(sys.stdin);settings=data['settings'];run_root=Path(data['run_root'])
os.environ.update(CODE_FIX_PERSONAL_MODE='1',CODE_FIX_PERSONAL_KEY=settings['api_key'],
 CODE_FIX_PERSONAL_BASE_URL=settings['base_url'],CODE_FIX_PERSONAL_APPROVED='1',CODE_FIX_LIVE_APPROVED='1',
 CODE_FIX_EXECUTION_APPROVED='1',CODE_FIX_EXECUTION_URL=settings['execution_url'],
 CODE_FIX_EXECUTION_TOKEN=settings['execution_token'],CODE_FIX_EXECUTION_SSH_TUNNEL='1' if settings.get('ssh_tunnel') else '0',
 CODE_FIX_PERSONAL_TIMEOUT='60',CODE_FIX_PERSONAL_BUDGET='money',CODE_FIX_PERSONAL_DAILY_LIMIT='5',
 CODE_FIX_PERSONAL_AUTHORIZATION_ID='A-097-CNY5-20261111',CODE_FIX_PERSONAL_AUTHORIZATION_LIMIT='5')
from app.personal_pricing import catalog,validate_price
price=settings.get('model_prices',{}).get(settings['model'])
if price is None:
 price=next((p for p in catalog() if p['model']==settings['model'] and p['endpoint_host']==urlsplit(settings['base_url']).hostname),None)
if price is None:
 print(json.dumps({'blocked':'model_price_unmatched','model':settings['model']}),flush=True);sys.exit(2)
price=validate_price(price)
settings.update(max_output_tokens=8192,models=[settings['model']],monetary_budget=True,budget_currency='CNY',daily_limit='5',
 model_prices={settings['model']:price},price_version=price['version'])
from app.personal import configure_runtime
configure_runtime(runtime,settings)
from app.connection_checks import check_execution,check_ledger
from app.live_model import Ledger
ledger=Ledger(run_root)
def balance():
 with sqlite3.connect(ledger.path) as db:
  rows=db.execute('SELECT status,actual,reserved FROM personal_calls WHERE authorization_id=?',('A-097-CNY5-20261111',)).fetchall()
 return {'requests':len(rows),'estimated_cny':str(Decimal(sum(r[1] or 0 for r in rows))/1000000),
         'unknown_or_pending':sum(r[0] in {'pending','unknown'} for r in rows),
         'held_unknown':sum(r[0]=='reserved_unknown' for r in rows),
         'held_upper_cny':str(Decimal(sum(r[2] for r in rows if r[0]=='reserved_unknown'))/1000000),
         'budget_committed_upper_cny':str(Decimal(sum(r[1] if r[1] is not None else r[2] for r in rows))/1000000)}

from app.connection_checks import check_execution
from app.service import TaskService
from app.configuration import ConfigurationStore
from app.role_overrides import capabilities
from app.model_profiles import load_profiles
out=root/'evidence/recovery/113';out.mkdir(parents=True,exist_ok=True)
probe=out/'preflight';proof=check_execution(settings,probe)
preflight={'execution':proof,'balance':balance(),'provider_model_requests':0}
(out/'preflight.json').write_text(json.dumps(preflight,ensure_ascii=False,indent=2),encoding='utf-8')
if not proof.get('isolation_verified') or balance()['unknown_or_pending']:sys.exit(2)
service=TaskService(run_root)
profile=load_profiles(runtime)['profiles']['personal_model']
limits={'repairer':8192,'detector':2048,'scheduler':2048,'locator':2048}
thinking={'enable_thinking':False} if 'enable_thinking' in capabilities(profile) else {}
config=ConfigurationStore(service.store).save({'strategy':'dynamic','bindings':{role:'personal_model' for role in limits},
 'parameters':{'max_rounds':5,'max_calls':20,'total_seconds':600},
 'role_overrides':{role:{'generation':{'max_output_tokens':n,**thinking}} for role,n in limits.items()}})
original=json.loads((root/'evidence/single-multi-agent/111/manifest.json').read_text(encoding='utf-8'))
manifest={'scope':'separate targeted recovery; expanded limits and new transport version; NOT replacement paired results',
 'source':json.loads((runtime/'codefix-runtime-source.json').read_text(encoding='utf-8'))['commit'],
 'config_id':config['config_id'],'parameters':config['parameters'],'started_at':datetime.now(timezone.utc).isoformat(),
 'original_runs':{v['project']:v['run_id'] for v in original['results'] if v['arm']=='dynamic' and v['status']!='success'},
 'results':[],'balance_before':balance(),'stop_reason':None}
def persist():
 manifest['authorization']=balance()
 (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
persist()
for project in ['qb-flatten','qb-shunting_yard','qb-next_palindrome']:
 task=service.store.create({'project':project,'mode':'repair','strategy':'dynamic','config_id':config['config_id'],**config['parameters']})
 print(json.dumps({'starting':project,'run_id':task['run_id']}),flush=True)
 run=service.execute(task['run_id']);service.write_report(task['run_id'])
 manifest['results'].append({'project':project,'run_id':task['run_id'],'status':run['status'],'result':run['result']})
 shutil.copytree(service.store.directory(task['run_id']),out/task['run_id']);persist()
 print(json.dumps({'completed':project,'status':run['status'],'rounds':run['result'].get('rounds_used'),'balance':balance()},ensure_ascii=False),flush=True)
 if balance()['unknown_or_pending'] or run.get('control') or run['result'].get('isolation_ready') is False:
  manifest['stop_reason']='unbounded unknown fee, user control, or isolation fault';break
manifest['finished_at']=datetime.now(timezone.utc).isoformat();persist()
