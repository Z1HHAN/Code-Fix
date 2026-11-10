"""Static evidence inventory. Does not import or execute benchmark programs."""
from pathlib import Path
import ast
import csv
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'evidence/quixbugs-coverage-review'
OUT.mkdir(exist_ok=True)
catalog = json.loads((ROOT/'configs/tasks/catalog-v3.json').read_text(encoding='utf-8'))['projects']
source = ROOT/'app/split_runner.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
functions = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'FUNCTIONS' for target in node.targets))
batch_path = ROOT/'evidence/quixbugs-batch1.md'
batch = batch_path.read_text(encoding='utf-8')
history = {}
for line in batch.splitlines():
    if not line.startswith('| `qb-'):
        continue
    cells = [x.strip() for x in line.strip('|').split('|')]
    history[cells[0].strip('`')] = dict(rounds=int(cells[1]), final_result=cells[3],
                                     reported_cost_cny=cells[5], notes=cells[6],
                                     run_ids=re.findall(r'run-[0-9a-f]+', line))
classes = {
    'qb-shunting_yard': 'plain_json_registration_review',
    'qb-hanoi': 'tuple_preserving_output',
    'qb-minimum_spanning_tree': 'tuple_keys_and_set_output',
    'qb-shortest_path_lengths': 'tuple_keys_and_numeric_special_values_review',
    'qb-shortest_paths': 'tuple_keys',
    'qb-breadth_first_search': 'node_graph_identity',
    'qb-depth_first_search': 'node_graph_identity',
    'qb-detect_cycle': 'node_graph_cycles',
    'qb-reverse_linked_list': 'node_graph_identity_and_mutation',
    'qb-shortest_path_length': 'node_graph_identity_and_tuple_keys',
    'qb-topological_ordering': 'node_graph_identity_and_ordered_output',
}
rows = []
for name, task in sorted(catalog.items()):
    if not name.startswith('qb-'):
        continue
    profile = task['profile']
    rows.append(dict(project=name, historical_final=history[name]['final_result'],
                     historical_run_ids=';'.join(history[name]['run_ids']),
                     approved_rpc=name in functions,
                     qualification=task.get('qualification_status'),
                     expected_nodeids=len(profile['expected_nodeids']),
                     adapter_review='existing_approved_entry' if name in functions else classes[name],
                     source=profile['source_paths'][0],
                     historical_raw_record_available=(ROOT/'runs/batch1'/f'{name}.json').is_file()))
assert len(rows) == 40 and len(history) == 40
assert sum(row['approved_rpc'] for row in rows) == 29
assert sum(row['historical_final']=='passed' for row in rows) == 29
assert sum(row['historical_final']=='not_run' for row in rows) == 11
result = dict(scope='static coverage inventory; historical report claims are not independently rerun',
              historical_batch=dict(date='2026-11-06', source='evidence/quixbugs-batch1.md',
                                    source_sha256=hashlib.sha256(batch_path.read_bytes()).hexdigest(),
                                    max_rounds=3,max_calls=12,total_seconds=600,
                                    traversed=40,reported_final_passed=29,preflight_blocked=11,
                                    caveat='Summary includes reruns and configuration fixes; not a frozen first-attempt success-rate estimate'),
              current_rpc_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
              current_approved_rpc=29, current_missing_rpc=11,
              qualification_note='All 40 catalog fields remain pending; field value alone does not negate received B run evidence',
              paid_requests=0,candidate_execution=False,rows=rows)
(OUT/'coverage.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
with (OUT/'coverage.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
(OUT/'coverage-SHA256SUMS.txt').write_text(''.join(hashlib.sha256((OUT/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in ['coverage.json','coverage.csv']),encoding='utf-8')
print(json.dumps({k:result[k] for k in ['current_approved_rpc','current_missing_rpc','paid_requests']}) )
