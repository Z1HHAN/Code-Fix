"""Fixed subprocess entry for reviewed fixtures only, not an arbitrary command API."""
import json
import os
from pathlib import Path
import sys
import time

# Import installed pytest before inserting the approved workspace into sys.path.
import pytest


class Evidence:
    def __init__(self, destination):
        self.destination = Path(destination)
        self.data = {'schema_version': 1, 'collected_nodeids': None,
                     'collection_errors': [], 'reports': [],
                     'session_finished': False, 'exit_code': None}
        self.save()

    def save(self):
        temporary = self.destination.with_suffix('.writing')
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Windows scanners can briefly hold the previous destination open.
        # Retry only this atomic operation; persistent failure remains a failure.
        for attempt in range(3):
            try:
                os.replace(temporary, self.destination)
                return
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.02 * (attempt + 1))

    def pytest_collection_finish(self, session):
        self.data['collected_nodeids'] = [item.nodeid for item in session.items]
        self.save()

    def pytest_collectreport(self, report):
        if report.failed:
            self.data['collection_errors'].append({'nodeid': report.nodeid, 'details': str(report.longrepr)})
            self.save()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        result = yield
        report = result.get_result()
        entry = {'nodeid': report.nodeid, 'when': report.when,
                 'outcome': report.outcome, 'exception_type': None,
                 'exception_path': None}
        if call.excinfo is not None:
            entry['exception_type'] = call.excinfo.typename
            if call.excinfo.traceback:
                entry['exception_path'] = str(call.excinfo.traceback[-1].path)
        self.data['reports'].append(entry)
        self.save()

    def pytest_sessionfinish(self, session, exitstatus):
        self.data['session_finished'] = True
        self.data['exit_code'] = int(exitstatus)
        self.save()


def main():
    metadata_path, junit_path, configuration, *test_paths = sys.argv[1:]
    sys.path.insert(0, str(Path.cwd()))
    # Every argument comes from the runner's frozen registry, never a model.
    args = ['-c', configuration, '--rootdir=.', '-s', '-v', '--color=no',
            '-p', 'no:cacheprovider', '-p', 'no:debugging',
            '--junitxml=' + junit_path, '-o', 'junit_family=xunit2',
            '-o', 'junit_logging=no', *test_paths]
    return int(pytest.main(args, plugins=[Evidence(metadata_path)]))


if __name__ == '__main__':
    raise SystemExit(main())
