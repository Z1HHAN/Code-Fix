"""Inventory evidence or run serial bad-baseline checks; never import a model client."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true', help='Run actual isolated baselines using configured execution service')
    args = parser.parse_args()
    from app.tasks_v3 import catalog, get_task
    from app.runner import code_version
    history_path = ROOT / 'evidence/quixbugs-coverage-review/coverage.json'
    history = json.loads(history_path.read_text(encoding='utf-8')) if history_path.exists() else {}
    history_rows = {r['project']: r for r in history.get('rows', [])}
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'registered bad-baseline qualification, not repair success',
              'model_requests': 0, 'history_source': str(history_path.relative_to(ROOT)), 'rows': []}
    for project in sorted(p for p in catalog() if p.startswith('qb-')):
        task = get_task(project)
        previous = history_rows.get(project, {})
        row = {'project': project, 'registration': task.summary(),
               'historical_final': previous.get('historical_final', 'unrecorded'),
               'historical_run_ids': previous.get('historical_run_ids'),
               'historical_raw_record_available': previous.get('historical_raw_record_available', False),
               'current_qualification': 'not_run', 'failure_class': None,
               'gap': '本轮隔离坏版本资格未复跑；历史汇总不能代替当前资格'}
        if args.execute:
            from app.remote_runner import execution_runner
            from app.split_runner import SplitDockerRunner
            runner = execution_runner(project, local_factory=SplitDockerRunner)
            folder = args.output / project
            try:
                probe = runner.preflight(folder / 'isolation')
                if not probe['isolation_ready']:
                    row.update(current_qualification='blocked', gap=probe.get('reason'))
                else:
                    work = task.prepare(folder / 'bad-baseline')
                    result = runner.run(work, folder / 'baseline', phase='development', timeout=60,
                        expected_version=code_version(work, task.profile['source_paths']))
                    accepted = (result.get('verification_status') == 'failed'
                        and result.get('failure_class') in {'assertion_failed', 'code_runtime_error'}
                        and result.get('evidence_matches_current_version') is True
                        and result.get('cleanup_passed') is True)
                    row.update(current_qualification='qualified' if accepted else 'not_qualified',
                        failure_class=result.get('failure_class'), counts=result.get('counts'),
                        evidence_path=f'{project}/baseline', image=runner.image,
                        gap=None if accepted else '本轮原始证据未满足坏版本资格')
            except Exception as exc:
                row.update(current_qualification='blocked', gap=type(exc).__name__ + ': ' + str(exc))
            finally:
                if hasattr(runner, 'close'):
                    runner.close()
        report['rows'].append(row)
        (args.output / 'qualification-inventory.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(project, row['current_qualification'], flush=True)
    print('Projects:', len(report['rows']), '; model requests: 0')


if __name__ == '__main__':
    main()
