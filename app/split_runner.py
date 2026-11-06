"""Two-container trust boundary for registered pure JSON functions only.

Only the candidate container imports candidate bytes. The trusted test container
has tests and a fixed proxy, and alone generates reports. Shared volume contains
only a Unix socket. A hostile candidate can cause failure/DoS, not write reports.
"""
from pathlib import Path
import json
import keyword
import re
import subprocess
import time
import uuid

from c_runner.runner import compute_test_suite_version, parse_evidence

from .docker_runner import DockerRunner, IsolationBlocked, ROOT
from .runner import code_version

# Reviewed pure-JSON adapters.  Registration is per task, not per dataset: the
# only thing a name unlocks is "call this one function with JSON args inside the
# candidate container".  Every entry below was reviewed against the pinned test
# for three properties, and an entry that fails any of them stays out:
#   1. the module exports exactly that callable and the pinned test imports it
#      by that name;
#   2. every argument the pinned test passes survives a JSON round-trip
#      unchanged (no `node.Node` graphs, no tuple-keyed dicts);
#   3. the return value the pinned test compares survives a JSON round-trip
#      unchanged (no sets, no tuples, no generators, no Infinity).
# The one reviewed exception to (3) is MATERIALIZE_AS_LIST: those functions
# return a generator and their pinned test compares `list(f(...))`.  The
# candidate endpoint applies exactly that `list(...)` before serialising, so the
# RPC side evaluates the same expression the native test evaluates.  It is
# declared per function -- returns of every other entry are never coerced -- and
# mirrored in docker/candidate_rpc.py, with a regression test pinning equality.
# Excluded on purpose: breadth_first_search, depth_first_search, detect_cycle,
# reverse_linked_list, shortest_path_length, shortest_paths, topological_ordering,
# shortest_path_lengths (Node objects / tuple keys), hanoi (list of tuples),
# minimum_spanning_tree (set).  Isolation controls are identical for every
# entry; this table only decides which function may be invoked over the RPC
# socket.
FUNCTIONS = {
    'leap': 'is_leap',
    'qb-bitcount': 'bitcount',
    'qb-bucketsort': 'bucketsort',
    'qb-find_first_in_sorted': 'find_first_in_sorted',
    'qb-find_in_sorted': 'find_in_sorted',
    'qb-flatten': 'flatten',
    'qb-gcd': 'gcd',
    'qb-get_factors': 'get_factors',
    'qb-is_valid_parenthesization': 'is_valid_parenthesization',
    'qb-kheapsort': 'kheapsort',
    'qb-knapsack': 'knapsack',
    'qb-kth': 'kth',
    'qb-lcs_length': 'lcs_length',
    'qb-levenshtein': 'levenshtein',
    'qb-lis': 'lis',
    'qb-longest_common_subsequence': 'longest_common_subsequence',
    'qb-max_sublist_sum': 'max_sublist_sum',
    'qb-mergesort': 'mergesort',
    'qb-next_palindrome': 'next_palindrome',
    'qb-next_permutation': 'next_permutation',
    'qb-pascal': 'pascal',
    'qb-possible_change': 'possible_change',
    'qb-powerset': 'powerset',
    'qb-quicksort': 'quicksort',
    'qb-rpn_eval': 'rpn_eval',
    'qb-sieve': 'sieve',
    'qb-sqrt': 'sqrt',
    'qb-subsequences': 'subsequences',
    'qb-to_base': 'to_base',
    'qb-wrap': 'wrap',
}

# Generator-returning entries whose pinned test compares `list(f(...))`.
# Kept separate from FUNCTIONS so the name table stays a plain
# project -> function mapping.
MATERIALIZE_AS_LIST = frozenset({'flatten', 'kheapsort'})

AUDIT_PREFIX = b'CODEFIX_AUDIT '
FAKE_REPORT = re.compile(rb'(?i)tests?\s+passed\s*=')


class SplitDockerRunner(DockerRunner):
    def preflight(self, evidence_dir):
        if self.task.project not in FUNCTIONS:
            folder = Path(evidence_dir); folder.mkdir(parents=True, exist_ok=True)
            report = {'isolation_ready': False, 'reason': 'Task has no approved pure JSON RPC adapter'}
            (folder/'isolation.json').write_text(json.dumps(report),encoding='utf-8')
            return report
        return super().preflight(evidence_dir)

    def command(self, *args):
        command = super().command(*args)
        if getattr(self, 'rpc_volume', None):
            command[2:2] = ['--mount', f'type=volume,source={self.rpc_volume},target=/rpc,readonly']
        return command

    def _candidate_command(self, name, candidate, volume):
        command = super().command(name, candidate, candidate, ROOT / 'docker/candidate_rpc.py',
                                  ['/entry.py', '/w/' + self.task.source])
        command[2:2] = ['-d', '--mount', f'type=volume,source={volume},target=/rpc']
        # 候选容器的 stderr 是审计通道（audit hook 固定前缀），必须可读；
        # 基础命令里的 --log-driver=none 会丢弃它，这里换成有上限的 json-file。
        try:
            index = command.index('--log-driver=none')
            command[index:index + 1] = ['--log-driver=json-file', '--log-opt', 'max-size=1m',
                                        '--log-opt', 'max-file=1']
        except ValueError:
            pass
        return command

    def _collect_candidate_audit(self, name, outer):
        record = {'container': name, 'logs_captured': False, 'events': [], 'event_kinds': [],
                  'fake_report_output': False, 'violation': None, 'reason': None}
        try:
            logs = subprocess.run([self.executable, 'logs', name], capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            record['log_error'] = type(exc).__name__
            return record
        (outer / 'candidate-stdout.txt').write_bytes(logs.stdout or b'')
        (outer / 'candidate-stderr.txt').write_bytes(logs.stderr or b'')
        record['logs_captured'] = logs.returncode == 0
        events = []
        for line in ((logs.stdout or b'') + b'\n' + (logs.stderr or b'')).splitlines():
            if line.startswith(AUDIT_PREFIX):
                try:
                    payload = json.loads(line[len(AUDIT_PREFIX):])
                except ValueError:
                    continue
                if isinstance(payload, dict) and isinstance(payload.get('event'), str):
                    events.append(payload)
            elif FAKE_REPORT.search(line):
                record['fake_report_output'] = True
        kinds = sorted({event['event'] for event in events})
        record['events'] = events
        record['event_kinds'] = kinds
        if 'tests_write' in kinds or 'artifact_write' in kinds:
            record['violation'] = 'security_violation'
            record['reason'] = '候选进程尝试写入测试资产或证据输出目录'
        elif 'process_fork' in kinds or 'process_spawn' in kinds:
            record['violation'] = 'security_violation'
            record['reason'] = '候选进程创建子进程或执行外部程序'
        elif 'early_exit' in kinds or record['fake_report_output']:
            record['violation'] = 'fake_pass'
            record['reason'] = '候选进程伪造测试输出或提前退出'
        return record

    def run(self, work, evidence_dir, *, phase, timeout, expected_version):
        if not self.ready:
            raise IsolationBlocked('Isolation probe required')
        if self.task.project not in FUNCTIONS:
            raise IsolationBlocked('Task has no approved pure JSON RPC adapter')
        function = FUNCTIONS[self.task.project]
        if not isinstance(function, str) or not function.isidentifier() or keyword.iskeyword(function):
            raise IsolationBlocked('RPC function must be a non-keyword Python identifier')
        work = Path(work)
        self.task.verify(work, candidate=True)
        if code_version(work, self.task.profile['source_paths']) != expected_version:
            raise IsolationBlocked('Candidate version changed')
        outer = Path(evidence_dir)
        outer.mkdir(parents=True, exist_ok=False)
        candidate = outer / 'candidate-source'; candidate.mkdir()
        raw = (work / self.task.source).read_bytes()
        target = candidate / self.task.source; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        trusted = self.task.prepare(outer / 'trusted-tests')
        proxy = (ROOT / 'docker/rpc_proxy.py').read_text(encoding='utf-8')
        (trusted / self.task.source).write_text(proxy + f'\ndef {function}(*args, **kwargs):\n    return invoke({function!r}, args, kwargs)\n', encoding='utf-8')
        volume = 'codefix-rpc-' + uuid.uuid4().hex
        name = 'codefix-candidate-' + uuid.uuid4().hex
        errors = []
        started = time.monotonic()
        result = {'call_id': name, 'status': 'blocked', 'verification_status': 'not_run',
                  'failure_class': 'environment_error', 'reason': 'candidate run did not produce a result'}
        try:
            created = subprocess.run([self.executable, 'volume', 'create', '--driver', 'local', '--opt', 'type=tmpfs',
                                      '--opt', 'device=tmpfs',
                                      '--opt', 'o=uid=65534,gid=65534,mode=0700,size=1048576', volume],
                                     capture_output=True, timeout=max(.01, timeout - (time.monotonic() - started)))
            if created.returncode:
                raise IsolationBlocked('无法创建 RPC 临时卷')
            subprocess.run(self._candidate_command(name, candidate, volume), capture_output=True,
                           timeout=max(.01, timeout-(time.monotonic()-started)), check=True)
            self.rpc_volume = volume
            # Only reviewed tests and fixed proxy bytes enter this container.
            runner = DockerRunner(self.task.project)
            runner.ready = True; runner.executable = self.executable; runner.image = self.image
            runner.cancelled = getattr(self, 'cancelled', lambda: False)
            # Execute trusted snapshot with fixed proxy; do not pretend its
            # code_version equals the real candidate's source hash.
            folder = outer / 'trusted-run'; folder.mkdir()
            output = folder / 'output'; output.mkdir()
            runner.command = self.command
            result = runner._execute(trusted, output, ['/harness.py', *self.task.profile['test_paths']],
                                     max(.01, timeout-(time.monotonic()-started)), folder)
            result.update(phase=phase, code_version=expected_version, project=self.task.project,
                          execution_mode='docker-split-rpc', isolation_ready=True,
                          test_suite_version=compute_test_suite_version(self.task.profile),
                          expected_test_count=len(self.task.profile['expected_nodeids']),
                          stdout_path='trusted-run/stdout.txt', stderr_path='trusted-run/stderr.txt',
                          junit_path='trusted-run/output/junit.xml', structured_path='trusted-run/output/pytest_events.json',
                          evidence_matches_current_version=True,
                          trust_boundary='separate_candidate_and_test_containers')
            audit = self._collect_candidate_audit(name, outer)
            result['candidate_audit'] = audit
            result['runner_classification'] = {key: result.get(key) for key in
                                               ('status', 'verification_status', 'failure_class', 'reason')}
            if audit['violation']:
                result.update(status='failed', verification_status='failed',
                              failure_class=audit['violation'],
                              reason='候选审计通道阻断：' + audit['reason'])
            elif result['timed_out']:
                result.update(status='timeout', verification_status='inconclusive', failure_class='timeout',
                              reason='运行超时，已强制终止容器')
            elif not result['cleanup_passed'] or result['artifact_channel'] != 'docker-cp':
                result.update(status='failed', verification_status='inconclusive', failure_class='environment_error',
                              reason='可信测试容器产物通道或清理失败')
            else:
                result.update(parse_evidence(output/'junit.xml', output/'pytest_events.json',
                                             self.task.profile, result['exit_code']))
        finally:
            self.rpc_volume = None
            for args in (['rm','--force',name], ['volume','rm',volume]):
                try:
                    cleanup = subprocess.run([self.executable,*args], capture_output=True, timeout=15)
                    if cleanup.returncode: errors.append({'operation':args[0],'exit_code':cleanup.returncode})
                except (OSError,subprocess.SubprocessError) as exc:
                    errors.append({'operation':args[0],'error_type':type(exc).__name__})
            (outer/'split-cleanup.json').write_text(json.dumps(errors),encoding='utf-8')
        if errors:
            result.update(status='failed',verification_status='inconclusive',failure_class='environment_error',cleanup_passed=False)
        (outer/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
