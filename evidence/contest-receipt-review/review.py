"""Read received B artifacts only; never import or execute candidate code."""
import csv
import hashlib
import io
import json
import subprocess
import tarfile
from decimal import Decimal
from pathlib import Path
import sys
import zipfile

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'app/role_config.py').is_file())
sys.path.insert(0, str(ROOT))
from app.role_config import digest
from app.runner import code_version
from app.tasks_v3 import get_task
from app.comparisons import summarize, export_csv
from app.experiment_identity import SOURCE_PATHS, code_identity
from c_runner.runner import parse_evidence

BASE = ROOT / 'evidence/contest-strategies-config-comparison'
OUT = ROOT / 'runs/b-contest-receipt-review.json'
FROZEN = 'ebc7450973081fe95d88a777f49f26633aa5d851'
errors = []
checks = 0

def check(value, label):
    global checks
    checks += 1
    if not value:
        errors.append(label)

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

manifest = read(BASE / 'manifest.json')
comparison = read(BASE / 'comparison/comparison.json')
checksums = (BASE / 'SHA256SUMS.txt').read_text(encoding='utf-8-sig').splitlines()
for line in checksums:
    expected, name = line.split(None, 1)
    path = (BASE / name.lstrip('* ')).resolve()
    check(path.is_relative_to(BASE.resolve()), 'checksum path escapes archive')
    check(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
          'checksum:' + name)
check(hashlib.sha256((BASE / 'SHA256SUMS.txt').read_bytes()).hexdigest() ==
      (BASE / 'SHA256SUMS.txt.sha256').read_text().split()[0], 'checksum list digest')

configs = {s: read(BASE / f'configs/{s}-config.json') for s in ('fixed', 'dynamic')}
for field in ('bindings', 'parameters', 'roles', 'profiles_sha256', 'prompts_sha256'):
    check(configs['fixed'][field] == configs['dynamic'][field], 'paired configuration:' + field)
for strategy, config in configs.items():
    check(config['strategy'] == strategy, 'configuration strategy')

rows = []
snapshots = []
for folder in sorted((BASE / 'runs').iterdir()):
    if not folder.is_dir():
        continue
    report = read(folder / 'report-from-api.json')
    run, events = report['run'], report['events']
    task, result = run['task'], run['result']
    label = folder.name
    definition = get_task(task['project'])
    freeze = read(folder / 'trace/experiment.json')
    identity = freeze['identity']
    check(run['run_id'] == label, label + ':run id')
    # Incremental events omit raw_text to keep polling small; full reports/calls retain it.
    exported_events = read(folder / 'events-from-api.json')
    check([{k: v for k, v in e.items() if k != 'raw_text'} for e in events] == exported_events,
          label + ':event export excluding documented report-only raw_text')
    check(run['status'] == 'success' and result['final_result'] == 'passed', label + ':terminal')
    check(task['max_rounds'] == 3 and task['max_calls'] == 12 and task['total_seconds'] == 300,
          label + ':limits')
    check(task['config_id'] == configs[task['strategy']]['config_id'], label + ':configuration id')
    check(freeze['execution_kind'] == 'real' and identity['code']['commit'] == FROZEN
          and identity['code']['dirty'] is False, label + ':frozen source')
    check(digest(identity) == freeze['identity_sha256'], label + ':identity hash')
    check(identity['role_overrides'] == {}, label + ':default overrides scope')
    calls = [e for e in events if e.get('event') == 'agent_call']
    check(len(calls) == 3 and all(e.get('model_api_called') is True and e['mode'] == 'live'
                                and e['status'] == 'ok' for e in calls), label + ':real calls')
    check({e['role'] for e in calls} == {'repairer', 'detector', 'scheduler'}, label + ':roles')
    cost = Decimal(0)
    repair = None
    for call in calls:
        sub = folder / 'calls' / call['call_id']
        inp, out = read(sub / 'input.json'), read(sub / 'output.json')
        provenance = inp['provenance']
        binding = provenance['model_binding']
        check(provenance == out['provenance'], label + ':call provenance')
        check(digest(inp['messages']) == provenance['messages_sha256'], label + ':message hash')
        check(digest('') == provenance['task_instruction_sha256'], label + ':default instruction hash')
        check(binding['effective_parameters'] == {'max_tokens': 8192}, label + ':effective defaults')
        check(digest(binding['effective_parameters']) == binding['effective_parameters_sha256'],
              label + ':parameter hash')
        check(out['usage'] == call['usage'] and out['raw_text'] == call['raw_text'], label + ':call exports')
        usage = out['usage']
        calculated = (Decimal(usage['prompt_tokens']) * Decimal(binding['input_cny_per_million']) +
                      Decimal(usage['completion_tokens']) * Decimal(binding['output_cny_per_million'])) / 1000000
        check(calculated == Decimal(out['cost']['amount_cny']), label + ':usage price')
        cost += calculated
        if out['role'] == 'repairer':
            repair = out['parsed_output']
    check(cost == Decimal(result['cost']['amount_cny']), label + ':call cost total')
    for name in ('base', 'candidate-1', 'final-work'):
        definition.verify(folder / name, candidate=name != 'base')
    candidate = folder / 'candidate-1' / definition.source
    final = folder / 'final-work' / definition.source
    check(candidate.read_bytes() == final.read_bytes(), label + ':clean final bytes')
    check(code_version(folder / 'final-work', (definition.source,)) == result['code_version'],
          label + ':final version')
    change = next(c for c in repair['changes'] if c['path'] == definition.source)
    check(final.read_text(encoding='utf-8') == change['new_content'], label + ':natural repair bytes')
    parsed_counts = {}
    for phase in ('baseline', 'development-1', 'final-check'):
        stage = folder / phase
        record = read(stage / 'result.json')
        parsed = parse_evidence(stage / 'trusted-run/output/junit.xml',
                                stage / 'trusted-run/output/pytest_events.json',
                                definition.profile, record['exit_code'])
        expected = 'failed' if phase == 'baseline' else 'passed'
        check(parsed['verification_status'] == expected, label + ':' + phase + ':parsed status')
        check(parsed['counts'] == record['counts'], label + ':' + phase + ':parsed counts')
        check(record['cleanup_passed'] and record['isolation_ready']
              and record['evidence_matches_current_version'], label + ':' + phase + ':bound isolation')
        container = read(stage / 'trusted-run/container.json')[0]
        host = container['HostConfig']
        check(host['NetworkMode'] == 'none' and host['ReadonlyRootfs'] and host['CapDrop'] == ['ALL']
              and 'no-new-privileges' in host['SecurityOpt'] and not host['Privileged'],
              label + ':' + phase + ':container controls')
        check(container['Image'] == identity['image'], label + ':' + phase + ':image')
        if phase != 'baseline':
            check(record['code_version'] == result['code_version'] and
                  record['test_suite_version'] == result['test_suite_version'], label + ':' + phase + ':version')
        parsed_counts[phase] = parsed['counts']
    bundle = BASE / f'bundle-{label}.zip'
    # Bundles are alongside the run folders in B's archive.
    if not bundle.exists():
        bundle = next(BASE.rglob(f'bundle-{label}.zip'))
    with zipfile.ZipFile(bundle) as z:
        check(z.testzip() is None, label + ':bundle CRC')
    check(any(e.get('event') == 'decision' and e.get('effective_action') == 'final_check'
              and e.get('program_decision') for e in events), label + ':program final decision')
    rows.append({'run_id': label, 'project': task['project'], 'strategy': task['strategy'],
                 'status': run['status'], 'cost_cny': str(cost), 'identity_sha256': freeze['identity_sha256'],
                 'counts': parsed_counts})
    snapshots.append((run['created_at'], cost, Decimal(result['cost']['budget']['accounts'][0]['estimated_spent_cny'])))

check(len(rows) == 6, 'six original runs')
check(summarize(comparison['pairs']) == comparison['summary'], 'comparison independently recalculated summary')
actual_csv = list(csv.reader(io.StringIO((BASE / 'comparison/comparison.csv').read_text(encoding='utf-8-sig'))))
generated_csv = list(csv.reader(io.StringIO(export_csv(comparison).lstrip('\ufeff'))))
check(actual_csv == generated_csv, 'CSV equals frozen JSON')
for pair in comparison['pairs']:
    fixed = next(r for r in rows if r['run_id'] == pair['fixed']['run_id'])
    dynamic = next(r for r in rows if r['run_id'] == pair['dynamic']['run_id'])
    check(fixed['identity_sha256'] == dynamic['identity_sha256'], pair['project'] + ':paired identity')
    for strategy, row in [('fixed', fixed), ('dynamic', dynamic)]:
        check(Decimal(pair[strategy]['cost']['amount_cny']) == Decimal(row['cost_cny']),
              pair['project'] + ':' + strategy + ':comparison cost')
ledger = read(BASE / 'ledger/budget-after.json')['accounts'][0]
running = Decimal(manifest['ledger']['before_cny'])
for _, cost, after in sorted(snapshots):
    running += cost
    check(running == after, 'successive budget snapshot arithmetic')
check(running == Decimal(ledger['estimated_spent_cny']) == Decimal(manifest['ledger']['after_cny']),
      'final ledger arithmetic')
isolation = read(BASE / 'isolation/docker-repair-132b0464623c/summary.json')
check(isolation['status'] == 'passed' and isolation['git_commit'] == FROZEN
      and isolation['model_api_called'] is False, 'five-case isolation scope')
names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', FROZEN, '--', *SOURCE_PATHS]).decode().splitlines()
archive = subprocess.check_output(['git', 'archive', '--format=tar', FROZEN, '--',
                                   *sorted({n.split('/')[0] for n in names})])
with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as t:
    hashes = {m.name: hashlib.sha256(t.extractfile(m).read()).hexdigest()
              for m in t.getmembers() if m.isfile() and m.name in names}
reported_source = sorted({read(p)['identity']['code']['source_sha256']
                          for p in (BASE / 'runs').glob('*/trace/experiment.json')})
source_review = {'B_reported': reported_source,
                 'local_checkout': code_identity(ROOT)['source_sha256'],
                 'frozen_git_blob_bytes': digest(hashes), 'tracked_source_files': len(hashes),
                 'status': 'not independently reproduced; request B per-file source manifest or source archive',
                 'note': 'Different checkout bytes may affect aggregate hashes; cause not established. Do not infer tampering or dismiss as newline-only.'}
review = {'scope': 'received B artifact consistency review; no candidate execution, no paid calls, no C personal signoff',
          'B_commit': '43efb37', 'frozen_commit': FROZEN, 'checks': checks, 'errors': errors,
          'checksum_entries': len(checksums), 'runs': rows, 'summary': comparison['summary'],
          'event_export_note': 'Incremental API events omit raw_text; reports and per-call originals retain it. Compared remaining fields and order exactly.',
          'ui_initiation': 'User confirmed API/CLI initiation; UI creation/start requirement not achieved.',
          'source_identity_review': source_review,
          'estimated_total_cny': str(sum((Decimal(r['cost_cny']) for r in rows), Decimal(0))),
          'limitations': ['All six runs pass at first candidate; no retry, upper-limit or human-handoff real-run proof.',
                          'Saved configurations omit role_overrides; live evidence covers defaults, not edited instructions or advanced values.',
                          'User confirmed API/CLI initiation, so these six runs do not satisfy the requested UI initiation.',
                          'Source aggregate hash cannot yet be independently reproduced from local checkout or frozen Git blobs; B per-file source hashes are required.',
                          'Ledger exports include budget snapshots, not the original ledger or independently exported per-call ledger rows.',
                          'No C personal exact-version review, formal video or rehearsal record in this package.']}
OUT.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'checks': checks, 'errors': errors, 'runs': len(rows), 'total_cny': review['estimated_total_cny'],
                  'output': str(OUT)}, ensure_ascii=False))
sys.exit(bool(errors))
