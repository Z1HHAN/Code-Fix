import json,os,sys,sqlite3,shutil
from pathlib import Path
from decimal import Decimal
from datetime import datetime,timezone
from urllib.parse import urlsplit

root=Path.cwd();runtime=root/'runs/paired111/runtime'
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
probe_folder=root/'runs/paired111'/('probe-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S'))
probe_folder.mkdir()
proof=check_execution(settings,probe_folder)
preflight={'model':settings['model'],'endpoint_host':urlsplit(settings['base_url']).hostname,
 'price_version':price['version'],'balance':balance(),'execution':proof,'model_requests':0}
out=root/'evidence/single-multi-agent/111';out.mkdir(parents=True,exist_ok=True)
preflight_path=out/'preflight.json'
if preflight_path.exists():preflight_path=out/('preflight-'+str(len(list(out.glob('preflight*.json'))))+'.json')
preflight_path.write_text(json.dumps(preflight,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(preflight,ensure_ascii=False),flush=True)
if sys.argv[-1] not in {'execute','resume'}:sys.exit(0)
if not proof.get('isolation_verified') or balance()['unknown_or_pending']:
 print(json.dumps({'blocked':'preflight_not_ready','new_paid_requests':0}),flush=True);sys.exit(2)
if not (runtime/'codefix-runtime-source.json').is_file():
 print(json.dumps({'blocked':'source_not_frozen','new_paid_requests':0}),flush=True);sys.exit(2)
from app.service import TaskService
from app.tasks_v3 import catalog as tasks
from app.configuration import ConfigurationStore
from app.role_overrides import capabilities
from app.model_profiles import load_profiles
service=TaskService(run_root)
limits={'repairer':8192,'detector':2048,'scheduler':2048,'locator':2048}
profile=load_profiles(runtime)['profiles']['personal_model']
limits={k:min(v,profile['max_output_tokens']) for k,v in limits.items()}
thinking={'enable_thinking':False} if 'enable_thinking' in capabilities(profile) else {}
configs={arm:ConfigurationStore(service.store).save({'strategy':arm,'bindings':{role:'personal_model' for role in limits},
 'parameters':{'max_rounds':3,'max_calls':12,'total_seconds':300},
 'role_overrides':{role:{'generation':{'max_output_tokens':n,**thinking}} for role,n in limits.items()}}) for arm in ('single','dynamic')}
projects=sorted(p for p in tasks() if p.startswith('qb-'));assert len(projects)==40
manifest={'scope':'one paired run per arm per registered task; no success fishing','kind':'agents',
 'started_at':datetime.now(timezone.utc).isoformat(),'model':settings['model'],'price_version':price['version'],
 'source_identity':json.loads((runtime/'codefix-runtime-source.json').read_text(encoding='utf-8'))['source_sha256'],
 'limits':{'max_rounds':3,'max_calls':12,'total_seconds':300,'cumulative_authorization_cny':'5'},
 'pairs':[{'project':p,'single':None,'dynamic':None,'not_run_reason':{'single':'尚未执行','dynamic':'尚未执行'}} for p in projects],
 'results':[],'stopped_reason':None}
def persist():
 manifest['authorization']=balance()
 (out/'manifest.tmp').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 (out/'manifest.tmp').replace(out/'manifest.json')
if sys.argv[-1]=='resume':
 original=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
 if original['source_identity']!=manifest['source_identity'] or original['model']!=manifest['model'] or original['price_version']!=manifest['price_version']:
  raise RuntimeError('续跑的代码、模型或价格身份不同，禁止混入原批')
 archive=out/('manifest-segment-'+str(len(original.get('segments',[]))+1).zfill(3)+'.json')
 if archive.exists():raise RuntimeError('续跑归档已存在，需人工核对，禁止覆盖')
 shutil.copyfile(out/'manifest.json',archive)
 previous=out/('comparison-'+json.loads((out/'comparison.json').read_text(encoding='utf-8'))['comparison_id']+'.json')
 if not previous.exists():shutil.copyfile(out/'comparison.json',previous)
 manifest=original
 recorded={row['run_id'] for row in manifest['results']}
 if any(pair[arm] and pair[arm] not in recorded for pair in manifest['pairs'] for arm in ('single','dynamic')):
  raise RuntimeError('有未归档的已创建任务，需先补收，不能重复调用')
 manifest.setdefault('segments',[]).append({'original_manifest':archive.name,'prior_finished_at':manifest.get('finished_at'),
  'resumed_at':datetime.now(timezone.utc).isoformat()})
 manifest['stopped_reason']=None;manifest['finished_at']=None
persist()
for index,pair in enumerate(manifest['pairs']):
 for arm in (('single','dynamic') if index%2==0 else ('dynamic','single')):
  if pair[arm] is not None:continue
  b=balance()
  if b['unknown_or_pending'] or Decimal(b['estimated_cny'])>=Decimal('5'):
   manifest['stopped_reason']='累计费用未知或已到授权上限';break
  config=configs[arm]
  task=service.store.create({'project':pair['project'],'mode':'repair','strategy':arm,
    'config_id':config['config_id'],**config['parameters']})
  pair[arm]=task['run_id'];pair['not_run_reason'].pop(arm,None);persist()
  print(json.dumps({'starting':pair['project'],'arm':arm,'run_id':task['run_id']}),flush=True)
  run=service.execute(task['run_id']);service.write_report(task['run_id'])
  manifest['results'].append({'project':pair['project'],'arm':arm,'run_id':task['run_id'],'status':run['status'],'result':run['result']})
  shutil.copytree(service.store.directory(task['run_id']),out/task['run_id'])
  persist();print(json.dumps({'completed':pair['project'],'arm':arm,'status':run['status'],'requests':run['result'].get('actual_model_requests'),'balance':balance()},ensure_ascii=False),flush=True)
  if run.get('control') or run['result'].get('isolation_ready') is False:
   manifest['stopped_reason']='用户控制或隔离条件失败';break
  if balance()['unknown_or_pending'] or '累计费用授权' in run['result'].get('reason',''):
   manifest['stopped_reason']='累计费用授权不足或费用未知';break
 if manifest['stopped_reason']:break
manifest['finished_at']=datetime.now(timezone.utc).isoformat();persist()
from app.comparisons import ComparisonStore,export_csv
comparison=ComparisonStore(service.store).create({'kind':'agents','title':'QuixBugs-Python 40题：单Agent与多Agent同条件配对111',
 'scope':'formal','pairs':manifest['pairs']})
(out/'comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
(out/'comparison.csv').write_text(export_csv(comparison),encoding='utf-8')
print(json.dumps({'comparison_id':comparison['comparison_id'],'summary':comparison['summary'],'paired_outcomes':comparison['paired_outcomes']},ensure_ascii=False),flush=True)
