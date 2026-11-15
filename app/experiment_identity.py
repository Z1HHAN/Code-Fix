"""Freeze comparison inputs before any candidate execution or model request."""
import hashlib
import os
import subprocess
import json
from pathlib import Path

from .model_profiles import load_profiles, resolved_base_url
from .config import ConfigError
from .live_model import ModelBlocked, positive_decimal
from .role_config import digest
from .role_overrides import effective_parameters

SOURCE_PATHS = ('app', 'c_runner', 'docker', 'configs', 'prompts', 'knowledge', 'ui',
                'tests', 'scripts', 'acceptance_tests', 'requirements.txt', 'requirements-lock.txt',
                'pytest.ini', 'pyproject.toml')


def code_identity(root, *, read_git=True):
    manifest = root / 'codefix-runtime-source.json'
    if read_git and manifest.is_file():
        frozen = json.loads(manifest.read_text(encoding='utf-8'))
        files = frozen.get('files', {})
        if not files or digest(files) != frozen.get('source_sha256') or not frozen.get('commit'):
            raise ConfigError('运行源码清单身份无效')
        for name, expected in files.items():
            path = root / name
            if (Path(name).is_absolute() or '..' in Path(name).parts
                    or not path.resolve().is_relative_to(root.resolve()) or path.is_symlink()
                    or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                raise ConfigError('运行源码与冻结清单不一致：' + name)
        return {'commit': frozen['commit'], 'source_sha256': digest(files),
                'dirty': None, 'verified_bundle': True}
    if not read_git:
        # Offline doubles must remain usable under the independent gate's
        # strict no-process boundary. They never acquire a claimed Git commit.
        files = {}
        for name in SOURCE_PATHS:
            source = root / name
            for path in source.rglob('*') if source.is_dir() else (source,):
                if path.is_file() and not any(p in {'__pycache__', '.pytest_cache'} for p in path.parts):
                    files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return {'commit': None, 'source_sha256': digest(files), 'dirty': None}
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              capture_output=True, timeout=15).stdout
    try:
        commit = git('rev-parse', 'HEAD').decode().strip()
        names = set(git('ls-files', '-z', '--', *SOURCE_PATHS).split(b'\0'))
        names.update(git('ls-files', '-z', '--others', '--exclude-standard', '--', *SOURCE_PATHS).split(b'\0'))
        files = {name.decode('utf-8'): hashlib.sha256((root / name.decode('utf-8')).read_bytes()).hexdigest()
                 for name in names if name and (root / name.decode('utf-8')).is_file()}
        return {'commit': commit, 'source_sha256': digest(files),
                'dirty': bool(git('status', '--porcelain', '--untracked-files=all', '--', *SOURCE_PATHS))}
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {'commit': None, 'source_sha256': None, 'dirty': None}


def freeze(root, task, definition, config, runner, model):
    from .split_runner import SplitDockerRunner
    from .remote_runner import RemoteRunner
    from .model_profiles import ModelRouter
    registry = load_profiles(root)
    profiles = registry['profiles']
    # Type and transport originate in the running process, not a submitted label.
    remote = type(runner) is RemoteRunner and runner.transport_mode == 'live'
    real = (type(runner) is SplitDockerRunner or remote) and (model is None or type(model) is ModelRouter and model.mode == 'live')
    def numeric_env(name):
        try:
            return str(positive_decimal(os.environ.get(name)))
        except ModelBlocked:
            return None
    bindings = {}
    for role, agent in config.agents.items():
        ref = config.snapshot['models'][agent['model_ref']].get('profile_ref')
        p = profiles.get(ref)
        try:
            endpoint = resolved_base_url(p) if p else None
        except (ConfigError, ModelBlocked):
            endpoint = None
        bindings[role] = {'profile_ref': ref,
                         'base_url': endpoint,
                         'currency': p.get('currency', 'USD') if p else None,
                         'parameters': effective_parameters(p, config.role_overrides.get(role, {})) if p else None,
                         'input_price': numeric_env(p['input_price_env']) if p else None,
                         'output_price': numeric_env(p['output_price_env']) if p else None,
                         'request_seconds': numeric_env(p['request_seconds_env']) if p else None}
        if p and p.get('personal_pricing'):
            bindings[role]['personal_pricing'] = p['personal_pricing']
    identity = {'code': code_identity(root, read_git=real), 'project': task['project'], 'description': task['description'],
                'task_limits': {k: task[k] for k in ('max_rounds', 'max_calls', 'total_seconds')},
                'approved_files': definition.profile['approved_files'],
                'test_suite_version': definition.summary()['test_suite_version'],
                'roles': config.snapshot, 'role_overrides': config.role_overrides,
                'prompts_sha256': {r: p[1] for r, p in config.prompts.items()},
                'profiles_sha256': digest(registry), 'bindings': bindings,
                'image': getattr(runner, 'image', None)}
    if definition.data.get('registered_contract'):
        identity['registered_contract'] = definition.data['registered_contract']
    if remote:
        identity['execution_endpoint'] = runner.url
        identity['execution_server_build_sha256'] = runner.server_build_sha256
    return {'schema_version': 1, 'strategy': task.get('strategy', 'dynamic'),
            'execution_kind': 'real' if real else 'offline', 'identity': identity,
            'identity_sha256': digest(identity)}
