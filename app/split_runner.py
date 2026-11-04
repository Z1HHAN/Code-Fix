"""Two-container trust boundary for registered pure JSON functions only.

Only the candidate container imports candidate bytes. The trusted test container
has tests and a fixed proxy, and alone generates reports. Shared volume contains
only a Unix socket. A hostile candidate can cause failure/DoS, not write reports.
"""
from pathlib import Path
import json
import subprocess
import time
import uuid

from .docker_runner import DockerRunner, IsolationBlocked, ROOT
from .runner import code_version

FUNCTIONS = {'leap': 'is_leap', 'qb-get_factors': 'get_factors', 'qb-gcd': 'gcd', 'qb-sqrt': 'sqrt'}

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

    def run(self, work, evidence_dir, *, phase, timeout, expected_version):
        if not self.ready:
            raise IsolationBlocked('Isolation probe required')
        if self.task.project not in FUNCTIONS:
            raise IsolationBlocked('Task has no approved pure JSON RPC adapter')
        work = Path(work)
        self.task.verify(work, candidate=True)
        if code_version(work, self.task.profile['source_paths']) != expected_version:
            raise IsolationBlocked('Candidate version changed')
        outer = Path(evidence_dir)
        outer.mkdir(parents=True, exist_ok=False)
        candidate = outer / 'candidate-source'; candidate.mkdir()
        raw = (work / self.task.source).read_bytes()
        target = candidate / self.task.source; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        if code_version(candidate, self.task.profile['source_paths']) != expected_version:
            raise IsolationBlocked('Candidate snapshot mismatch')
        trusted = self.task.prepare(outer / 'trusted-tests')
        proxy = (ROOT / 'docker/rpc_proxy.py').read_text(encoding='utf-8')
        function = FUNCTIONS[self.task.project]
        (trusted / self.task.source).write_text(proxy + f'\ndef {function}(*args, **kwargs):\n    return invoke({function!r}, args, kwargs)\n', encoding='utf-8')
        volume = 'codefix-rpc-' + uuid.uuid4().hex
        name = 'codefix-candidate-' + uuid.uuid4().hex
        errors = []
        started = time.monotonic()
        def execute(args):
            return subprocess.run([self.executable, *args], capture_output=True, timeout=max(.01, timeout-(time.monotonic()-started)), check=True)
        result = None
        try:
            execute(['volume', 'create', '--driver', 'local', '--opt', 'type=tmpfs', '--opt', 'device=tmpfs',
                     '--opt', 'o=uid=65534,gid=65534,mode=0700,size=1048576', volume])
            command = super().command(name, candidate, candidate, ROOT / 'docker/candidate_rpc.py', ['/entry.py', '/w/'+self.task.source])
            command[2:2] = ['-d', '--mount', f'type=volume,source={volume},target=/rpc']
            subprocess.run(command, capture_output=True, timeout=max(.01,timeout-(time.monotonic()-started)), check=True)
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
                                      max(.01,timeout-(time.monotonic()-started)), folder)
            from c_runner.runner import parse_evidence, compute_test_suite_version
            result.update(phase=phase, code_version=expected_version, project=self.task.project,
                          execution_mode='docker-split-rpc', isolation_ready=True,
                          test_suite_version=compute_test_suite_version(self.task.profile),
                          expected_test_count=len(self.task.profile['expected_nodeids']),
                          stdout_path='trusted-run/stdout.txt', stderr_path='trusted-run/stderr.txt',
                          junit_path='trusted-run/output/junit.xml', structured_path='trusted-run/output/pytest_events.json',
                          evidence_matches_current_version=True, trust_boundary='separate_candidate_and_test_containers')
            if result['timed_out'] or not result['cleanup_passed'] or result['artifact_channel'] != 'docker-cp':
                result.update(status='failed', verification_status='inconclusive', failure_class='environment_error')
            else:
                result.update(parse_evidence(output/'junit.xml',output/'pytest_events.json',self.task.profile,result['exit_code']))
        finally:
            self.rpc_volume = None
            for args in (['rm','--force',name], ['volume','rm',volume]):
                try:
                    cleanup = subprocess.run([self.executable,*args], capture_output=True, timeout=10)
                    if cleanup.returncode: errors.append({'operation':args[0],'exit_code':cleanup.returncode})
                except (OSError,subprocess.SubprocessError) as exc:
                    errors.append({'operation':args[0],'error_type':type(exc).__name__})
            (outer/'split-cleanup.json').write_text(json.dumps(errors),encoding='utf-8')
        if errors:
            result.update(status='failed',verification_status='inconclusive',failure_class='environment_error',cleanup_passed=False)
        (outer/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
