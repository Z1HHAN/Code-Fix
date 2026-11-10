"""Local workflow adapter for the authenticated cloud test service."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid

import httpx

from .docker_runner import IsolationBlocked
from .remote_execution import PROTOCOL, TERMINAL, EVIDENCE_NAMES, MAX_EVIDENCE
from .runner import code_version
from .tasks_v3 import get_task


class RemoteRunner:
    mode = 'remote-docker'

    def __init__(self, project='leap', *, url=None, token=None, client=None):
        self.task = get_task(project)
        self.url = (url or os.environ.get('CODE_FIX_EXECUTION_URL', '')).rstrip('/')
        parsed = urlsplit(self.url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise IsolationBlocked('远程执行地址须为HTTPS服务根地址')
        token = token or os.environ.get('CODE_FIX_EXECUTION_TOKEN', '')
        if not isinstance(token, str) or not 32 <= len(token) <= 256 or not token.isascii() or any(c.isspace() for c in token):
            raise IsolationBlocked('请配置有效的远程执行访问码')
        from .redaction import register_secret
        self.token = register_secret(token)
        self.transport_mode = 'test_transport' if client is not None else 'live'
        self.client = client or httpx.Client(base_url=self.url, follow_redirects=False, trust_env=False)
        self.owns_client = client is None
        self.cancelled = lambda: False
        self.deadline = None
        self.ready, self.image = False, None
        self.server_build_sha256 = None

    def close(self):
        if self.owns_client:
            self.client.close()

    def request(self, method, path, *, value=None, seconds=10, limit=1048576):
        try:
            with self.client.stream(method, path, json=value,
                                    headers={'Authorization': 'Bearer ' + self.token},
                                    timeout=max(.01, seconds), follow_redirects=False) as response:
                if response.status_code >= 300:
                    raise IsolationBlocked('远程执行服务返回HTTP ' + str(response.status_code))
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        raise IsolationBlocked('远程执行响应超过大小上限')
            return json.loads(body)
        except (httpx.HTTPError, ValueError, UnicodeDecodeError):
            raise IsolationBlocked('远程执行连接或响应异常；没有自动重提任务') from None

    def remaining(self, deadline):
        if self.cancelled():
            raise IsolationBlocked('用户已停止执行')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IsolationBlocked('远程执行等待或运行超时')
        return remaining

    def submit(self, kind, seconds, **extra):
        deadline = min(time.monotonic() + seconds, self.deadline or float('inf'))
        request = {'protocol': PROTOCOL, 'kind': kind, 'project': self.task.project,
                   'request_id': uuid.uuid4().hex, 'expires_in': self.remaining(deadline),
                   'test_seconds': min(60, seconds),
                   'test_suite_version': self.task.summary()['test_suite_version'], **extra}
        job = self.request('POST', '/v1/jobs', value=request, seconds=min(10, self.remaining(deadline)))
        jid = job.get('job_id', '')
        if not re.fullmatch(r'job-[a-f0-9]{32}', jid):
            raise IsolationBlocked('远程执行返回非法任务编号')
        try:
            while True:
                if (job.get('job_id') != jid or job.get('project') != self.task.project
                        or job.get('kind') != kind):
                    raise IsolationBlocked('远程任务身份不一致')
                self.remaining(deadline)
                if job.get('status') in TERMINAL:
                    if job['status'] != 'completed':
                        raise IsolationBlocked('远程任务' + job['status'] + '；' + str(job.get('reason') or '没有验收结果'))
                    break
                if job.get('status') not in {'queued', 'running', 'cancel_requested'}:
                    raise IsolationBlocked('远程任务状态无效')
                time.sleep(min(.25, self.remaining(deadline)))
                job = self.request('GET', '/v1/jobs/' + jid, seconds=min(10, self.remaining(deadline)))
            evidence = self.request('GET', '/v1/jobs/' + jid + '/evidence',
                                    seconds=min(15, self.remaining(deadline)), limit=MAX_EVIDENCE * 2)
            if evidence.get('protocol') != PROTOCOL or evidence.get('job') != job:
                raise IsolationBlocked('远程证据任务身份不一致')
            return job, evidence
        except Exception:
            # Cancellation is best effort. Server expiry still bounds disconnected jobs.
            try:
                self.request('POST', '/v1/jobs/' + jid + '/cancel', seconds=2)
            except IsolationBlocked:
                pass
            raise

    @staticmethod
    def files(evidence):
        files, total = {}, 0
        for name, record in evidence.get('files', {}).items():
            if name not in EVIDENCE_NAMES or not isinstance(record, dict):
                raise IsolationBlocked('远程证据包含非批准文件')
            try:
                raw = base64.b64decode(record['base64'], validate=True)
                if hashlib.sha256(raw).hexdigest() != record['sha256']:
                    raise IsolationBlocked('远程证据哈希不一致')
            except (ValueError, TypeError, KeyError):
                raise IsolationBlocked('远程证据格式无效') from None
            total += len(raw)
            if total > MAX_EVIDENCE:
                raise IsolationBlocked('远程证据超过上限')
            files[name] = raw
        return files

    @staticmethod
    def save(folder, job, files):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        for name, raw in files.items():
            relative = name.removeprefix('test/') if name.startswith('test/') else 'remote-' + name
            target = folder / relative
            if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in (target, *target.parents)):
                raise IsolationBlocked('本地证据路径包含链接')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        (folder / 'remote-job.json').write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
        (folder / 'remote-manifest.json').write_text(json.dumps({n: hashlib.sha256(raw).hexdigest()
            for n, raw in files.items()}, indent=2), encoding='utf-8')

    def preflight(self, evidence_dir):
        try:
            job, evidence = self.submit('probe', 60)
            files = self.files(evidence)
            result = job['result']
            if (not isinstance(result, dict) or result.get('isolation_ready') is not True
                    or not re.fullmatch(r'sha256:[a-f0-9]{64}', str(result.get('image', '')))
                    or 'isolation/isolation.json' not in files
                    or json.loads(files['isolation/isolation.json']) != result):
                raise IsolationBlocked('远程隔离探针证据不完整')
            self.save(evidence_dir, job, files)
            if not re.fullmatch(r'[a-f0-9]{64}', str(job.get('server_build_sha256', ''))):
                raise IsolationBlocked('远程执行服务版本指纹缺失')
            self.ready, self.image = True, result['image']
            self.server_build_sha256 = job['server_build_sha256']
            result = {**result, 'execution_mode': self.mode, 'execution_endpoint': self.url,
                      'remote_job_id': job['job_id'], 'transport_mode': self.transport_mode}
        except (IsolationBlocked, ValueError, KeyError, TypeError) as exc:
            self.ready = False
            result = {'isolation_ready': False, 'reason': str(exc), 'execution_mode': self.mode}
        folder = Path(evidence_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'isolation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result

    def run(self, work, evidence_dir, *, phase, timeout, expected_version):
        if not self.ready:
            raise IsolationBlocked('必须先通过远程隔离探针')
        self.task.verify(work, candidate=True)
        if code_version(Path(work), self.task.profile['source_paths']) != expected_version:
            raise IsolationBlocked('候选版本已改变')
        source = (Path(work) / self.task.source).read_bytes().decode('utf-8')
        job, evidence = self.submit('test', min(60, timeout), phase=phase, source=source,
                                   code_version=expected_version, image=self.image)
        files = self.files(evidence)
        result = job['result']
        required = {'test/result.json', 'test/trusted-run/stdout.txt', 'test/trusted-run/stderr.txt'}
        if (not required <= files.keys() or not isinstance(result, dict)
                or json.loads(files['test/result.json']) != result or job.get('image') != self.image
                or job.get('server_build_sha256') != self.server_build_sha256
                or result.get('phase') != phase or result.get('project') != self.task.project
                or result.get('code_version') != expected_version
                or result.get('test_suite_version') != self.task.summary()['test_suite_version']
                or result.get('evidence_matches_current_version') is not True
                or code_version(Path(work), self.task.profile['source_paths']) != expected_version):
            raise IsolationBlocked('远程测试证据与当前候选不一致')
        for field in ('stdout_path', 'stderr_path', 'junit_path', 'structured_path'):
            if result.get(field) not in {n.removeprefix('test/') for n in EVIDENCE_NAMES if n.startswith('test/')}:
                raise IsolationBlocked('远程测试返回非法产物路径')
        self.save(evidence_dir, job, files)
        if result.get('verification_status') == 'passed':
            from c_runner.runner import parse_evidence
            folder = Path(evidence_dir)
            parsed = parse_evidence(folder / result['junit_path'], folder / result['structured_path'],
                                    self.task.profile, result.get('exit_code'))
            if parsed['verification_status'] != 'passed' or parsed['counts'] != result.get('counts'):
                raise IsolationBlocked('远程通过结论与原始测试报告不一致')
        result = {**result, 'execution_mode': self.mode, 'remote_job_id': job['job_id'],
                  'execution_endpoint': self.url, 'transport_mode': self.transport_mode}
        (Path(evidence_dir) / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result


def execution_runner(project, *, local_factory=None):
    if os.environ.get('CODE_FIX_EXECUTION_URL'):
        return RemoteRunner(project)
    from .split_runner import SplitDockerRunner
    return (local_factory or SplitDockerRunner)(project)
